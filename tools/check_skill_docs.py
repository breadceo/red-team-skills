#!/usr/bin/env python3
"""스킬 문서의 progressive disclosure 규칙 검사 (issue #6).

검사 항목 — 하나라도 어기면 exit 1:
1. 각 스킬의 SKILL.md 추정 토큰 ≤ BUDGET (한글 1.1 tok/char + 그 외 0.27 근사)
2. references/·assets/ 의 모든 파일이 SKILL.md 본문에서 파일명으로 참조된다
   (가이드 "Mistake 4: Missing Resource References" 방지)
3. 스킬 루트에 SKILL.md 외 고아 .md 가 없다 — 두려면 references/ 로 내리고 참조한다
   (DESIGN.md 가 참조 0건으로 떠 있던 사고의 재발 방지)

사용: python3 tools/check_skill_docs.py   (저장소 어디서든)
"""
import sys
from pathlib import Path

BUDGET = 5000  # 앤트로픽 skill-development 가이드의 최대치(<5k words)를 토큰 근사로 적용

ROOT = Path(__file__).resolve().parent.parent


def est_tokens(text: str) -> int:
    hangul = sum(1 for c in text if "가" <= c <= "힣" or "ㄱ" <= c <= "ㅣ")
    return round(hangul * 1.1 + (len(text) - hangul) * 0.27)


def main() -> int:
    failures = []
    skills = sorted(p.parent for p in ROOT.glob("*/SKILL.md"))
    if not skills:
        print("SKILL.md 를 가진 스킬 디렉토리가 없다", file=sys.stderr)
        return 1

    for skill in skills:
        body = (skill / "SKILL.md").read_text(encoding="utf-8")
        tok = est_tokens(body)
        status = "OK" if tok <= BUDGET else "FAIL"
        print(f"[{status}] {skill.name}/SKILL.md ~{tok} tok (한도 {BUDGET})")
        if tok > BUDGET:
            failures.append(
                f"{skill.name}/SKILL.md 추정 {tok} tok > {BUDGET} — 규칙을 지우지 말고 references/ 로 옮긴다"
            )

        # 2. references/·assets/ 전 파일이 본문에서 참조되는가
        for sub in ("references", "assets"):
            for f in sorted((skill / sub).glob("*")):
                if f.name not in body:
                    failures.append(
                        f"{skill.name}/{sub}/{f.name} 이 SKILL.md 에서 참조되지 않는다 — "
                        f"「참고 문서」절에 '언제 읽는지'와 함께 나열한다"
                    )

        # 3. 스킬 루트 고아 .md
        for f in sorted(skill.glob("*.md")):
            if f.name != "SKILL.md":
                failures.append(
                    f"{skill.name}/{f.name} — 스킬 루트에 SKILL.md 외 .md 를 두지 않는다 (references/ 로)"
                )

    if failures:
        print("\n".join(f"FAIL: {m}" for m in failures), file=sys.stderr)
        return 1
    print("OK — 모든 스킬 문서가 progressive disclosure 규칙을 지킨다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
