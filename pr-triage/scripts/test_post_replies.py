"""post_replies 의 게시 전 검증 게이트·리액션·일괄 fp_replies 기록 검증.

gh 호출은 monkeypatch 로 대체한다 — 네트워크 없이 돈다. 실행: python3 test_post_replies.py
"""
import builtins, contextlib, io, json, os, sys, tempfile, types
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="post-test-"))
os.environ["RED_TEAM_HOME"] = str(TMP)          # 상태 격리 — import 전에 잡아야 한다
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 설치 위치를 가정하지 않는다
import fetch_comments as fc
import post_replies as ppr

fc.repository_id = lambda repo: {"o/r": 303, "other/repo": 404}.get(repo)

STATE_DIR = TMP / "runs" / "repo" / "branch"
FP = "auth null-flow, v2"


def rejected(items, why):
    try:
        ppr.validate(items, "🤖")
    except SystemExit as e:
        return str(e)
    raise AssertionError(f"거부해야 한다: {why}")


OK_ITEM = {"target_id": 1, "source": "inline", "body": "지적이 맞습니다 — 고쳤습니다 (abc1234)"}

# ── 1) body 가 is_bot 판정됨 → 거부 (is_bot 3분기 전부 — fp 마커만이 아니다) ─
msg = rejected([{**OK_ITEM, "body": f"반박문.\n<!-- hermes:fp={FP} -->"}], "raw fp 마커 인용")
assert "봇 판정" in msg
rejected([{**OK_ITEM, "body": "🤖 자동 확인 결과 문제없음"}], "선두 🤖")
rejected([{**OK_ITEM, "body": "<!-- zigbang-pr-auto-review:v3 -->\n지적이 맞습니다"}],
         "선두 auto-review 서명 주석")
# blockquote 인용은 정당하다 — fp_markers/is_bot 이 인용을 걷어내므로 게이트도 통과한다
ppr.validate([{**OK_ITEM, "body": f"> <!-- hermes:fp={FP} -->\n> 🤖 원문 인용\n\n지적이 맞습니다"}],
             "🤖")

# ── 1b) target_id 게이트 — inline 회신·reaction 은 target_id 없이 게시 대상을 못 정한다 ─
no_target = {k: v for k, v in OK_ITEM.items() if k != "target_id"}
msg = rejected([no_target], "inline 인데 target_id 없음")
assert "target_id" in msg, "inline target_id 게이트 메시지가 아니다"
msg = rejected([{**no_target, "source": "top-level", "fps": [FP], "reaction": "-1"}],
               "reaction 인데 target_id 없음")
assert "target_id" in msg, "reaction target_id 게이트 메시지가 아니다"

# ── 2) reaction 게이트 ─────────────────────────────────────────────────────
msg = rejected([{**OK_ITEM, "reaction": "-1"}], "fps 없는 reaction")
assert "fps" in msg, "필수 필드 게이트 메시지가 아니다"
msg = rejected([{**OK_ITEM, "source": "review", "fps": [FP], "reaction": "-1"}],
               "review + reaction")
assert "404" in msg
rejected([{**OK_ITEM, "fps": [FP], "reaction": "0"}], "허용 밖 reaction 값")
ppr.validate([{**OK_ITEM, "fps": [FP], "reaction": "-1"}], "🤖")     # 정상 조합
ppr.validate([{**OK_ITEM, "fps": [FP], "reaction": "+1"}], "🤖")

# ── 3) fps 원소 타입 — 객체 배열 오복사·빈 문자열·비리스트 거부 ────────────
msg = rejected([{**OK_ITEM, "fps": [{"fp": FP, "fp_seq": 1}]}], "fetch 객체 배열 통복사")
assert "문자열" in msg, "타입 게이트 메시지가 아니다"
rejected([{**OK_ITEM, "fps": [""]}], "빈 문자열 fp")
rejected([{**OK_ITEM, "fps": FP}], "리스트가 아닌 fps")
# fps: null 은 "다루는 fp 없음"의 명시적 표현이다 — 빈 리스트로 취급해 통과시킨다
ppr.validate([{**OK_ITEM, "fps": None}], "🤖")

