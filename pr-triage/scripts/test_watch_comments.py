#!/usr/bin/env python3
"""예약 `--once` 감시가 빈 baseline 뒤 첫 코멘트를 놓치지 않는지 확인한다."""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_comments as fetch
import watch_comments as watch


def main():
    state = {}
    incoming = []
    incoming_calls = [0]
    current_repo = ["owner/repo"]
    repo_ids = {
        "owner/repo": "101",
        "renamed/repo": "101",
        "other/repo": "202",
    }

    watch.detect = lambda *_: (current_repo[0], 17)
    def fake_gh(*args, **_kwargs):
        if args[:2] == ("api", "user"):
            return "me"
        if args[0] == "api" and args[1].startswith("repos/"):
            return repo_ids.get(args[1][len("repos/"):].casefold())
        raise AssertionError(args)
    watch.gh = fake_gh
    fetch.repository_id = lambda repo: int(repo_ids[repo.casefold()]) \
        if repo.casefold() in repo_ids else None
    fetch.gh_auth_ok = lambda: True
    def fake_incoming(*_args):
        incoming_calls[0] += 1
        return list(incoming)
    watch.incoming = fake_incoming
    watch.load_state = lambda *_: dict(state) if state else {"pr": 17, "notified": []}
    watch.save_state = lambda _cwd, _pr, saved: state.update(saved)
    def fake_merge(_cwd, _pr, updates, adopt_repo=False):
        current_id, status = fetch.repository_status(state, updates["repo"])
        assert status == "same" or (status == "adoptable" and adopt_repo)
        state.update({**updates, "repo_id": current_id})
        return dict(state)
    watch.merge_state = fake_merge

    def run_once(*args):
        sys.argv = ["watch_comments.py", "--once", *args]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            watch.main()
        return out.getvalue()

    assert "기존 0건" in run_once()
    incoming.append(("inline", 123, "reviewer", "첫 리뷰", "https://example.test/123"))
    second = run_once()
    assert "[pr-triage]" in second and "id=123" in second, second
    assert state["notified"] == [123] and state["repo_id"] == 101, state

    current_repo[0] = "OWNER/REPO"
    run_once()  # GitHub repo 식별자는 대소문자만 다르면 같은 저장소다

    state.pop("repo_id")  # 기존 설치의 legacy 상태도 과거 slug 조회로 ID를 backfill한다
    current_repo[0] = "renamed/repo"
    run_once()  # rename/transfer 뒤에도 불변 repository ID가 같으면 커서를 승계한다
    assert state["repo"] == "renamed/repo" and state["notified"] == [123], state

    current_repo[0] = "other/repo"
    try:
        run_once()
        raise AssertionError("repo 변경을 거부하지 않았다")
    except SystemExit as e:
        assert "renamed/repo" in str(e) and "other/repo" in str(e), e
    assert state["repo"] == "renamed/repo" and state["notified"] == [123], state

    try:
        run_once("--adopt-repo")
        raise AssertionError("확실히 다른 repository ID를 채택했다")
    except SystemExit as e:
        assert "--adopt-repo" not in str(e), e
    assert state["repo"] == "renamed/repo" and state["repo_id"] == 101, state

    state.pop("repo_id")
    state["repo"] = "gone/repo"  # legacy slug가 사라져 동일성을 확인할 수 없는 경우
    before_calls = incoming_calls[0]
    try:
        run_once("--cwd", "relative repo")
        raise AssertionError("확인 불가 legacy repo를 묵시적으로 채택했다")
    except SystemExit as e:
        assert "--adopt-repo" in str(e), e
        assert str(Path("relative repo").resolve()) in str(e), e
    adopted = run_once("--adopt-repo")
    assert "채택" in adopted and "종료" in adopted and "원래 명령" in adopted \
        and incoming_calls[0] == before_calls, adopted
    assert state["repo"] == "other/repo" and state["repo_id"] == 202, state
    adopted_again = run_once("--adopt-repo")
    assert "이미 채택" in adopted_again and incoming_calls[0] == before_calls, adopted_again

    events = []
    incoming.append(("inline", 124, "reviewer", "두 번째 리뷰", "https://example.test/124"))
    real_merge, real_print = watch.merge_state, getattr(watch, "print", None)
    def ordered_merge(*args, **kwargs):
        events.append("save")
        return fake_merge(*args, **kwargs)
    watch.merge_state = ordered_merge
    watch.print = lambda *_args, **_kwargs: events.append("print")
    run_once()
    watch.merge_state = real_merge
    if real_print is None:
        del watch.print
    else:
        watch.print = real_print
    assert events.index("print") < events.index("save"), events

    current_repo[0] = "missing/repo"
    auth_checks = [0]
    fetch.gh_auth_ok = lambda: auth_checks.__setitem__(0, auth_checks[0] + 1) or False
    try:
        run_once("--adopt-repo")
        raise AssertionError("ID를 확인할 수 없는 repo를 채택했다")
    except SystemExit as e:
        assert "gh auth login" in str(e), e
    assert auth_checks[0] == 1
    assert state["repo"] == "other/repo" and state["repo_id"] == 202, state

    print("PASS — 빈 baseline, repo rename/transfer, 명시적 채택을 안전하게 처리한다")


if __name__ == "__main__":
    main()
