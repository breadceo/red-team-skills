"""extract_lesson_candidate 의 초안·거부 4분기 검증.

lessons.py search 는 monkeypatch 로 대체한다 — hipocampus 체크아웃 없이 돈다.
실행: HOME=$(mktemp -d) python3 test_extract_lesson_candidate.py
"""
import contextlib, io, json, os, sys, tempfile
from datetime import date
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="lesson-test-"))
os.environ["RED_TEAM_HOME"] = str(TMP / "rt")       # 상태 격리 — import 전에 잡아야 한다
os.environ["HIPOCAMPUS_DIR"] = str(TMP / "hipo")
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 설치 위치를 가정하지 않는다
import extract_lesson_candidate as elc

elc.LESSONS_DIR.mkdir(parents=True)
elc.LOG.parent.mkdir(parents=True)
ROUND = TMP / "runs2" / "o__r" / "b" / "code-1"
ROUND.mkdir(parents=True)
ROW = {"id": 111, "predicted": ["real-defect"], "confirmed": ["real-defect"], "corrected": False,
       "note": "inline-dotenv name 을 process.env 에서 읽으면 빈 값", "repo": "o/r", "pr": 7, "at": "2026-10-30T02:12:41Z"}
elc.LOG.write_text("\n".join(json.dumps(r) for r in [
    ROW, {**ROW, "id": 222, "note": "스코프 밖", "confirmed": ["out-of-scope"]},
    {**ROW, "id": 333, "note": "교정된 건", "corrected": True}, {**ROW, "id": 444, "pr": 8}]) + "\n")
(ROUND / "decisions.md").write_text("## 반영\n\n- [rev, comment 111] 빈 값 → getValue 전에 읽도록 (커밋 abc1234)\n"
                                   "- [rev, comment 333] 교정됨 → 유지\n\n## 후속 티켓\n\n- [rev, comment 222] 범위 밖\n")
elc.search_top = lambda title: (0.3, "unrelated")


def run(*extra):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit) as _:
        try:
            code = elc.main()
        except SystemExit as e:
            code = e.code
    return code, json.loads(out.getvalue() or "{}")


def drafts():
    return [json.loads(l) for l in elc.DRAFTS.read_text().splitlines()] if elc.DRAFTS.exists() else []


sys.argv = ["x", "--repo", "o/r", "--pr", "7", "--round-dir", str(ROUND), "--commit", "abc1234"]

# ── 1) 정상 초안 — 반영 줄에 적힌 real-defect 111 만 뽑힌다(222 분류 다름·333 교정·444 다른 PR 제외) ─
code, card = run()
assert code == 0, code
assert card["id"] == "r-pr7-inline-dotenv-name-process-env" and card["type"] == "anti-pattern", card
assert card["sources"] == ["https://github.com/o/r/pull/7#discussion_r111", str(ROUND / "decisions.md")]
assert "getValue 전에" in card["summary"] and "커밋 abc1234" in card["summary"], card["summary"]
assert 3 <= len(card["keywords"]) <= 5 and "process.env" in card["keywords"], card["keywords"]
assert drafts()[-1]["outcome"] == "draft" and drafts()[-1]["pr"] == 7

# ── 2) sources 형식 거부 — repo 에 공백 등 URL 이 깨지면 exit 2 ─
elc.LOG.write_text(json.dumps({**ROW, "repo": "o/r x"}) + "\n")
sys.argv[2] = "o/r x"
code, card = run()
assert code == 2 and drafts()[-1]["outcome"] == "invalid", (code, drafts()[-1])
elc.LOG.write_text(json.dumps(ROW) + "\n"); sys.argv[2] = "o/r"

# ── 3) 하루 상한 — 오늘 만든 discussion_r 카드 3장이면 exit 3(옛 날짜·discussion_r 없는 카드는 안 센다) ─
today = date.today().isoformat()
for i in range(3):
    (elc.LESSONS_DIR / f"today-{i}.md").write_text(
        f"---\nid: today-{i}\ncreated_at: {today}\nsources: [\"https://github.com/o/r/pull/1#discussion_r{i}\"]\n---\n")
(elc.LESSONS_DIR / "old.md").write_text("---\nid: old\ncreated_at: 2026-01-01\nsources: [\"x#discussion_r9\"]\n---\n")
(elc.LESSONS_DIR / "manual.md").write_text(f"---\nid: manual\ncreated_at: {today}\nsources: [\"https://x\"]\n---\n")
code, _ = run()
assert code == 3 and drafts()[-1]["outcome"] == "cap", code
(elc.LESSONS_DIR / "today-2.md").unlink()
code, _ = run()
assert code == 0, "2장이면 통과해야 한다"

# ── 4) 중복 — search 상위 1건 점수 ≥ 0.6 이면 duplicate_of 만 출력하고 exit 4 ─
elc.search_top = lambda title: (0.6, "existing-card")
code, card = run()
assert code == 4 and card == {"duplicate_of": "existing-card"} and drafts()[-1]["outcome"] == "dup", (code, card)

# ── 5) --dry-run 은 drafts 에 남기지 않는다 ─
n = len(drafts()); sys.argv.append("--dry-run")
run()
assert len(drafts()) == n

print("ok — extract_lesson_candidate 5 checks")
