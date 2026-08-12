"""fetch_comments 의 fp 파싱·diff 플래그·재게시 텔레메트리·상태 병합 검증.

gh 호출은 전부 fixture 로 대체한다 — 네트워크 없이 돈다. 실행: python3 test_fetch_comments.py
"""
import contextlib, io, json, os, shlex, sys, tempfile, threading
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="triage test "))
os.environ["RED_TEAM_HOME"] = str(TMP)          # LOG·REPOSTS 격리 — import 전에 잡아야 한다
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 설치 위치를 가정하지 않는다
import fetch_comments as fc

assert str(fc.REPOSTS).startswith(str(TMP)), "REPOSTS 가 RED_TEAM_HOME 파생식을 안 쓴다"
assert fc.RED_TEAM_CANDIDATES == [
    Path(fc.__file__).resolve().parents[2] / "red-team" / "scripts"
], "배포본 sibling 밖의 호스트 전용 red-team 을 탐색한다"

STATE_DIR = TMP / "runs" / "repo" / "branch"
fc.branch_dir = lambda cwd: STATE_DIR           # git 없이 상태 경로를 고정한다

# ── gh fixture ─────────────────────────────────────────────────────────────
# hermes 실물 형태: fp 값에 공백·쉼표, 마커 뒤 footer, 한 코멘트 마커 2개,
# 🤖 없는 사람 계정 재게시(#9588 5139840774 형).
FP_A = "auth null-flow, v2"     # 공백·쉼표 포함 fp
FP_B = "solo-fp"

INLINE = [
    {"id": 101, "user": {"login": "human-reviewer"}, "created_at": "2026-08-05T01:00:00Z",
     "body": f"결함: null 을 그대로 흘린다.\n\n<!-- hermes:fp={FP_A} -->\n<sub>by hermes</sub>",
     "path": "src/a.ts", "line": 10, "side": "RIGHT", "html_url": "http://c/101"},
    {"id": 102, "user": {"login": "human-reviewer"}, "created_at": "2026-08-05T02:00:00Z",
     "body": f"🤖 재게시.\n<!-- hermes:fp={FP_A} -->\n<!-- hermes:fp={FP_B} -->",
     "path": "src/gone.ts", "line": None, "side": "RIGHT", "html_url": "http://c/102"},
    {"id": 107, "user": {"login": "human-reviewer"}, "created_at": "2026-08-05T02:05:00Z",
     "body": "중복 마커.\n<!-- hermes:fp=dup-fp --><!-- hermes:fp=dup-fp -->",
     "path": "src/a.ts", "line": 10, "side": "RIGHT", "html_url": "http://c/107"},
    {"id": 104, "user": {"login": "reviewer2"}, "created_at": "2026-08-05T02:10:00Z",
     "body": "왼쪽 라인 지적", "path": "src/a.ts", "line": 5, "side": "LEFT",
     "html_url": "http://c/104"},
    {"id": 105, "user": {"login": "reviewer2"}, "created_at": "2026-08-05T02:20:00Z",
     "body": "바이너리 지적", "path": "bin/x.png", "line": 3, "side": "RIGHT",
     "html_url": "http://c/105"},
    {"id": 106, "user": {"login": "reviewer2"}, "created_at": "2026-08-05T02:30:00Z",
     "body": "hunk 밖 지적", "path": "src/a.ts", "line": 99, "side": "RIGHT",
     "html_url": "http://c/106"},
]
ISSUE = [
    # 내 회신이 봇 코멘트를 blockquote 로 인용 — 인용 속 마커는 파싱되면 안 된다
    {"id": 103, "user": {"login": "ethan"}, "created_at": "2026-08-05T03:00:00Z",
     "body": f"> <!-- hermes:fp={FP_A} -->\n\n지적이 맞습니다 — 고쳤습니다 (abc1234)",
     "html_url": "http://c/103"},
]
FILES = [
    # src/a.ts: new-side hunk 는 10~15줄 (start 10, count 6)
    {"filename": "src/a.ts", "patch": "@@ -1,4 +10,6 @@\n context"},
    {"filename": "bin/x.png"},                  # 바이너리 — patch 부재
]


