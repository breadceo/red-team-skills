#!/usr/bin/env python3
"""트리아지 결과를 red-team 라운드의 `decisions.md` 에 넣는다.

usage:
  record_decisions.py --round-dir <dir> --items <items.json> [--dry-run]

items.json:
  [{"section": "반영",     "text": "- [Jinwoong, 5112608871] …\n  → … (커밋 abc1234)"},
   {"section": "후속 티켓", "text": "- [5113623641] … → TICKET-XXX"},
   {"section": "보류",     "text": "- [5114121080] … 사용자 결정 대기"}]

**`## 반영` / `## 후속 티켓` 절 안에 정확히 들어가야** red-team 의 `resume.py --next` 가
다음 라운드 컨텍스트로 이관한다. 절을 잘못 짚으면 이관이 조용히 누락되고, 리뷰어가 이미
처리한 지적을 다시 제기한다 — 그래서 손으로 붙이지 않고 스크립트로 넣는다.
"""
import argparse, json, re, sys
from pathlib import Path

SECTIONS = {
    "반영": "## 반영 — 다음 컨텍스트의 `이미 반영된 지적` 이 된다",
    "후속 티켓": "## 후속 티켓 — 다음 컨텍스트의 `스코프 밖` 이 된다",
    "보류": "## 보류 — 사용자가 아직 결정하지 않음",
}


def split_sections(text):
    parts = re.split(r"^(## .+)$", text, flags=re.M)
    out = [("", parts[0])] if parts[0].strip() else []
    for i in range(1, len(parts), 2):
        out.append((parts[i], parts[i + 1]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round-dir", required=True)
    ap.add_argument("--items", required=True)
    ap.add_argument("--label", default=None, help="소절 제목 (기본: 'PR 리뷰 코멘트 처리')")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rd = Path(a.round_dir)
    if not rd.is_dir():
        sys.exit(f"라운드 디렉토리가 없다: {rd}")
    items = json.loads(Path(a.items).read_text())
    bad = [i for i in items if i.get("section") not in SECTIONS]
    if bad:
        sys.exit(f"section 은 {list(SECTIONS)} 중 하나여야 한다: {bad}")

    label = a.label or "PR 리뷰 코멘트 처리"
    dec = rd / "decisions.md"
    text = dec.read_text() if dec.exists() else \
        f"# {rd.name} 처리 결과\n\n" + "".join(f"{h}\n\n\n" for h in SECTIONS.values())

    secs = split_sections(text)
    have = {h.split("—")[0].strip() for h, _ in secs if h}
    for key, head in SECTIONS.items():   # 없는 절은 만들어 둔다
        if head.split("—")[0].strip() not in have:
            secs.append((head, "\n\n"))

    added = 0
    for key, head in SECTIONS.items():
        chunk = [i["text"].rstrip() for i in items if i["section"] == key]
        if not chunk:
            continue
        for i, (h, b) in enumerate(secs):
            if h and h.split("—")[0].strip() == head.split("—")[0].strip():
                body = re.sub(r"^\s*[-*]?\s*\(?없[음다][^\n]*\)?\s*$", "", b, flags=re.M)
                secs[i] = (h, body.rstrip() + f"\n\n### {label}\n\n" + "\n".join(chunk) + "\n\n")
                added += len(chunk)
                break

    out = "".join((h + "\n" if h else "") + b for h, b in secs)
    if a.dry_run:
        print(f"[dry-run] {dec} 에 {added}건 추가\n{'─'*70}\n{out}")
        return
    dec.write_text(out)
    print(f"✅ {dec} · {added}건 추가 (소절: {label})")
    if any(i["section"] == "보류" for i in items):
        print("⚠ `보류` 에 항목을 넣었다 — 결정 전에는 red-team 이 다음 라운드로 넘어가지 않는다.")


if __name__ == "__main__":
    main()
