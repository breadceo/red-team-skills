#!/usr/bin/env python3
"""archive_runs 보존 정책·원자성·기존 소비 도구 호환 회귀 검사."""
import gzip, hashlib, importlib, io, json, os, sys, tempfile, threading, time
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def fingerprint(root: Path):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob("*") if p.is_file()}


def write_round(base: Path, name: str, *, coverage="full", age_days=31):
    rd = base / name
    rd.mkdir(parents=True)
    assignment = {"a-plan": {"engine": "codex", "model": "x", "effort": "high",
                              "tier": "deep", "tokens": None}}
    state = {"verdict": "GO", "reviewers": {"a-plan": "GO"}, "findings": [],
             "assignments": assignment, "repo_cwd": str(base)}
    if coverage is not None:
        state["coverage"] = coverage
    (rd / "round.json").write_text(json.dumps(state))
    for name, body in {"a-plan.txt": b"raw\n" * 200, "a-plan.prompt.md": b"prompt\n" * 100,
                       "a-plan.superseded-20260101T000000.json": b"{}",
                       "context.md": b"context", "decisions.md": b"decisions",
                       "diff.md": b"diff"}.items():
        p = rd / name
        p.write_bytes(body)
        old = time.time() - age_days * 86400
        os.utime(p, (old, old))
    return rd


