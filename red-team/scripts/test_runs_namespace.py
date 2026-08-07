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
assert lossy.startswith("feature-foo--") and len(lossy) == len("feature-foo--") + 8
assert lossy != rr.branch_key("feature foo")                  # 같은 slug, 다른 원문 → 다른 키
assert rr.branch_key("feature/foo") == lossy                  # 결정적
# 예약 접미(code-1 P1): lossy 키와 같은 문자열의 실존 브랜치는 강제 해시 — 출력 공간 분리
assert rr.branch_key(lossy) != lossy and rr.branch_key(lossy).startswith(lossy + "--")

# ── 2b) repo_key: `__` 경계 모호성 (code-1 P1) ─────────────────────────────
keys = []
for url in ["git@github.com:a__b/c.git", "git@github.com:a/b__c.git"]:
    sh(repo_a, "remote", "set-url", "origin", url)
    keys.append(rr.repo_key(str(repo_a)))
assert keys[0] != keys[1], keys                                # 경계가 다르면 키도 다르다
assert all(k.startswith("a__b__c--") for k in keys), keys      # 둘 다 해시로 갈린다
sh(repo_a, "remote", "set-url", "origin", "git@github.com:team_/app.git")  # 경계 인접 `_`
k1 = rr.repo_key(str(repo_a))
sh(repo_a, "remote", "set-url", "origin", "git@github.com:team/_app.git")
assert k1 != rr.repo_key(str(repo_a)), (k1, rr.repo_key(str(repo_a)))
sh(repo_a, "remote", "set-url", "origin", "git@github.com:team-a/app.git")  # 원복

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

# 3e) 혼합 기록(code-1 P2) — 하나라도 다른 저장소면 전체를 두고 간다 (첫 일치 break 금지)
repo_d = make_repo("d", "git@github.com:team-d/app.git", "feature/foo")
legacy_d = runs / "app" / "feature-foo"
(legacy_d / "code-1").mkdir(parents=True)
(legacy_d / "code-1" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_d)}))  # 일치
(legacy_d / "code-2").mkdir()
(legacy_d / "code-2" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_a)}))  # 불일치
rr.branch_dir(str(repo_d))
assert (legacy_d / "code-1").exists() and (legacy_d / "code-2").exists(), \
    "혼합 기록인데 통째로 가져갔다"
assert not (runs / "team-d__app" / lossy).exists()

# 3f) 잘린 UTF-8 round.json(code-1 P2) — 판정 불가로 취급, 죽지 않고 이전한다
shutil.rmtree(legacy_d)
repo_e = make_repo("e", "git@github.com:team-e/app.git", "feature/foo")
legacy_e = runs / "app" / "feature-foo"
(legacy_e / "code-1").mkdir(parents=True)
(legacy_e / "code-1" / "round.json").write_bytes('{"repo_cwd": "가나다'.encode()[:-1])
new_e = runs / "team-e__app" / lossy
assert rr.branch_dir(str(repo_e)) == new_e
assert (new_e / "code-1").is_dir(), "잘린 UTF-8 기록에서 이전이 죽었다"

# ── 4) resume 워크트리 매칭 방향 정합 — 디렉토리명 == branch_key(브랜치) ──
assert new_a.name == rr.branch_key("feature/foo")

print("test_runs_namespace: ok")
shutil.rmtree(TMP)
