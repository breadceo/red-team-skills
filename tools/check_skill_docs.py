#!/usr/bin/env python3
"""스킬 문서의 progressive disclosure 규칙 검사 (issue #6).

검사 항목 — 하나라도 어기면 exit 1:
1. 각 스킬의 SKILL.md 추정 토큰 ≤ BUDGET (한글 1.1 tok/char + 그 외 0.27 근사)
2. references/·assets/ 의 모든 파일이 SKILL.md 본문에서 파일명으로 참조된다
   (가이드 "Mistake 4: Missing Resource References" 방지)
3. 스킬 루트에 SKILL.md 외 고아 .md 가 없다 — 두려면 references/ 로 내리고 참조한다
   (DESIGN.md 가 참조 0건으로 떠 있던 사고의 재발 방지)
4. 번들 스크립트 명령은 절대 skill 경로를 쓰고, 공백 가능한 경로 placeholder 를 quote 한다
5. 백틱으로 인용한 절 이름(`## …`)이 assets/ 템플릿이나 SKILL.md 에 실제로 존재한다
   — 판정 근거로 부르는 절이 템플릿에 없으면 그 근거는 영원히 비어 있다. 검사 4가 파일
   참조를 보는 것의 절 단위 대응이다(`## 판정 기준` 계열 6건이 무음으로 떠 있던 사고).

사용: python3 tools/check_skill_docs.py             (저장소 어디서든)
      python3 tools/check_skill_docs.py --selftest  (검사 5 판정 로직만)
"""
import re
import sys
from pathlib import Path

BUDGET = 5000  # 앤트로픽 skill-development 가이드의 최대치(<5k words)를 토큰 근사로 적용

# 검사 5 면제 — 우리 템플릿의 슬롯이 아닌 절 이름. 이유 없이 늘리지 않는다.
SECTION_ALLOWLIST = {
    "## Diff 스냅샷": "run_round.py 가 생성해 붙인다 — 사람이 채우는 템플릿 슬롯이 아니다",
    "## 리뷰 반영": "PR 본문의 섹션이다 — 우리 템플릿이 아니다",
    "## 중단 근거": "절 이름 파싱이 오인하는 예로 든 가상의 절 이름이다",
}

ROOT = Path(__file__).resolve().parent.parent


def est_tokens(text: str) -> int:
    hangul = sum(1 for c in text if "가" <= c <= "힣" or "ㄱ" <= c <= "ㅣ")
    return round(hangul * 1.1 + (len(text) - hangul) * 0.27)


def cited_sections(text: str) -> set:
    """백틱으로 감싼 절 이름 인용 — `## 스코프 밖`, `### 불변식` 등."""
    return set(re.findall(r"`(#{1,6} [^`\n]+?)\s*`", text))


def defined_sections(text: str) -> set:
    return set(re.findall(r"(?m)^(#{1,6} .+?)\s*$", text))


def _resolves(cited: str, defined: str) -> bool:
    """인용이 실제 제목의 앞부분만 적은 것은 해소로 본다
    (`## 판정 기준` ← `## 판정 기준 (Spec AC · Gherkin)`). 단 **낱말 경계에서만** —
    이어지는 글자가 공백·괄호가 아니면 다른 절이다(`### 불변식` 은 `### 불변식들` 이 아니다).
    경계를 안 걸면 절 개명이 무음으로 통과한다."""
    return defined == cited or (
        defined.startswith(cited) and defined[len(cited)] in " ("
    )


def unresolved_sections(cited: set, defined: set) -> list:
    """인용됐지만 정의되지 않은 절."""
    return sorted(
        h
        for h in cited
        if h not in SECTION_ALLOWLIST and not any(_resolves(h, d) for d in defined)
    )


def _selftest() -> int:
    defined = {"## 판정 기준 (Spec AC · Gherkin)", "### 불변식"}
    assert unresolved_sections({"### 불변식"}, defined) == []
    assert unresolved_sections({"## 판정 기준"}, defined) == [], "앞부분 인용은 해소다"
    assert unresolved_sections({"## 없는 절"}, defined) == ["## 없는 절"]
    assert unresolved_sections({"## 리뷰 반영"}, defined) == [], "allowlist 는 면제다"
    assert unresolved_sections({"## 불변식"}, defined) == ["## 불변식"], "레벨이 다르면 미해소다"
    assert unresolved_sections({"### 불변식"}, {"### 불변식들"}) == [
        "### 불변식"
    ], "개명은 무음으로 통과하지 않는다"
    assert cited_sections("근거는 `## 스코프 밖` 과 `### 기각한 대안` 이다") == {
        "## 스코프 밖",
        "### 기각한 대안",
    }
    assert cited_sections("`--gate plan` 은 절 이름이 아니다") == set()
    print("OK — 검사 5 판정 로직 selftest 통과")
    return 0


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

    docs = [ROOT / "README.md", *(f for skill in skills for f in skill.rglob("*.md"))]

    # 5. 인용된 절 이름이 실제로 정의돼 있는가 — 스킬 경계를 넘어 대조한다
    #    (pr-triage 3절 표가 red-team 템플릿의 절을 근거로 부른다)
    defined = set()
    for skill in skills:
        defined |= defined_sections((skill / "SKILL.md").read_text(encoding="utf-8"))
        for f in sorted((skill / "assets").glob("*.md")):
            defined |= defined_sections(f.read_text(encoding="utf-8"))
    for doc in docs:
        for h in unresolved_sections(
            cited_sections(doc.read_text(encoding="utf-8")), defined
        ):
            failures.append(
                f"{doc.relative_to(ROOT)} — 인용한 절 `{h}` 이 assets/ 템플릿·SKILL.md 에 없다. "
                f"절을 신설하거나, 우리 문서의 절이 아니면 SECTION_ALLOWLIST 에 이유와 함께 등재한다"
            )

    forbidden = {
        r"(?m)^(?:python3 )?(?:archive_runs|fetch_comments|record_decisions|post_replies|run_round|resume|report_usage)\.py\b": "번들 스크립트를 PATH 에서 찾는다",
        r"(?<![/\w])(?:archive_runs|fetch_comments|record_decisions|post_replies|run_round|resume|report_usage)\.py (?=--[a-z])": "본문의 실행 명령이 skill 경로를 생략한다",
        r"(?m)^python3 (?:scripts|evals)/": "번들 스크립트를 현재 디렉토리 기준으로 찾는다",
        r"python3 <(?:red-team|pr-triage)-skill>/(?:scripts|evals)/": "skill 스크립트 경로가 quote 되지 않았다",
        r"--(?:cwd|context|round-dir|items|replies) <[^>\n]+>": "공백 가능한 인자 경로가 quote 되지 않았다",
    }
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for pattern, message in forbidden.items():
            if re.search(pattern, text):
                failures.append(f"{doc.relative_to(ROOT)} — {message}")

    if failures:
        print("\n".join(f"FAIL: {m}" for m in failures), file=sys.stderr)
        return 1
    print("OK — 모든 스킬 문서가 progressive disclosure 규칙을 지킨다")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest() if "--selftest" in sys.argv else main())
