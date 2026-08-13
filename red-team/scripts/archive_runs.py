#!/usr/bin/env python3
"""오래된 red-team raw 산출물을 계측하거나 검증된 gzip으로 교체한다."""
import argparse, fcntl, gzip, json, os, re, shutil, stat, tempfile, time
from pathlib import Path

import run_round

HOME = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team"))
ROUND_RE = re.compile(r"(?:plan|code)-\d+")
CHUNK = 1024 * 1024


class Counter:
    def __init__(self):
        self.size = 0

    def write(self, data):
        self.size += len(data)
        return len(data)

    def flush(self):
        pass


def lock_round(round_dir: Path, *, exclusive: bool):
    path = round_dir / "round.json"
    flags = (os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        lock = os.fdopen(os.open(path, flags), "rb")
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            lock.close()
            return None
    except OSError:
        return None
    try:
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(lock.fileno(), mode | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return False
    return lock


def child_dirs(parent: Path):
    try:
        children = sorted(parent.iterdir())
    except OSError:
        return
    for child in children:
        if not child.is_symlink() and child.is_dir():
            yield child


def locked_rounds(root: Path, *, exclusive: bool):
    for repo in child_dirs(root):
        for branch in child_dirs(repo):
            for rd in child_dirs(branch):
                if not ROUND_RE.fullmatch(rd.name):
                    continue
                lock = lock_round(rd, exclusive=exclusive)
                if lock is False:
                    yield rd, None, None
                    continue
                if lock is None:
                    continue
                try:
                    state = json.loads(lock.read().decode())
                except (OSError, ValueError, UnicodeError):
                    lock.close()
                    continue
                if isinstance(state, dict) and state.get("coverage") == "full":
                    yield rd, state, lock
                else:
                    lock.close()


def candidates(round_dir: Path, cutoff: float):
    for path in sorted(round_dir.iterdir()):
        name = path.name
        raw = name.endswith(".txt") or name.endswith(".prompt.md") or "superseded-" in name
        temporary = name.startswith(".") and name.endswith(".tmp")
        if raw and not temporary and not name.endswith(".gz") and path.is_file() and not path.is_symlink() \
                and path.stat().st_mtime <= cutoff:
            yield path


def open_source(source: Path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(source, flags)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise OSError("source가 regular file이 아니다")
    return os.fdopen(fd, "rb"), (st.st_dev, st.st_ino), st.st_size


def same_source(source: Path, identity):
    try:
        st = source.lstat()
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) == identity


def gzip_to(src, sink):
    with gzip.GzipFile(filename="", mode="wb", compresslevel=1,
                       fileobj=sink, mtime=0) as zipped:
        shutil.copyfileobj(src, zipped, CHUNK)


def estimate(source: Path):
    try:
        src, _identity, original = open_source(source)
    except OSError:
        return None
    with src:
        counter = Counter()
        gzip_to(src, counter)
        return original, counter.size


def replace_with_gzip(source: Path, target: Path):
    if os.path.lexists(target):
        return None
    try:
        src, identity, original = open_source(source)
    except OSError:
        return None
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=source.parent)
    temp = Path(temp_name)
    try:
        with src, os.fdopen(fd, "wb") as sink:
            gzip_to(src, sink)
            sink.flush()
            os.fsync(sink.fileno())
        with gzip.open(temp, "rb") as check:
            while check.read(CHUNK):
                pass
        if not same_source(source, identity):
            return None
        try:
            os.link(temp, target)  # 원자적 no-clobber 게시
        except FileExistsError:
            return None
        temp.unlink()
        if not same_source(source, identity):
            return None
        source.unlink()
        return original, target.stat().st_size
    finally:
        temp.unlink(missing_ok=True)


def archive(home: Path, *, older_than: int, apply: bool, include_legacy: bool):
    if older_than < 0:
        raise ValueError("--older-than은 0 이상이어야 한다")
    totals = {"files": 0, "original": 0, "compressed": 0, "busy": 0, "conflicts": 0}
    if not home.is_dir() or home.is_symlink():
        return totals
    migration_fd = run_round.lock_migrations(home, exclusive=apply)
    try:
        roots = [home / "runs2"] + ([home / "runs"] if include_legacy else [])
        cutoff = time.time() - older_than * 86400
        for root in roots:
            if root.is_symlink():
                continue
            for rd, state, lock in locked_rounds(root, exclusive=apply):
                if lock is None:
                    totals["busy"] += 1
                    continue
                try:
                    for source in candidates(rd, cutoff):
                        target = source.with_name(source.name + ".gz")
                        if os.path.lexists(target):
                            totals["conflicts"] += 1
                            continue
                        measured = replace_with_gzip(source, target) if apply else estimate(source)
                        if measured is None:
                            totals["conflicts"] += 1
                            continue
                        original, compressed = measured
                        totals["files"] += 1
                        totals["original"] += original
                        totals["compressed"] += compressed
                finally:
                    if lock:
                        lock.close()
    finally:
        if migration_fd is not None:
            os.close(migration_fd)
    return totals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--older-than", type=int, default=30, metavar="DAYS")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="기본값; 파일을 바꾸지 않는다")
    mode.add_argument("--apply", action="store_true", help="검증한 gzip으로 원본을 교체한다")
    ap.add_argument("--include-legacy", action="store_true", help="구형 runs/도 명시적으로 포함")
    args = ap.parse_args()
    if args.older_than < 0:
        ap.error("--older-than은 0 이상이어야 한다")
    result = archive(HOME, older_than=args.older_than, apply=args.apply,
                     include_legacy=args.include_legacy)
    saved = result["original"] - result["compressed"]
    print(f"mode={'apply' if args.apply else 'dry-run'} files={result['files']} "
          f"original={result['original']} compressed={result['compressed']} saved={saved} "
          f"busy_rounds={result['busy']} conflicts={result['conflicts']}")


if __name__ == "__main__":
    main()