def fake_gh(*args, check=True):
    if args[:2] == ("api", "user"):
        return "ethan\n"
    if args[0] == "api" and args[1].startswith("repos/"):
        return {"o/r": "303\n", "old/repo": "303\n",
                "other/repo": "404\n"}.get(args[1][len("repos/"):])
    raise AssertionError(f"예상 밖의 gh 호출: {args}")


FILES_CALLS = [0]
FILES_FAIL = [False]   # 토글 — True 면 files API 조회가 실패한 것처럼 None 을 돌려준다


def fake_gh_json(path, check=True):
    if path.endswith("/files"):
        FILES_CALLS[0] += 1
        if FILES_FAIL[0]:
            return None   # gh_json(check=False) 가 실패 시 돌려주는 값
        return FILES
    if "/pulls/5/comments" in path:
        return INLINE
    if "/issues/5/comments" in path:
        return ISSUE
    if path.endswith("/reviews") or path.endswith("/commits"):
        return []
    raise AssertionError(f"예상 밖의 gh_json 호출: {path}")


fc.gh, fc.gh_json = fake_gh, fake_gh_json
fc.gh_auth_ok = lambda: True

# repository metadata 일시 실패는 제한 재시도 뒤 복구한다.
repo_attempts = [0]
real_gh, real_sleep = fc.gh, fc.time.sleep
fc.gh = lambda *_args, **_kwargs: (repo_attempts.__setitem__(0, repo_attempts[0] + 1) \
    or ("303\n" if repo_attempts[0] == 3 else None))
fc.time.sleep = lambda _seconds: None
assert fc.repository_id("o/r") == 303 and repo_attempts[0] == 3
fc.gh, fc.time.sleep = real_gh, real_sleep
real_repository_id = fc.repository_id
fc.repository_id = lambda _repo: 101
assert fc.repository_status({"repo_id": 202}, "o/r")[1] == "different"
real_gh_auth_ok = fc.gh_auth_ok
fc.repository_id = lambda _repo: None
fc.gh_auth_ok = lambda: False
try:
    fc.repository_status({"repo_id": 202}, "o/r")
    raise AssertionError("metadata 인증 실패를 일반 소유권 실패로 처리했다")
except SystemExit as e:
    assert "gh auth login" in str(e), e
fc.gh_auth_ok = real_gh_auth_ok
fc.repository_id = real_repository_id

# secondary legacy slug 조회 중 인증이 만료되면 채택 가능 상태로 오인하지 않는다.
fc.repository_id = lambda repo: 101 if repo == "new/repo" else None
fc.gh_auth_ok = lambda: False
try:
    fc.repository_status({"repo": "old/repo"}, "new/repo")
    raise AssertionError("secondary metadata 인증 실패를 adoptable로 처리했다")
except SystemExit as e:
    assert "gh auth login" in str(e), e
fc.gh_auth_ok = real_gh_auth_ok
fc.repository_id = real_repository_id

# ID 없는 legacy slug를 확인할 수 없으면 기존 one-shot 채택 명령으로 복구를 안내한다.
fc.save_state("relative repo", 8, {"pr": 8, "repo": "gone/repo",
                                   "triaged": [], "notified": []})
fc.repository_id = lambda repo: 101 if repo == "new/repo" else None
try:
    fc.claim_repo("relative repo", 8, "new/repo")
    raise AssertionError("명시 채택이 필요한 legacy 상태를 묵시적으로 채택했다")
except SystemExit as e:
    message = str(e)
    assert "watch_comments.py" in message and "--adopt-repo" in message \
        and str(Path("relative repo").resolve()) in message, message
fc.repository_id = real_repository_id

# 서로 다른 두 최초 claim이 동시에 시작해도 lock+CAS로 하나만 소유권을 얻는다.
barrier = threading.Barrier(2)
local = threading.local()
def racing_repository_id(repo):
    if not getattr(local, "started", False):
        local.started = True
        barrier.wait()
    return {"first/repo": 501, "second/repo": 502}[repo]
fc.repository_id = racing_repository_id
claimed, rejected = [], []
def claim(repo):
    try:
        claimed.append((repo, fc.claim_repo(".", 6, repo)))
    except SystemExit:
        rejected.append(repo)
threads = [threading.Thread(target=claim, args=(repo,))
           for repo in ("first/repo", "second/repo")]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
