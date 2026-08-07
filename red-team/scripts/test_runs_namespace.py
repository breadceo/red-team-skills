"""issue #8 — runs/ 키 네임스페이스 검증.

repo 키의 owner 포함, 브랜치 키의 단사성, 구 레이아웃 마이그레이션 3분기
(일치 → 이전 / 불일치 → 거부 / 판정 불가 → 이전)를 확인한다.
"""
import json, os, pathlib, subprocess, sys, tempfile

TMP = tempfile.mkdtemp(prefix="rt-ns-")
os.environ["RED_TEAM_HOME"] = str(pathlib.Path(TMP) / "home")  # import 전에 고정해야 한다
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_round as rr


def sh(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def make_repo(name, origin, branch):
    d = pathlib.Path(TMP) / name
    d.mkdir()
    sh(d, "init", "-b", "main")
    sh(d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-m", "x")
    if origin:
        sh(d, "remote", "add", "origin", origin)
    if branch != "main":
        sh(d, "checkout", "-b", branch)
    return d


# ── 1) repo_key: URL 형태별 owner 포함 ─────────────────────────────────────
repo_a = make_repo("a", "git@github.com:team-a/app.git", "feature/foo")
assert rr.repo_key(str(repo_a)) == "team-a__app", rr.repo_key(str(repo_a))

for url, want in [
    ("https://github.com/team-b/app.git", "team-b__app"),
    ("https://github.com/team-b/app", "team-b__app"),
    ("ssh://git@github.com/team-c/app.git", "team-c__app"),
]:
    sh(repo_a, "remote", "set-url", "origin", url)
    assert rr.repo_key(str(repo_a)) == want, (url, rr.repo_key(str(repo_a)))

sh(repo_a, "remote", "set-url", "origin", "/local/bare/app.git")  # 로컬 경로 — basename 폴백
assert rr.repo_key(str(repo_a)) == "app", rr.repo_key(str(repo_a))

no_origin = make_repo("standalone", None, "main")  # origin 없음 — 디렉토리명 폴백
assert rr.repo_key(str(no_origin)) == "standalone", rr.repo_key(str(no_origin))

sh(repo_a, "remote", "set-url", "origin", "git@github.com:team-a/app.git")  # 원복

# ── 2) branch_key: 단사성 ──────────────────────────────────────────────────
assert rr.branch_key("feature-foo") == "feature-foo"          # 무손실 — 구 키 그대로
lossy = rr.branch_key("feature/foo")
assert lossy.startswith("feature-foo-") and len(lossy) == len("feature-foo-") + 8
assert lossy != rr.branch_key("feature foo")                  # 같은 slug, 다른 원문 → 다른 키
assert rr.branch_key("feature/foo") == lossy                  # 결정적

# ── 3) branch_dir: 새 키 + 마이그레이션 ────────────────────────────────────
runs = pathlib.Path(os.environ["RED_TEAM_HOME"]) / "runs"
new_a = runs / "team-a__app" / lossy

# 3a) 일치 — 구 기록의 repo_cwd 가 같은 origin 을 가리키면 rename
legacy = runs / "app" / "feature-foo"
(legacy / "code-1").mkdir(parents=True)
(legacy / "code-1" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_a)}))
assert rr.branch_dir(str(repo_a)) == new_a
assert (new_a / "code-1" / "round.json").exists(), "구 라운드가 이전되지 않았다"
assert not legacy.exists()

# 3b) 새 경로가 이미 있으면 구 디렉토리를 건드리지 않는다
(legacy / "code-9").mkdir(parents=True)
assert rr.branch_dir(str(repo_a)) == new_a
assert (legacy / "code-9").exists(), "새 경로가 있는데 구 디렉토리를 옮겼다"
(legacy / "code-9").rmdir(); legacy.rmdir()

# 3c) 불일치 — 다른 저장소(team-b/app)의 기록이면 두고 간다
repo_b = make_repo("b", "git@github.com:team-b/app.git", "feature/foo")
(legacy / "code-1").mkdir(parents=True)
(legacy / "code-1" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_a)}))
new_b = runs / "team-b__app" / lossy
assert rr.branch_dir(str(repo_b)) == new_b
assert (legacy / "code-1" / "round.json").exists(), "남의 기록을 가져갔다"
assert not new_b.exists()

# 3d) 판정 불가 — round.json 이 없으면(준비만 된 라운드) 이전한다
import shutil; shutil.rmtree(legacy)
repo_c = make_repo("c", "git@github.com:team-c/app.git", "feature/foo")
legacy_c = runs / "app" / "feature-foo"
(legacy_c / "plan-1").mkdir(parents=True)
new_c = runs / "team-c__app" / lossy
assert rr.branch_dir(str(repo_c)) == new_c
assert (new_c / "plan-1").is_dir(), "판정 불가 케이스가 이전되지 않았다"

# ── 4) resume 워크트리 매칭 방향 정합 — 디렉토리명 == branch_key(브랜치) ──
assert new_a.name == rr.branch_key("feature/foo")

print("test_runs_namespace: ok")
shutil.rmtree(TMP)
