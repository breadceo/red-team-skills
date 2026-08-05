"""post_replies 의 게시 전 검증 게이트·리액션·일괄 fp_replies 기록 검증.

gh 호출은 monkeypatch 로 대체한다 — 네트워크 없이 돈다. 실행: python3 test_post_replies.py
"""
import contextlib, io, json, os, sys, tempfile, types
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="post-test-"))
os.environ["RED_TEAM_HOME"] = str(TMP)          # 상태 격리 — import 전에 잡아야 한다
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 설치 위치를 가정하지 않는다
import fetch_comments as fc
import post_replies as ppr

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
    return types.SimpleNamespace(returncode=0, stderr="",
                                 stdout=json.dumps({"id": 991, "html_url": "http://r/991"}))


FAIL_REACTION = [False]
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
out = run_main([{**OK_ITEM, "fps": [FP, "new-fp"], "reaction": "-1"}], "--confirm")
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

print("PASS — is_bot 3분기 거부·인용 통과, reaction 필수 필드 게이트·review 404·값 검증, "
      "fps 타입 게이트, fetch↔post parity, dry-run 표시, branch_dir 가드, "
      "일괄 기록 keep-first·리액션 실패 별도 보고, 엔드포인트 분기 모두 정상")