fc.repository_id = real_repository_id
assert len(claimed) == len(rejected) == 1, (claimed, rejected)
race_state = fc.load_state(".", 6)
assert race_state["repo"] == claimed[0][0] and race_state["repo_id"] == claimed[0][1], race_state

# 같은 repo를 뒤이어 claim한 writer도 별도 세대다 — 앞 writer가 그 소유권을 rollback하면 안 된다.
fc.repository_id = lambda _repo: 601
claim_before = fc.load_state(".", 7)
fc.claim_repo(".", 7, "same/repo")
first_claim = fc.load_state(".", 7)
fc.claim_repo(".", 7, "same/repo")
assert not fc.rollback_repo_claim(".", 7, claim_before, first_claim), \
    "같은 repo의 후속 claim 세대를 구분하지 못해 앞 writer가 rollback했다"
fc.repository_id = real_repository_id


def run(*argv):
    sys.argv = ["fetch_comments.py", "--repo", "o/r", "--pr", "5", *argv]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fc.main()
    return buf.getvalue()


def fetch(*argv):
    out_path = TMP / "out.json"
    stdout = run("--out", str(out_path), *argv)
    data = json.loads(out_path.read_text())
    return stdout, {it["id"]: it for it in data["comments"]}


# ── 1) fp 파싱 단위 검증 ───────────────────────────────────────────────────
assert fc.fp_markers(f"<!-- hermes:fp={FP_A} -->") == [FP_A], "공백·쉼표 fp 유실"
assert fc.fp_markers(f"<!--hermes:fp={FP_B}-->") == [FP_B], "빡빡한 공백 변형 유실"
assert fc.fp_markers(f"산문.\n<!-- hermes:fp={FP_A} -->\n<sub>footer</sub>") == [FP_A], \
    "footer 후행이 있으면 못 잡는다 (비탐욕이면 --> 를 삼키지 않아야 한다)"
assert fc.fp_markers(f"<!-- hermes:fp={FP_A} -->\n<!-- hermes:fp={FP_B} -->") == [FP_A, FP_B], \
    "다중 마커 미추출"
assert fc.fp_markers(f"> <!-- hermes:fp={FP_A} -->\n답변") == [], "blockquote 인용을 마커로 오인"
# is_bot: fp 마커는 🤖 보다 강한 봇 서명 / 인용은 봇이 아니다
assert fc.is_bot(f"산문으로 시작.\n<!-- hermes:fp={FP_A} -->", "🤖"), "사람 계정 fp 코멘트 미판정"
assert fc.is_bot("🤖 자동 리뷰", "🤖") and fc.is_bot("<!-- x-pr-auto-review:v1 -->\n지적", "🤖")
assert not fc.is_bot(f"> <!-- hermes:fp={FP_A} -->\n\n지적이 맞습니다", "🤖"), \
    "내 회신의 인용이 봇으로 뒤집혔다"
# 코드펜스 안 마커도 인용과 같은 취급이다 — 제3자가 펜스로 봇 코멘트를 그대로 인용하면
# 마커가 있다는 이유만으로 그 사람이 봇으로 오판정되면 안 된다
fenced = f"설명입니다.\n```\n<!-- hermes:fp={FP_A} -->\n```\n본문 계속"
assert fc.fp_markers(fenced) == [], "코드펜스 안 마커가 걷어내지지 않았다"
assert not fc.is_bot(fenced, "🤖"), "펜스 인용만으로 봇 판정됐다"

# ── 2) 수집 — fps 스키마·seq·diff 플래그 ──────────────────────────────────
stdout, by_id = fetch()
c101, c102, c103 = by_id[101], by_id[102], by_id[103]
# 🤖 없는 사람 계정 hermes fp 코멘트가 봇·incoming 으로 파싱된다
assert c101["bot_marker"] and c101["is_incoming"], "사람 계정 fp 코멘트가 리뷰로 안 잡혔다"
assert [e["fp"] for e in c101["fps"]] == [FP_A]
e = c101["fps"][0]
assert e["fp_seq"] == 1 and e["fp_first_id"] == 101 and not e["fp_replied"] \
    and e["fp_reply_url"] is None