# ── 4) parity — fetch 의 fp_markers 가 마커로 파싱하는 body 는 게이트가 반드시 거부 ─
for body in (f"<!-- hermes:fp={FP} -->",
             f"산문 시작.\n<!-- hermes:fp={FP} -->\n<sub>footer</sub>",
             f"<!--hermes:fp=tight-->",
             f"<!-- hermes:fp={FP} -->\n<!-- hermes:fp=second -->"):
    assert fc.fp_markers(body), f"전제 실패 — fetch 가 마커로 안 잡는다: {body!r}"
    rejected([{**OK_ITEM, "body": body}], f"fp 마커 본문: {body!r}")
# 역방향 — fetch 가 안 잡는 인용 본문은 게이트도 fp 사유로 막지 않는다 (동일 헬퍼 기반)
quoted = f"> <!-- hermes:fp={FP} -->\n지적이 맞습니다"
assert not fc.fp_markers(quoted)
ppr.validate([{**OK_ITEM, "body": quoted}], "🤖")

# ── 5) 게시 경로 — subprocess 를 가짜로 갈아끼운다 ─────────────────────────
CALLS = []


def fake_run(cmd, capture_output=True, text=True):
    CALLS.append(cmd)
    path = cmd[4]
    if path.endswith("/reactions"):
        if FAIL_REACTION[0]:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return types.SimpleNamespace(returncode=0, stdout="{}", stderr="")
    if CRASH_TARGET[0] is not None and path.endswith(f"/comments/{CRASH_TARGET[0]}/replies"):
        raise RuntimeError("boom-mid-loop")   # 게시 루프 중간의 미검증 예외를 흉내낸다
    if FAIL_POST[0]:
        return types.SimpleNamespace(returncode=1, stdout="", stderr="post-boom")
    if NONJSON_REPLY[0]:
        return types.SimpleNamespace(returncode=0, stdout="not-json", stderr="")   # id 없는 성공
    return types.SimpleNamespace(returncode=0, stderr="",
                                 stdout=json.dumps({"id": 991, "html_url": "http://r/991"}))


FAIL_REACTION = [False]
FAIL_POST = [False]
NONJSON_REPLY = [False]
CRASH_TARGET = [None]
ppr.subprocess.run = fake_run


def run_main(items, *argv):
    replies = TMP / "replies.json"
    replies.write_text(json.dumps(items, ensure_ascii=False))
    sys.argv = ["post_replies.py", "--repo", "o/r", "--pr", "5",
                "--replies", str(replies), *argv]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ppr.main()
    return buf.getvalue()


# 5a) dry-run — 리액션 예정 표시, gh 호출 0건, 상태 기록 없음
CALLS.clear()
out = run_main([{**OK_ITEM, "fps": [FP], "reaction": "-1"}])
assert "[dry-run] 리액션 -1 예정" in out, "dry-run 에 리액션 예정이 안 보인다"
assert "[dry-run] POST" in out and not CALLS, "dry-run 이 실제 호출을 만들었다"

# 5b) branch_dir 미발견 — 경고 후 상태 기록만 건너뛴다 (게시 도중 즉사 금지)
real_branch_dir = fc.branch_dir
fc.branch_dir = None
CALLS.clear()
out = run_main([{**OK_ITEM, "fps": [FP]}], "--confirm")
assert "✓ http://r/991" in out and "fp_replies 를 기록하지 못했다" in out, \
    "red-team 미발견에서 게시 성공 + 경고가 아니다"
fc.branch_dir = lambda cwd: STATE_DIR

