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
    ("ssh://git@github.com:22/team-c/app.git", "team-c__app"),  # 명시 포트는 키가 아니다(code-9 P2)
    ("ssh://git@example.com:22/app.git", "app"),                # 포트 소비 후 단일 세그먼트 → basename
]:
    sh(repo_a, "remote", "set-url", "origin", url)
    assert rr.repo_key(str(repo_a)) == want, (url, rr.repo_key(str(repo_a)))

sh(repo_a, "remote", "set-url", "origin", "/local/bare/app.git")  # 로컬 경로 — basename 폴백
assert rr.repo_key(str(repo_a)) == "app", rr.repo_key(str(repo_a))
sh(repo_a, "remote", "set-url", "origin", "../remotes/team/app.git")  # 상대 경로도 동일(code-2 P2)
assert rr.repo_key(str(repo_a)) == "app", rr.repo_key(str(repo_a))
sh(repo_a, "remote", "set-url", "origin", "file://localhost/x/team/app.git")  # file:// 도 로컬(code-8 P2)
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
# 길이 상한(code-2 P1): 접미가 붙어도 NAME_MAX(255) 이내, 절단 뒤에도 단사
long_a, long_b = "x/" + "a" * 300, "x/" + "a" * 299 + "b"
assert len(rr.branch_key(long_a)) <= 255
assert rr.branch_key(long_a) != rr.branch_key(long_b)  # 절단 구간 밖 차이는 해시가 가른다
# 대문자 hex 접미도 예약(code-4 P2) — APFS 비구분 파일시스템에서 소문자 키와 충돌 방지
up = lossy[:-8] + lossy[-8:].upper()
assert rr.branch_key(up) != up and rr.branch_key(up).startswith(up + "--")

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

# ── 2c) _single_key: 폴백 키와 pair 키의 출력 공간 분리 (code-3 P1) ────────
sh(repo_a, "remote", "set-url", "origin", "/local/bare/team__app.git")
k_fallback = rr.repo_key(str(repo_a))
assert k_fallback != "team__app" and k_fallback.startswith("team__app--"), k_fallback
sh(repo_a, "remote", "set-url", "origin", "git@github.com:team/app.git")
assert rr.repo_key(str(repo_a)) == "team__app"          # pair 키는 무접미 그대로
sh(repo_a, "remote", "set-url", "origin", "git@github.com:" + "o" * 155 + "/" + "r" * 99 + ".git")
assert len(rr.repo_key(str(repo_a))) <= 255              # 무손실 pair 도 길이 상한(code-3 P2)
sh(repo_a, "remote", "set-url", "origin", "git@github.com:team-a/app.git")  # 원복

# ── 3) branch_dir: 새 키 + 마이그레이션 ────────────────────────────────────
runs = pathlib.Path(os.environ["RED_TEAM_HOME"]) / "runs"    # 구 루트 (레거시 픽스처)
runs2 = pathlib.Path(os.environ["RED_TEAM_HOME"]) / "runs2"  # v2 루트 (신 키 전용)
new_a = runs2 / "team-a__app" / lossy

# 3a) 일치 — 구 기록의 repo_cwd 가 같은 origin 을 가리키면 rename
legacy = runs / "app" / "feature-foo"
(legacy / "code-1").mkdir(parents=True)
(legacy / "code-1" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_a)}))
assert rr.branch_dir(str(repo_a), migrate=True) == new_a
assert (new_a / "code-1" / "round.json").exists(), "구 라운드가 이전되지 않았다"
assert not legacy.exists()

# 3b) 새 경로가 이미 있으면 구 디렉토리를 건드리지 않는다
(legacy / "code-9").mkdir(parents=True)
assert rr.branch_dir(str(repo_a), migrate=True) == new_a
assert (legacy / "code-9").exists(), "새 경로가 있는데 구 디렉토리를 옮겼다"
(legacy / "code-9").rmdir(); legacy.rmdir()

# 3c) 불일치 — 다른 저장소(team-b/app)의 기록이면 두고 간다
repo_b = make_repo("b", "git@github.com:team-b/app.git", "feature/foo")
(legacy / "code-1").mkdir(parents=True)
(legacy / "code-1" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_a)}))
new_b = runs2 / "team-b__app" / lossy
assert rr.branch_dir(str(repo_b), migrate=True) == new_b
assert (legacy / "code-1" / "round.json").exists(), "남의 기록을 가져갔다"
assert not new_b.exists()

# 3d) 판정 불가 — round.json 이 없으면(준비만 된 라운드) 이전한다
import shutil; shutil.rmtree(legacy)
repo_c = make_repo("c", "git@github.com:team-c/app.git", "feature/foo")
legacy_c = runs / "app" / "feature-foo"
(legacy_c / "plan-1").mkdir(parents=True)
new_c = runs2 / "team-c__app" / lossy
assert rr.branch_dir(str(repo_c), migrate=True) == new_c
assert (new_c / "plan-1").is_dir(), "판정 불가 케이스가 이전되지 않았다"