# 한 코멘트 마커 2개 — fp 별 seq 독립
assert {x["fp"]: x["fp_seq"] for x in c102["fps"]} == {FP_A: 2, FP_B: 1}, "fp 별 seq 독립 실패"
assert {x["fp"]: x["fp_first_id"] for x in c102["fps"]} == {FP_A: 101, FP_B: 102}
# 같은 코멘트 안의 동일 마커 2회 — 그 코멘트에서는 1회 등장으로 세고 fps 에도 1건만 남는다
c107 = by_id[107]
assert {x["fp"]: x["fp_seq"] for x in c107["fps"]} == {"dup-fp": 1}, \
    "같은 코멘트 안의 중복 마커가 seq 를 2번 올렸다"
assert len(c107["fps"]) == 1, "중복 마커가 dedup 되지 않아 fps 에 2번 들어갔다"
# 내 회신은 파싱하지 않는다 (인용 마커 제외)
assert c103["is_my_reply"] and c103["fps"] == [], "내 회신 인용이 fps 로 파싱됐다"
# diff 플래그 — inline 한정, null 규칙 (line null / side LEFT / patch 부재)
assert c101["in_diff"] is True and c101["line_in_hunk"] is True
assert c102["in_diff"] is False and c102["line_in_hunk"] is None, "line=null 이 null 이 아니다"
assert by_id[104]["line_in_hunk"] is None, "side=LEFT 가 null 이 아니다"
assert by_id[105]["in_diff"] is True and by_id[105]["line_in_hunk"] is None, "patch 부재가 null 이 아니다"
assert by_id[106]["line_in_hunk"] is False, "hunk 밖 라인이 False 가 아니다"
assert c103["in_diff"] is None and c103["line_in_hunk"] is None, "top-level 에 플래그가 계산됐다"
assert FILES_CALLS[0] == 1, f"files API 가 {FILES_CALLS[0]}회 호출됐다 — 1회만 불러야 한다"
# 목록에 fp 회차·diff 플래그 표시
assert "↻2" in stdout and "∉diff" in stdout

# ── 3) --show-files ────────────────────────────────────────────────────────
stdout = run("--show-files")
assert "변경 파일 2개" in stdout and "src/a.ts" in stdout and "bin/x.png" in stdout

# ── 4) --new-only 필터 **전** fp_seq 계산 ─────────────────────────────────
run("--mark-triaged", "101")
st = fc.load_state(".", 5)
assert st["repo"] == "o/r" and st["repo_id"] == 303, st
try:
    fc.merge_state(".", 5, {"repo": "other/repo", "triaged": [999]})
    raise AssertionError("다른 repository ID로 공유 상태를 변경했다")
except SystemExit:
    pass
st = fc.load_state(".", 5)
assert st["repo"] == "o/r" and st["repo_id"] == 303 and 999 not in st["triaged"], st
stdout, by_id = fetch("--new-only")
assert 101 not in by_id, "triaged 코멘트가 목록에 남았다"
assert {x["fp"]: x["fp_seq"] for x in by_id[102]["fps"]}[FP_A] == 2, \
    "필터 후에 seq 를 세었다 — 처리된 1회차가 빠져 회차가 줄었다"

# ── 5) 재게시 텔레메트리 — 회신 이후분만, dedup, crossing ─────────────────
fc.merge_state(".", 5, {"fp_replies": {FP_A: {"reply_id": 103, "reply_url": "http://c/103"}}})
# 내 회신(03:00) **이후** 재게시 1건 추가. 101·102 는 회신 전이라 재게시가 아니다.
INLINE.append({"id": 108, "user": {"login": "human-reviewer"},
               "created_at": "2026-08-05T04:00:00Z",
               "body": f"재게시 산문.\n<!-- hermes:fp={FP_A} -->",
               "path": "src/a.ts", "line": 10, "side": "RIGHT", "html_url": "http://c/108"})