# 5c) 일괄 fp_replies 기록 — 항목의 fps 전원, keep-first (디스크 기존 앵커 우선)
fc.merge_state(".", 5, {"fp_replies": {FP: {"reply_id": 1, "reply_url": "u-1회차앵커"}}})
FAIL_REACTION[0] = True     # 리액션 실패는 회신 성공과 별도 보고돼야 한다
real_repository_id = fc.repository_id
repository_id_calls = [0]
def counted_repository_id(repo):
    repository_id_calls[0] += 1
    return real_repository_id(repo)
fc.repository_id = counted_repository_id
out = run_main([{**OK_ITEM, "fps": [FP, "new-fp"], "reaction": "-1"}], "--confirm")
fc.repository_id = real_repository_id
assert repository_id_calls[0] == 1, "게시 전 검증 뒤 상태 기록에서 metadata를 다시 조회했다"
st = fc.load_state(".", 5)
assert st["fp_replies"][FP]["reply_url"] == "u-1회차앵커", "keep-first 실패 — 1회차 앵커가 덮였다"
assert st["fp_replies"]["new-fp"] == {"reply_id": 991, "reply_url": "http://r/991"}, \
    "fps 전원 기록 실패"
assert "✓ http://r/991" in out and "리액션 실패 1건" in out, "리액션 실패가 별도 보고되지 않았다"
assert [c for c in CALLS if c[4].endswith("/reactions")][0][4] == \
    "repos/o/r/pulls/comments/1/reactions", "inline 리액션 엔드포인트가 틀렸다"
FAIL_REACTION[0] = False

# 5d) top-level 리액션 엔드포인트는 issues/comments 다
CALLS.clear()
run_main([{"target_id": 2, "source": "top-level",
           "body": "지적이 맞습니다", "fps": ["x"], "reaction": "+1"}], "--confirm")
assert [c for c in CALLS if c[4].endswith("/reactions")][0][4] == \
    "repos/o/r/issues/comments/2/reactions", "issue 리액션 엔드포인트가 틀렸다"

# 5e) 회신 실패 시 리액션은 보류한다 — 1회차 계약은 "전문 반박 + 👎 동시"다
CALLS.clear()
owned = fc.load_state(".", 5)
unowned = dict(owned)
unowned.pop("repo", None)
unowned.pop("repo_id", None)
fc.save_state(".", 5, unowned)
FAIL_POST[0] = True
out = run_main([{**OK_ITEM, "fps": [FP], "reaction": "-1"}], "--confirm")
FAIL_POST[0] = False
assert "✗ 실패" in out, "회신 실패 메시지가 안 보인다"
assert not [c for c in CALLS if c[4].endswith("/reactions")], "회신 실패에도 리액션이 호출됐다"
failed_state = fc.load_state(".", 5)
assert "repo" not in failed_state and "repo_id" not in failed_state, \
    "실패한 첫 게시의 repository claim이 남아 올바른 repo 재시도를 막는다"
fc.save_state(".", 5, owned)

# claim 직후 첫 출력이 깨져도 try/finally가 이미 설치돼 있어 claim을 복원한다.
fc.save_state(".", 5, unowned)
real_print = getattr(ppr, "print", None)
print_calls = [0]
def broken_once(*args, **kwargs):
    print_calls[0] += 1
    if print_calls[0] == 1:
        raise BrokenPipeError("closed stdout")
    builtins.print(*args, **kwargs)
ppr.print = broken_once
try:
    run_main([{**OK_ITEM, "fps": [FP]}], "--confirm")
    raise AssertionError("첫 출력 실패가 전파되지 않았다")
except BrokenPipeError:
    pass
if real_print is None:
    del ppr.print
else:
    ppr.print = real_print
failed_state = fc.load_state(".", 5)
assert "repo" not in failed_state and "repo_id" not in failed_state, \
    "첫 출력 실패가 repository claim을 남겼다"
fc.save_state(".", 5, owned)