# 3e) 혼합 기록(code-1 P2) — 하나라도 다른 저장소면 전체를 두고 간다 (첫 일치 break 금지)
repo_d = make_repo("d", "git@github.com:team-d/app.git", "feature/foo")
legacy_d = runs / "app" / "feature-foo"
(legacy_d / "code-1").mkdir(parents=True)
(legacy_d / "code-1" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_d)}))  # 일치
(legacy_d / "code-2").mkdir()
(legacy_d / "code-2" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_a)}))  # 불일치
rr.branch_dir(str(repo_d), migrate=True)
assert (legacy_d / "code-1").exists() and (legacy_d / "code-2").exists(), \
    "혼합 기록인데 통째로 가져갔다"
assert not (runs2 / "team-d__app" / lossy).exists()

# 3f) 잘린 UTF-8 round.json(code-1 P2) — 판정 불가로 취급, 죽지 않고 이전한다
shutil.rmtree(legacy_d)
repo_e = make_repo("e", "git@github.com:team-e/app.git", "feature/foo")
legacy_e = runs / "app" / "feature-foo"
(legacy_e / "code-1").mkdir(parents=True)
(legacy_e / "code-1" / "round.json").write_bytes('{"repo_cwd": "가나다'.encode()[:-1])
new_e = runs2 / "team-e__app" / lossy
assert rr.branch_dir(str(repo_e), migrate=True) == new_e
assert (new_e / "code-1").is_dir(), "잘린 UTF-8 기록에서 이전이 죽었다"

# 3g) 비 dict 유효 JSON(null·[])·비문자열 repo_cwd(code-2 P2) — 판정 불가, 죽지 않는다
repo_f = make_repo("f", "git@github.com:team-f/app.git", "feature/foo")
legacy_f = runs / "app" / "feature-foo"
for i, body in enumerate(["null", "[]", '{"repo_cwd": []}'], 1):
    (legacy_f / f"code-{i}").mkdir(parents=True)
    (legacy_f / f"code-{i}" / "round.json").write_text(body)
new_f = runs2 / "team-f__app" / lossy
assert rr.branch_dir(str(repo_f), migrate=True) == new_f
assert (new_f / "code-3").is_dir(), "비 dict 기록에서 이전이 죽었다"

# 3h) repo_cwd 디렉토리가 git 워크트리가 아니면 판정 불가 — 오판 거부 없이 이전(code-3 P2)
repo_g = make_repo("g", "git@github.com:team-g/app.git", "feature/foo")
legacy_g = runs / "app" / "feature-foo"
plain = pathlib.Path(TMP) / "plain-dir"; plain.mkdir()   # git 아님
(legacy_g / "code-1").mkdir(parents=True)
(legacy_g / "code-1" / "round.json").write_text(json.dumps({"repo_cwd": str(plain)}))
new_g = runs2 / "team-g__app" / lossy
assert rr.branch_dir(str(repo_g), migrate=True) == new_g
assert (new_g / "code-1").is_dir(), "비워크트리 repo_cwd 가 이전을 오판 거부했다"

# 3i) v2 루트의 구조적 스쿼팅 배제(code-7 P1) — 구 루트의 리터럴 `team__app2` 디렉토리
# (basename 에 `__` 를 담은 다른 저장소의 gen0, 또는 gen1)가 새 키와 이름이 같아도,
# 루트가 달라 새 키 자리를 선점할 수 없다. gen1 마이그레이션도 여기서 함께 확인한다.
repo_h = make_repo("h", "git@github.com:team/app2.git", "main")
gen1_h = runs / "team__app2" / "main"                    # gen1 (미출시 중간 레이아웃) 픽스처
(gen1_h / "code-9").mkdir(parents=True)
(gen1_h / "code-9" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_h)}))
new_h = runs2 / "team__app2" / "main"
assert rr.branch_dir(str(repo_h), migrate=True) == new_h
assert (new_h / "code-9").is_dir(), "gen1 레이아웃이 v2 루트로 이전되지 않았다"
assert not gen1_h.exists()

# 3j) dry-run 읽기 전용 해석(code-3 P1) — MIGRATE=False 면 rename 없이 구 경로를 반환
shutil.rmtree(new_h.parent)
repo_i = make_repo("i", "git@github.com:team-i/app.git", "feature/foo")
legacy_i = runs / "app" / "feature-foo"
(legacy_i / "code-1").mkdir(parents=True)
rr.MIGRATE = False
assert rr.branch_dir(str(repo_i), migrate=True) == legacy_i, "dry-run 해석이 구 경로를 반환하지 않았다"
assert legacy_i.is_dir(), "MIGRATE=False 인데 rename 이 일어났다"
rr.MIGRATE = True
# migrate=False 인자도 같은 무변경 해석(code-4 P1) — 마커 검사 등 경고용 파생이 쓴다
assert rr.branch_dir(str(repo_i), migrate=False) == legacy_i
assert legacy_i.is_dir(), "migrate=False 인데 rename 이 일어났다"
# 기본값이 무변경(code-6 P1) — 조회 소비처(pr-triage 등)는 인자 없이 불러도 순수하다
assert rr.branch_dir(str(repo_i)) == legacy_i
assert legacy_i.is_dir(), "기본 호출인데 rename 이 일어났다"
assert rr.target_dir(str(repo_i)) == runs2 / "team-i__app" / lossy  # 표시용 순수 계산
assert rr.branch_dir(str(repo_i), migrate=True) == runs2 / "team-i__app" / lossy  # 실제 실행은 이전한다