# crossing 검증을 위해 이전 누적 9건을 깔아둔다 → 이번 1건으로 10건 임계를 '통과'한다
fc.REPOSTS.parent.mkdir(parents=True, exist_ok=True)
fc.REPOSTS.write_text("".join(json.dumps({"fp": f"seed-{i}"}) + "\n" for i in range(9)))
stdout, by_id = fetch()
rows = [json.loads(l) for l in fc.REPOSTS.read_text().splitlines() if l.strip()]
mine = [r for r in rows if r.get("pr") == 5]
assert len(mine) == 1 and mine[0]["fp"] == FP_A and mine[0]["comment_id"] == 108 \
    and mine[0]["prior_reply"] == "http://c/103", f"재게시 기록이 틀렸다: {mine}"
assert len(rows) == 10, "회신 이전 코멘트(101·102)까지 재게시로 기록됐다"
e = {x["fp"]: x for x in by_id[108]["fps"]}[FP_A]
assert e["fp_replied"] and e["fp_reply_url"] == "http://c/103" and e["fp_seq"] == 3
assert "봇 종결 실패 누적 10건" in stdout, "crossing(이전<t<=이후) 안내가 없다"
# 상태 커서 유실 뒤 rename돼도 append log의 repository ID로 같은 사건을 다시 쓰지 않는다.
all_rows = [json.loads(l) for l in fc.REPOSTS.read_text().splitlines() if l.strip()]
for row in all_rows:
    if row.get("comment_id") == 108:
        row["repo"] = "old/repo"
all_rows.append({"repo": "unrelated/repo", "pr": 999, "fp": "other", "comment_id": 1})
fc.REPOSTS.write_text("".join(json.dumps(row) + "\n" for row in all_rows))
cursorless = fc.load_state(".", 5)
cursorless.pop("repost_logged", None)
fc.save_state(".", 5, cursorless)
real_repository_id = fc.repository_id
def no_unrelated_lookup(repo):
    assert repo != "unrelated/repo", "후보와 무관한 legacy row metadata를 조회했다"
    return real_repository_id(repo)
fc.repository_id = no_unrelated_lookup
stdout, _ = fetch()
fc.repository_id = real_repository_id
rows = [json.loads(l) for l in fc.REPOSTS.read_text().splitlines() if l.strip()]
assert len(rows) == 11, "repost dedup 실패 — fetch 마다 다시 쌓인다"
assert "봇 종결 실패" not in stdout, "임계 안내가 반복된다 (스팸)"
assert f"{FP_A}:108" in fc.load_state(".", 5).get("repost_logged", []), \
    "append log에서 복구한 cursor를 상태에 backfill하지 않았다"
# 기존 repo_id 없는 로그도 old slug가 같은 ID로 해석되면 rename 중복을 막는다.
for row in rows:
    if row.get("comment_id") == 108:
        row.pop("repo_id", None)
fc.REPOSTS.write_text("".join(json.dumps(row) + "\n" for row in rows))
cursorless = fc.load_state(".", 5)
cursorless.pop("repost_logged", None)
fc.save_state(".", 5, cursorless)
real_repository_id = fc.repository_id
fc.repository_id = lambda repo: None if repo == "old/repo" else real_repository_id(repo)
fc.gh_auth_ok = lambda: False
try:
    fetch()
    raise AssertionError("legacy repost metadata 인증 실패를 일반 소유권 실패로 처리했다")
except SystemExit as e:
    assert "gh auth login" in str(e), e
fc.gh_auth_ok = lambda: True
try:
    fetch()
    raise AssertionError("legacy repo 소유권 확인 실패에도 재게시 로그를 진행했다")
except SystemExit as e:
    assert "legacy 재게시 로그" in str(e), e
fc.repository_id = real_repository_id
fc.gh_auth_ok = real_gh_auth_ok
assert len([l for l in fc.REPOSTS.read_text().splitlines() if l.strip()]) == 11
stdout, _ = fetch()
rows = [json.loads(l) for l in fc.REPOSTS.read_text().splitlines() if l.strip()]
assert len(rows) == 11 and "봇 종결 실패" not in stdout

# ── 6) merge_state — 재읽기 병합: 리스트 합집합 · fp_replies keep-first ───
st = fc.merge_state(".", 5, {"fp_replies": {FP_A: {"reply_id": 999, "reply_url": "u-덮기시도"},
                                            "new-fp": {"reply_id": 7, "reply_url": "u7"}},
                             "repost_logged": ["z:1"]})
assert st["fp_replies"][FP_A]["reply_url"] == "http://c/103", \
    "keep-first 실패 — 디스크 기존 앵커가 덮였다"
