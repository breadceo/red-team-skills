#!/usr/bin/env python3
"""8절 게시(`post_replies.py --confirm`) 직후, 이번 라운드의 real-defect 1건을 교훈 카드 초안으로 뽑는다.

입력: pr-triage-log.jsonl 의 row(confirmed 에 real-defect, corrected == false, 같은 PR, 해당 id 가
라운드 decisions.md `## 반영` 에 적힌 것) + 그 `## 반영` 줄 + 수정 커밋 해시(인자).
출력: stdout 에 카드 JSON 1장(knowledge_put 인자 그대로). exit 0.
코드로 거부 — 지시문만으로는 안 지켜진다(written-procedure 교훈):
  exit 1 후보 없음 / exit 2 sources[0] 형식 위반 / exit 3 하루 상한(3장) / exit 4 중복(점수 ≥ 0.6, duplicate_of 출력).
모든 결과를 lesson-drafts.jsonl 에 한 줄 append 한다(승인율 측정 재료). --dry-run 이면 append 하지 않는다.

python3 extract_lesson_candidate.py --repo owner/name --pr N --round-dir <라운드 경로> --commit <sha> [--dry-run]
"""
import argparse, json, os, re, subprocess, sys
from datetime import date, datetime, timezone
from pathlib import Path

RT_HOME = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team"))
LOG = RT_HOME / "pr-triage-log.jsonl"
DRAFTS = RT_HOME / "lesson-drafts.jsonl"
HIPO = Path(os.environ.get("HIPOCAMPUS_DIR", Path.home() / "Documents/git/hipocampus"))
LESSONS_DIR = HIPO / "_memory" / "lessons"
LESSONS_PY = HIPO / "scripts" / "lessons.py"
SOURCE_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/pull/\d+#discussion_r\d+$")
DAILY_CAP, DUP_THRESHOLD = 3, 0.6
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,}|[가-힣]{2,}")


def pick(repo: str, pr: int, round_dir: Path) -> tuple[dict, list[str]]:
    """이번 라운드의 real-defect row 1건 + `## 반영` 줄. 같은 id 는 마지막 row 가 진실(교정 row 가 덮는다)."""
    rows: dict = {}
    for line in LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []:
        if line.strip():
            r = json.loads(line)
            if r.get("repo") == repo and r.get("pr") == pr:
                rows[r["id"]] = r
    text = (round_dir / "decisions.md").read_text(encoding="utf-8") if (round_dir / "decisions.md").exists() else ""
    m = re.search(r"^## 반영.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    applied = [ln for ln in (m.group(1) if m else "").splitlines() if ln.startswith("- ")]
    cands = [r for r in rows.values() if "real-defect" in r.get("confirmed", []) and not r.get("corrected")
             and any(str(r["id"]) in ln for ln in applied)]
    if not cands:
        sys.exit("후보 없음 — real-defect·corrected=false·`## 반영` 에 적힌 id 가 없다")
    row = max(cands, key=lambda r: r.get("at", ""))
    return row, [ln for ln in applied if str(row["id"]) in ln]


def build(row: dict, fix_lines: list[str], commit: str, round_dir: Path) -> dict:
    note = row.get("note", "").strip()
    repo_name = row["repo"].split("/")[-1]
    latin = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9-]+", note)]
    slug = "-".join(latin)[:40].strip("-") or f"c{row['id']}"  # ponytail: 한글 note 는 슬러그가 안 나온다 → comment id
    words = list(dict.fromkeys(TOKEN_RE.findall(note)))
    return {
        "type": "anti-pattern",
        "id": f"{repo_name}-pr{row['pr']}-{slug}",
        "title": note,  # ponytail: "<무엇을> 하면 <무슨 결함>" 다듬기는 사람이 넣는다/버린다 로 판단 — 수정 안 받음
        "summary": " / ".join([note, *fix_lines, f"커밋 {commit}"]),
        "keywords": words[:5],
        "sources": [f"https://github.com/{row['repo']}/pull/{row['pr']}#discussion_r{row['id']}",
                    str(round_dir / "decisions.md")],
    }


def search_top(title: str) -> tuple[float, str]:
    """lessons.py search 상위 1건 (점수, id). 로직 복제 금지 — subprocess 로 부른다."""
    out = subprocess.run([sys.executable, str(LESSONS_PY), "search", title], capture_output=True, text=True).stdout
    m = re.match(r"^([\d.]+) \[[^\]]+\] (\S+)", out.strip().splitlines()[0]) if out.strip() else None
    return (float(m.group(1)), m.group(2)) if m else (0.0, "")


def gate(card: dict) -> tuple[str, int]:
    """(outcome, exit code). draft 면 0."""
    if not SOURCE_RE.match(card["sources"][0]):
        return "invalid", 2
    today = date.today().isoformat()
    n = sum(1 for p in LESSONS_DIR.glob("*.md")
            if (t := p.read_text(encoding="utf-8")) and re.search(rf"^created_at: {today}$", t, re.M)
            and re.search(r"^sources:.*discussion_r", t, re.M))
    if n >= DAILY_CAP:
        return "cap", 3
    score, dup = search_top(card["title"])
    if score >= DUP_THRESHOLD:
        card.clear(); card["duplicate_of"] = dup
        return "dup", 4
    return "draft", 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True); ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--round-dir", required=True, type=Path); ap.add_argument("--commit", required=True)
    ap.add_argument("--dry-run", action="store_true", help="lesson-drafts.jsonl 에 기록하지 않는다")
    a = ap.parse_args()
    row, fix_lines = pick(a.repo, a.pr, a.round_dir)
    card = build(row, fix_lines, a.commit, a.round_dir)
    cid = card["id"]
    outcome, code = gate(card)
    if not a.dry_run:
        DRAFTS.parent.mkdir(parents=True, exist_ok=True)
        with DRAFTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pr": a.pr,
                                "id": cid, "outcome": outcome}, ensure_ascii=False) + "\n")
    print(json.dumps(card, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