# 3k) 초장문 repo_cwd(code-4 P2) — Path.is_dir 의 OSError 도 판정 불가, 죽지 않는다
repo_j = make_repo("j", "git@github.com:team-j/app.git", "feature/foo")
legacy_j = runs / "app" / "feature-foo"
(legacy_j / "code-1").mkdir(parents=True)
(legacy_j / "code-1" / "round.json").write_text(json.dumps({"repo_cwd": "/x/" + "y" * 5000}))
new_j = runs2 / "team-j__app" / lossy
assert rr.branch_dir(str(repo_j), migrate=True) == new_j
assert (new_j / "code-1").is_dir(), "초장문 repo_cwd 에서 이전이 죽었다"

# 3l) 외부 소유 gen1 을 건너뛰고 자기 gen0 을 이전한다(code-8 P2) — 무변경 해석도 동일 선택
repo_l = make_repo("l", "git@github.com:team/appx.git", "main")
gen1_l = runs / "team__appx" / "main"                    # 외부(repo_a) 소유의 gen1 자리
(gen1_l / "code-9").mkdir(parents=True)
(gen1_l / "code-9" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_a)}))
gen0_l = runs / "appx" / "main"                          # 자기 gen0 기록
(gen0_l / "code-7").mkdir(parents=True)
(gen0_l / "code-7" / "round.json").write_text(json.dumps({"repo_cwd": str(repo_l)}))
assert rr.branch_dir(str(repo_l)) == gen0_l, "무변경 해석이 외부 gen1 을 반환했다"
new_l = runs2 / "team__appx" / "main"
assert rr.branch_dir(str(repo_l), migrate=True) == new_l
assert (new_l / "code-7").is_dir(), "외부 gen1 에 막혀 자기 gen0 을 이전하지 못했다"
assert (gen1_l / "code-9").is_dir(), "외부 gen1 을 건드렸다"

# ── 4) resume 정합 ─────────────────────────────────────────────────────────
assert new_a.name == rr.branch_key("feature/foo")        # 디렉토리명 == branch_key(브랜치)
import resume as rs
hit, _mm = rs.resolve_base("team-a__app/feature-foo", str(repo_a))  # parent/name 매칭(code-3 P2)
assert hit == new_a, hit
# name 우선(code-4 P2) — 티켓 키가 repo 키에도 걸려 다중 후보가 되지 않는다
(runs / "TICKET-9__x" / "main").mkdir(parents=True)
(runs / "org__app" / "TICKET-9").mkdir(parents=True)
hit2, _ = rs.resolve_base("ticket-9", str(repo_a))
assert hit2 == runs / "org__app" / "TICKET-9", hit2
# 완전 일치 우선(code-5 P2) — 안내된 구 경로 식별자 재입력이 새 경로와 또 겹치지 않는다
(runs / "appq" / "feature-q").mkdir(parents=True)
(runs / "t__appq" / "feature-q--12345678").mkdir(parents=True)
hit3, _ = rs.resolve_base("appq/feature-q", str(repo_a))
assert hit3 == runs / "appq" / "feature-q", hit3
# 워크트리 파생 동등성(code-5 P2) — 같은 브랜치명의 다른 저장소 워크트리를 거른다
wt, _how = rs.worktree_for(new_a, str(repo_b), None)
assert wt is None, wt                                      # repo_b(feature/foo)는 new_a 소속이 아니다
wt, _how = rs.worktree_for(new_a, str(repo_a), None)
assert wt == str(repo_a)
# 키 조회의 무부수효과(code-5 P2) — 다른 작업 조회가 cwd 의 구 라운드를 이전시키지 않는다
repo_k = make_repo("k", "git@github.com:team-k/app.git", "feature/foo")
legacy_k = runs / "app" / "feature-foo"
(legacy_k / "code-1").mkdir(parents=True)
rs.resolve_base("appq/feature-q", str(repo_k))
assert legacy_k.is_dir(), "키 조회가 cwd 의 구 라운드를 이전시켰다"
# 3부 식별자(code-8 P1) — 두 루트에 같은 parent/name 이 공존해도 루트 포함 키로 유일 선택
(runs / "dup__x" / "br").mkdir(parents=True)
(runs2 / "dup__x" / "br").mkdir(parents=True)
h_new, _ = rs.resolve_base("runs2/dup__x/br", str(repo_a))
h_old, _ = rs.resolve_base("runs/dup__x/br", str(repo_a))
assert h_new == runs2 / "dup__x" / "br" and h_old == runs / "dup__x" / "br", (h_new, h_old)

print("test_runs_namespace: ok")
shutil.rmtree(TMP)