assert st["fp_replies"]["new-fp"]["reply_url"] == "u7"
assert "z:1" in st["repost_logged"], "리스트 합집합 실패"

# metadata 조회 중 병행 watcher가 저장한 최신 커서를 최종 merge가 다시 읽어 보존한다.
concurrent = fc.load_state(".", 5)
concurrent["notified"] = []
fc.save_state(".", 5, concurrent)
real_repository_id = fc.repository_id
def repository_id_with_concurrent_write(repo):
    latest = fc.load_state(".", 5)
    latest["notified"] = [77]
    fc.save_state(".", 5, latest)
    return real_repository_id(repo)
fc.repository_id = repository_id_with_concurrent_write
st = fc.merge_state(".", 5, {"repo": "o/r", "triaged": [998]})
fc.repository_id = real_repository_id
assert 77 in st["notified"] and 998 in st["triaged"], st

# 같은 repository ID의 rename 경합은 허용하고 디스크의 최신 slug를 보존한다.
renamed = fc.load_state(".", 5)
renamed.update({"repo": "renamed/repo", "repo_id": 303})
fc.save_state(".", 5, renamed)
st = fc.merge_state(".", 5, {"repo": "o/r", "repo_id": 303, "triaged": [997]})
assert st["repo"] == "renamed/repo" and 997 in st["triaged"], st

# repo_id를 생략한 일반 caller도 redirect의 canonical slug를 써서 stale slug로 되돌리지 않는다.
real_repository_name = fc.repository_name
fc.repository_name = lambda _repo: "renamed/repo"
st = fc.merge_state(".", 5, {"repo": "o/r", "triaged": [996]})
fc.repository_name = real_repository_name
assert st["repo"] == "renamed/repo" and 996 in st["triaged"], st

# append-log dedup read→append는 전역 lock 안에서 재확인해 동시 writer도 한 행만 쓴다.
real_reposts = fc.REPOSTS
fc.REPOSTS = TMP / "race-reposts.jsonl"
race_candidate = [("race-fp:1", {"fp": "race-fp", "comment_id": 1,
                                  "repo": "o/r", "pr": 77, "at": "now"})]
race_results = []
threads = [threading.Thread(target=lambda: race_results.append(
    fc.record_reposts(77, "o/r", 303, race_candidate, set()))) for _ in range(2)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
assert sum(len(result[0]) for result in race_results) == 1, race_results
assert len(fc.REPOSTS.read_text().splitlines()) == 1
fc.REPOSTS = real_reposts

# ── 7) files API 실패 — 죽지 않고 플래그 전부 null 로 강등, 경고 1줄 ───────────
FILES_FAIL[0] = True
stdout, by_id = fetch()
assert "변경 파일 목록 조회 실패" in stdout, "files API 실패 경고가 안 보인다"
assert by_id[101]["in_diff"] is None and by_id[101]["line_in_hunk"] is None, \
    "files API 실패 후에도 diff 플래그가 계산됐다"
stdout = run("--show-files")
assert "조회 실패" in stdout, "--show-files 가 조회 실패를 표기하지 않았다"
FILES_FAIL[0] = False

# ── 8) 출력한 측정 명령은 공백 경로에서도 그대로 복사 실행된다 ────────────
old_total = fc.REVIEW_AT_TOTAL
fc.REVIEW_AT_TOTAL = (1,)
stdout = run("--log-classification", '[{"predicted": [], "confirmed": []}]')
fc.REVIEW_AT_TOTAL = old_total
cmd = stdout.split("묻는다:\n", 1)[1].strip()
argv = shlex.split(cmd)
assert argv[1] == str(Path(fc.__file__).resolve().parent.parent / "evals" / "score.py"), argv
assert argv[argv.index("--log") + 1] == str(fc.LOG), argv

print("PASS — fp 파싱(공백·쉼표·footer·다중·인용 제외·펜스), 같은 코멘트 중복 마커 dedup, "
      "사람 계정 fp 봇 판정, diff 플래그 null 규칙, files 1회 호출·API 실패 강등, "
      "필터 전 fp_seq, 재게시 기록·dedup·crossing, merge_state keep-first 모두 정상")