# claim 직후 병행 writer가 저장한 cursor는 post의 rollback snapshot에 섞여 삭제되면 안 된다.
fc.save_state(".", 5, unowned)
real_claim_repo = fc.claim_repo
def claim_then_writer(*args, **kwargs):
    result = real_claim_repo(*args, **kwargs)
    claimed_id = result[0] if isinstance(result, tuple) else result
    fc.merge_state(".", 5, {"notified": [777], "repo": "o/r", "repo_id": claimed_id})
    return result
fc.claim_repo = claim_then_writer
FAIL_POST[0] = True
run_main([{**OK_ITEM, "fps": [FP]}], "--confirm")
FAIL_POST[0] = False
fc.claim_repo = real_claim_repo
failed_state = fc.load_state(".", 5)
assert failed_state.get("notified") == [777], \
    "post rollback이 claim 이후 병행 writer의 cursor를 삭제했다"
fc.save_state(".", 5, owned)

# 5f) id 없는 성공(비-JSON 응답) — fp_replies 에 기록하지 않고 경고로 보고한다
NONJSON_REPLY[0] = True
out = run_main([{**OK_ITEM, "fps": ["id-missing-fp"]}], "--confirm")
NONJSON_REPLY[0] = False
assert "✓ 게시됨" in out and "id 없는 성공" in out, "id 없는 성공 경고가 안 보인다"
st = fc.load_state(".", 5)
assert "id-missing-fp" not in st.get("fp_replies", {}), "id 없는 성공이 그대로 기록됐다"

# 5g) 상태 파일의 repo 와 --repo 가 다르면 merge_state 전에 거부한다
fc.merge_state(".", 5, {"repo": "o/r"})   # 대조 기준을 명시적으로 맞춰 테스트 순서 독립으로 만든다
mismatch_path = TMP / "replies-mismatch.json"
mismatch_path.write_text(json.dumps([{**OK_ITEM, "fps": ["mismatch-fp"]}], ensure_ascii=False))
sys.argv = ["post_replies.py", "--repo", "other/repo", "--pr", "5",
            "--replies", str(mismatch_path), "--confirm"]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        ppr.main()
    raise AssertionError("repo 불일치인데 거부되지 않았다")
except SystemExit as e:
    assert "다르다" in str(e), f"repo 불일치 메시지가 아니다: {e}"
st = fc.load_state(".", 5)
assert "mismatch-fp" not in st.get("fp_replies", {}), "repo 불일치인데 fp_replies 가 병합됐다"

# 5h) 게시 중 예외에도 그 앞서 게시된 분은 finally 에서 fp_replies 에 기록된다
CRASH_TARGET[0] = 777
items = [{**OK_ITEM, "target_id": 555, "fps": ["survivor-fp"]},
         {**OK_ITEM, "target_id": 777, "fps": ["never-fp"]}]
crash_path = TMP / "replies-crash.json"
crash_path.write_text(json.dumps(items, ensure_ascii=False))
sys.argv = ["post_replies.py", "--repo", "o/r", "--pr", "5",
            "--replies", str(crash_path), "--confirm"]
try:
    with contextlib.redirect_stdout(io.StringIO()):
        ppr.main()
    raise AssertionError("예외가 전파되지 않았다")
except RuntimeError:
    pass
CRASH_TARGET[0] = None
st = fc.load_state(".", 5)
assert "survivor-fp" in st.get("fp_replies", {}), \
    "예외 이전 게시분이 finally 에서 기록되지 않았다"
assert "never-fp" not in st.get("fp_replies", {}), "예외가 난 항목까지 기록됐다"

print("PASS — is_bot 3분기 거부·인용 통과, target_id 게이트, reaction 필수 필드 게이트·"
      "review 404·값 검증, fps 타입 게이트(null 허용)·fetch↔post parity, dry-run 표시, "
      "branch_dir 가드, 일괄 기록 keep-first·리액션 보류·id 없는 성공 경고·repo 불일치 거부·"
      "예외 시 finally 기록, 엔드포인트 분기 모두 정상")