def main():
    with tempfile.TemporaryDirectory() as td:
        home = Path(td)
        branch = home / "runs2" / "breadceo__red-team-skills" / "main"
        full = write_round(branch, "plan-1")
        (full / "round.json").chmod(0o444)
        partial = write_round(branch, "plan-2", coverage="partial")
        running = branch / "plan-3"
        running.mkdir(parents=True)
        (running / "a-plan.txt").write_bytes(b"still running")
        fresh = write_round(branch, "plan-4", age_days=1)
        legacy = write_round(home / "runs" / "red-team-skills" / "main", "plan-1")
        unknown_legacy = write_round(home / "runs" / "red-team-skills" / "main", "plan-2",
                                     coverage=None)
        conflict = branch / "plan-5"
        conflict.mkdir()
        (conflict / "round.json").write_text(json.dumps({"coverage": "full"}))
        (conflict / "a-plan.txt").write_bytes(b"conflict")
        os.utime(conflict / "a-plan.txt", (time.time() - 31 * 86400,) * 2)
        (conflict / "a-plan.txt.gz").symlink_to("missing.gz")
        damaged = branch / "plan-6"
        damaged.mkdir()
        (damaged / "round.json").write_bytes(b"\xff")
        (damaged / "a-plan.txt").write_bytes(b"must stay")
        leftover = full / ".a-plan.superseded-20260101T000000.json.gz.deadbeef.tmp"
        leftover.write_bytes(b"incomplete gzip")
        os.utime(leftover, (time.time() - 31 * 86400,) * 2)
        linked_state = branch / "plan-8"
        linked_state.mkdir()
        valid_state = home / "valid-round.json"
        valid_state.write_text(json.dumps({"coverage": "full"}))
        (linked_state / "round.json").symlink_to(valid_state)
        (linked_state / "a-plan.txt").write_bytes(b"symlink state must stay")
        os.utime(linked_state / "a-plan.txt", (time.time() - 31 * 86400,) * 2)
        fifo_state = branch / "plan-9"
        fifo_state.mkdir()
        os.mkfifo(fifo_state / "round.json")
        (fifo_state / "a-plan.txt").write_bytes(b"fifo state must stay")
        os.utime(fifo_state / "a-plan.txt", (time.time() - 31 * 86400,) * 2)
        outside = home / "outside" / "main"
        escaped = write_round(outside, "plan-1")
        (home / "runs2" / "linked-owner").symlink_to(outside.parent, target_is_directory=True)
        linked_home = home / "linked-home"
        linked_home.mkdir()
        external_root = home / "external-runs2" / "owner" / "main"
        external_round = write_round(external_root, "plan-1")
        (linked_home / "runs2").symlink_to(external_root.parent.parent, target_is_directory=True)

        os.environ["RED_TEAM_HOME"] = td
        import archive_runs
        archive_runs = importlib.reload(archive_runs)
        assert archive_runs.lock_round(fifo_state, exclusive=False) is None
        assert archive_runs.lock_round(fifo_state, exclusive=True) is None
        missing_home = home / "never-created"
        parent_before = {p.name for p in home.iterdir()}
        empty = archive_runs.archive(missing_home, older_than=30, apply=False,
                                     include_legacy=True)
        assert empty == {"files": 0, "original": 0, "compressed": 0,
                         "busy": 0, "conflicts": 0}
        assert not missing_home.exists() and {p.name for p in home.iterdir()} == parent_before
        before = fingerprint(home)
        result = archive_runs.archive(home, older_than=30, apply=False, include_legacy=False)
        assert fingerprint(home) == before, "dry-run이 파일 또는 lock을 만들거나 바꿨다"
        assert result["files"] == 3 and result["compressed"] < result["original"], result
        assert result["conflicts"] == 1
        assert (damaged / "a-plan.txt").is_file() and (escaped / "a-plan.txt").is_file()
        assert (linked_state / "a-plan.txt").is_file(), "round.json symlink를 신뢰했다"
        assert (fifo_state / "a-plan.txt").is_file(), "round.json FIFO를 신뢰했다"
        assert leftover.is_file() and not leftover.with_name(leftover.name + ".gz").exists()
        archive_runs.archive(linked_home, older_than=30, apply=True, include_legacy=False)
        assert (external_round / "a-plan.txt").is_file(), "runs2 root symlink를 따라갔다"

        applied = archive_runs.archive(home, older_than=30, apply=True, include_legacy=False)
        assert applied == result, "dry-run과 apply의 대상·예상량이 갈렸다"
        for name in ("a-plan.txt", "a-plan.prompt.md", "a-plan.superseded-20260101T000000.json"):
            assert not (full / name).exists()
            with gzip.open(full / f"{name}.gz", "rb") as f:
                assert f.read()
        for name in ("round.json", "context.md", "decisions.md", "diff.md"):
            assert (full / name).is_file(), name
        assert (partial / "a-plan.txt").is_file() and (running / "a-plan.txt").is_file()
        assert (fresh / "a-plan.txt").is_file() and (legacy / "a-plan.txt").is_file()
        assert os.path.lexists(conflict / "a-plan.txt.gz") and (conflict / "a-plan.txt").is_file()
        once = fingerprint(home)
        archive_runs.archive(home, older_than=30, apply=True, include_legacy=False)
        assert fingerprint(home) == once, "성공 후 재실행이 산출물을 다시 건드렸다"

        raced = write_round(branch, "plan-7") / "a-plan.txt"
        raced_target = raced.with_name(raced.name + ".gz")
        original_link = archive_runs.os.link
        def create_target_before_publish(source, target):
            Path(target).write_bytes(b"other process")
            return original_link(source, target)
        archive_runs.os.link = create_target_before_publish
        try:
            assert archive_runs.replace_with_gzip(raced, raced_target) is None
        finally:
            archive_runs.os.link = original_link
        assert raced.is_file() and raced_target.read_bytes() == b"other process"

        fifo = branch / "plan-7" / "fifo.txt"
        os.mkfifo(fifo)
        assert archive_runs.estimate(fifo) is None, "FIFO를 일반 파일처럼 읽으려 했다"

        changed = write_round(branch, "code-4") / "a-plan.txt"
        changed_target = changed.with_name(changed.name + ".gz")
        original_link = archive_runs.os.link
        def replace_source_after_publish(temp, target):
            original_link(temp, target)
            changed.unlink()
            changed.write_bytes(b"new inode")
        archive_runs.os.link = replace_source_after_publish
        try:
            assert archive_runs.replace_with_gzip(changed, changed_target) is None
        finally:
            archive_runs.os.link = original_link
        assert changed.read_bytes() == b"new inode", "경합으로 교체된 새 source를 삭제했다"

        interrupted = write_round(branch, "code-1")
        original_unlink = Path.unlink
        source = interrupted / "a-plan.txt"
        def stop_after_publish(path, *args, **kwargs):
            if path == source:
                raise OSError("stop-after-replace")
            return original_unlink(path, *args, **kwargs)
        Path.unlink = stop_after_publish
        try:
            try:
                archive_runs.archive(home, older_than=30, apply=True, include_legacy=False)
                raise AssertionError("원본 삭제 실패가 전파되지 않았다")
            except OSError as e:
                assert str(e) == "stop-after-replace"
            assert source.is_file() and (interrupted / "a-plan.txt.gz").is_file()
        finally:
            Path.unlink = original_unlink

        import run_round
        run_round.merge_prepare(interrupted, ["a-plan"])
        assert not source.exists() and not (interrupted / "a-plan.txt.gz").exists()
        assert len(list(interrupted.glob("a-plan.superseded-*.txt"))) == 1
        assert len(list(interrupted.glob("a-plan.superseded-*.txt.gz"))) == 1
        source.write_bytes(b"new raw")

        lock = archive_runs.lock_round(interrupted, exclusive=True)
        try:
            locked_before = fingerprint(interrupted)
            busy = archive_runs.archive(home, older_than=30, apply=True, include_legacy=False)
            assert fingerprint(interrupted) == locked_before, "실행 중 라운드를 변경했다"
            assert busy["busy"] >= 1
        finally:
            lock.close()

        dry_race = write_round(branch, "code-3")
        shared = archive_runs.lock_round(dry_race, exclusive=False)
        try:
            try:
                run_round.lock_round(dry_race)
                raise AssertionError("dry-run shared lock 중 재실행 exclusive lock을 허용했다")
            except SystemExit:
                pass
        finally:
            shared.close()

        archive_runs.archive(home, older_than=30, apply=True, include_legacy=True)
        assert (legacy / "a-plan.txt.gz").is_file(), "명시한 legacy가 빠졌다"
        assert (unknown_legacy / "a-plan.txt").is_file(), "coverage 미상 legacy를 변경했다"

        migration_home = home / "migration-case"
        old_branch = migration_home / "runs" / "legacy" / "main"
        write_round(old_branch, "plan-1")
        moved_branch = migration_home / "runs2" / "legacy" / "main"
        migration_fd = run_round.lock_migrations(migration_home, exclusive=True)
        completed = []
        worker = threading.Thread(target=lambda: completed.append(
            archive_runs.archive(migration_home, older_than=30, apply=False, include_legacy=False)))
        worker.start()
        time.sleep(0.05)
        assert worker.is_alive(), "legacy archive가 migration lock을 기다리지 않았다"
        moved_branch.parent.mkdir(parents=True)
        old_branch.rename(moved_branch)
        os.close(migration_fd)
        worker.join(2)
        assert not worker.is_alive() and completed
        dry_after_move = completed[0]
        applied_after_move = archive_runs.archive(migration_home, older_than=30, apply=True,
                                                   include_legacy=False)
        assert dry_after_move == applied_after_move and dry_after_move["files"] > 0
        assert (moved_branch / "plan-1" / "a-plan.txt.gz").is_file(), \
            "migration 직후 runs2에서 재발견하지 못했다"

        import resume, summarize_round, report_usage
        resume = importlib.reload(resume)
        summarize_round = importlib.reload(summarize_round)
        report_usage = importlib.reload(report_usage)
        assert resume.latest_round(branch)[0] == dry_race
        with redirect_stdout(io.StringIO()):
            assert summarize_round.summarize(full / "round.json")["verdict"] == "GO"
        assert report_usage.rows("breadceo__red-team-skills/main/plan-1")[3] == 1

    print("test_archive_runs: ok")


if __name__ == "__main__":
    main()
