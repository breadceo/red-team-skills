"""ABORTED 마커 종결 상태 + same-origin P1 감지 검증.

중단 상태의 진실은 `runs/<repo>/<branch>/ABORTED` 파일 하나다(산문 파싱 없음).
same-origin 감지는 비차단 경고라 어떤 손상 입력에도 resume 를 죽이면 안 된다.
"""
import json, os, pathlib, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 설치 위치를 가정하지 않는다
from resume import _origin_paths, same_origin_p1
from run_round import rel_to_root

RESUME = pathlib.Path(__file__).resolve().parent / "resume.py"


def make_repo(home: pathlib.Path) -> pathlib.Path:
    repo = home / "wt"
    repo.mkdir()
    for cmd in (["init", "-q", "-b", "branch"],
                ["remote", "add", "origin", "https://example.com/org/repo.git"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                 "--allow-empty", "-m", "seed"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return repo


def round_json(gate="code", verdict="NO-GO", findings=(), repo_cwd="", repo_root="", **extra):
    d = {"gate": gate, "verdict": verdict, "coverage": "full", "skipped": [], "unparsed": [],
         "reviewers": {"a-code": verdict}, "findings": list(findings),
         "counts": {"regression_P1": sum(1 for f in findings
                                         if isinstance(f, dict) and f.get("severity") == "P1"),
                    "regression_P2": 0, "non_regression": 0},
         "repo_cwd": repo_cwd}
    if repo_root:
        d["repo_root"] = repo_root
    d.update(extra)
    return d


def p1(file=None, origin_file=None):
    f = {"severity": "P1", "classification": "regression", "claim": "x", "reviewer": "a-code"}
    if file is not None:
        f["file"] = file
    if origin_file is not None:
        f["origin_file"] = origin_file
    return f


def write_round(base: pathlib.Path, name: str, data: dict | str, mtime: float):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    rj = d / "round.json"
    if isinstance(data, str):
        rj.write_bytes(data.encode("utf-8")[:-1])  # 멀티바이트 중간 절단 — 잘린 파일 재현
    else:
        rj.write_text(json.dumps(data, ensure_ascii=False))
    os.utime(rj, (mtime, mtime))
    return d


def resume_run(repo, home, *args):
    env = dict(os.environ, RED_TEAM_HOME=str(home))
    return subprocess.run([sys.executable, str(RESUME), *args],
                          cwd=repo, env=env, capture_output=True, text=True)


def main():
    root = "/repo"

    # rel_to_root: 병합 시 절대경로 finding 을 현재 루트 기준으로 상대화 (`:줄` 보존, 루트 밖은 원형)
    with tempfile.TemporaryDirectory() as t:
        r = str(pathlib.Path(t).resolve())
        assert rel_to_root(f"{r}/src/a.ts:12", r) == "src/a.ts:12"
        assert rel_to_root(f"{r}/src/a.ts", r) == "src/a.ts"
        assert rel_to_root("/elsewhere/b.ts:1", r) == "/elsewhere/b.ts:1", "루트 밖 절대경로가 변형됐다"
        assert rel_to_root("src/rel.ts:3", r) == "src/rel.ts:3", "상대경로가 변형됐다"

    # --- same-origin 감지 (단위) ---
    with tempfile.TemporaryDirectory() as t:
        base = pathlib.Path(t)
        # 감지: 같은 파일을 절대·`./`상대·맨 상대로 섞어 표기 + repo_root 정규화
        write_round(base, "code-1", round_json(findings=[p1(file="ctx.md:1", origin_file="/repo/src/useConnection.ts:10")], repo_root=root), 100)
        write_round(base, "code-2", round_json(findings=[p1(file="./src/useConnection.ts:20")], repo_root=root), 200)
        write_round(base, "code-3", round_json(findings=[p1(file="src/useConnection.ts:30")], repo_root=root), 300)
        assert same_origin_p1(base, "code") == ["src/useConnection.ts"], same_origin_p1(base, "code")

        # repo_root 없는 구계약 + repo_cwd 가 저장소 하위 디렉토리 → git 파생 폴백(여기선 git 없음 → repo_cwd 기준 그대로)
        # 원인이 라운드마다 다른 파일이면 비감지
        write_round(base, "code-3", round_json(findings=[p1(file="src/other.ts:5")], repo_root=root), 300)
        assert same_origin_p1(base, "code") == [], "원인이 흩어졌는데 경고가 떴다"

        # P1 없는 라운드가 끼면 '연속' 이 아니다
        write_round(base, "code-3", round_json(verdict="GO", findings=[], repo_root=root), 300)
        assert same_origin_p1(base, "code") == []

        # 2라운드뿐이면 판정하지 않는다
        assert same_origin_p1(base, "plan") == []

    with tempfile.TemporaryDirectory() as t:
        base = pathlib.Path(t)
        # 계획 게이트: origin_file 만 후보 — file 이 계획서든 다른 문서든 폴백하지 않는다
        for i, fname in enumerate(["plan-a.md:1", "PLAN.markdown:2", "redteam-context.md:3"], 1):
            write_round(base, f"plan-{i}", round_json(gate="plan", findings=[p1(file=fname)], repo_root=root), i * 100)
        assert same_origin_p1(base, "plan") == [], "계획 게이트에서 file 폴백이 일어났다"
        # 계획 게이트라도 origin_file 이 일치하면 감지된다
        for i in (1, 2, 3):
            write_round(base, f"plan-{i}", round_json(
                gate="plan", findings=[p1(file=f"plan-{i}.md:9", origin_file="src/a.ts:1")], repo_root=root), i * 100)
        assert same_origin_p1(base, "plan") == ["src/a.ts"]

    with tempfile.TemporaryDirectory() as t:
        base = pathlib.Path(t)
        # 코드 게이트 폴백에서 계획 문서 basename 은 대소문자·markdown 확장자 무관 제외
        for i, fname in enumerate(["PLAN.md:1", "PLAN.md:2", "PLAN.md:3"], 1):
            write_round(base, f"code-{i}", round_json(findings=[p1(file=fname)], repo_root=root), i * 100)
        assert same_origin_p1(base, "code") == [], "PLAN.md 가 원인 파일로 집계됐다"
        for i in (1, 2, 3):
            write_round(base, f"code-{i}", round_json(findings=[p1(file="PLAN.markdown:1")], repo_root=root), i * 100)
        assert same_origin_p1(base, "code") == [], "PLAN.markdown 이 원인 파일로 집계됐다"

    with tempfile.TemporaryDirectory() as t:
        base = pathlib.Path(t)
        # 계약 위반 방어: 비문자열 origin_file 은 file 폴백, 빈 문자열은 무시 — 예외 없이
        write_round(base, "code-1", round_json(findings=[p1(file="src/a.ts:1", origin_file={"path": "x"})], repo_root=root), 100)
        write_round(base, "code-2", round_json(findings=[p1(file="src/a.ts:2", origin_file="")], repo_root=root), 200)
        write_round(base, "code-3", round_json(findings=[p1(file="src/a.ts:3", origin_file="   ")], repo_root=root), 300)
        assert same_origin_p1(base, "code") == ["src/a.ts"], same_origin_p1(base, "code")

        # 빈 문자열만 있는 3라운드는 거짓 경고를 만들지 않는다
        for i in (1, 2, 3):
            write_round(base, f"code-{i}", round_json(findings=[p1(origin_file="")], repo_root=root), i * 100)
        assert same_origin_p1(base, "code") == [], "빈 origin_file 교집합으로 거짓 경고"

    with tempfile.TemporaryDirectory() as t:
        base = pathlib.Path(t)
        # '최근 3' 은 번호 순(생성 순)이다 — 오래된 라운드의 --merge-into 재실행이 mtime 을
        # 갱신해도 선택이 왜곡되지 않는다: code-1~3 은 같은 파일, code-4 는 다른 파일인데
        # code-1 의 round.json 이 가장 새 mtime 이어도 최근 3 = code-2~4 라 비감지가 맞다
        for i in (1, 2, 3):
            write_round(base, f"code-{i}", round_json(findings=[p1(file="src/hot.ts:1")], repo_root=root), i * 100)
        write_round(base, "code-4", round_json(findings=[p1(file="src/cold.ts:1")], repo_root=root), 400)
        write_round(base, "code-1", round_json(findings=[p1(file="src/hot.ts:1")], repo_root=root), 999)  # merge 재실행 재현
        assert same_origin_p1(base, "code") == [], "mtime 재기록이 '최근 3' 을 왜곡했다"

    with tempfile.TemporaryDirectory() as t:
        base = pathlib.Path(t)
        # 코드 게이트: origin_file 이 계획 문서로 제외되면 file 로 폴백한다 — 코드 경로 교집합 유지
        for i in (1, 2, 3):
            write_round(base, f"code-{i}", round_json(
                findings=[p1(file="src/useConnection.ts:1", origin_file="PLAN.md:1")], repo_root=root), i * 100)
        assert same_origin_p1(base, "code") == ["src/useConnection.ts"], \
            "origin_file 제외 후 file 폴백이 안 됐다"

    with tempfile.TemporaryDirectory() as t:
        base = pathlib.Path(t)
        # 손상 기록 격리: 잘린 한글 멀티바이트 / 구조 이상(문자열 finding) — 경고만 생략, 예외 없음
        write_round(base, "code-1", round_json(findings=[p1(file="src/a.ts:1")], repo_root=root), 100)
        write_round(base, "code-2", '{"gate": "code", "findings": [], "한글잘림": "가나다', 200)
        write_round(base, "code-3", round_json(findings=[p1(file="src/a.ts:3")], repo_root=root), 300)
        assert same_origin_p1(base, "code") == []
        write_round(base, "code-2", round_json(findings=["manual note"], repo_root=root), 200)
        assert same_origin_p1(base, "code") == []

    # _origin_paths: repo_cwd 하위 디렉토리 구계약 라운드도 git 파생으로 정규화된다
    with tempfile.TemporaryDirectory() as t:
        home = pathlib.Path(t)
        repo = make_repo(home)
        sub = repo / "packages" / "app"
        sub.mkdir(parents=True)
        d = write_round(home, "code-1", round_json(
            findings=[p1(file=str(repo / "src" / "b.ts") + ":7")], repo_cwd=str(sub)), 100)
        assert _origin_paths(d) == {"src/b.ts"}, _origin_paths(d)

    # --- ABORTED 마커 (resume 통합) ---
    with tempfile.TemporaryDirectory() as home:
        home_p = pathlib.Path(home)
        repo = make_repo(home_p)
        base = home_p / "runs" / "repo" / "branch"
        rd = write_round(base, "code-1", round_json(
            verdict="GO", coverage="partial", unparsed=["b3-visibility"],
            repo_cwd=str(repo)), 100)
        (rd / "context.md").write_text("## 리뷰 대상\n\nx\n")
        (rd / "decisions.md").write_text("## 반영\n\n- 없음\n\n## 보류\n\n")
        marker = base / "ABORTED"
        marker.write_text("웹화 이후 재설계로 중단.\norigin: https://example.com/org/repo.git branch: branch — UNIQUE-TAIL-77")

        # ① 차단 + 경로·본문 전체(끝 식별자까지) 출력, partial top-up 안내도 없다
        out = resume_run(repo, home_p).stdout
        assert "중단됐다" in out and str(marker) in out, out
        assert "UNIQUE-TAIL-77" in out, "본문이 잘렸다 — 끝의 식별 정보가 안내에 없다"
        assert "--merge-into" not in out and "게이트 통과가 아니다" not in out, \
            "중단된 티켓에 top-up 안내가 나갔다:\n" + out

        # ② --next 는 새 라운드를 만들지 않는다
        before = {p.name for p in base.iterdir()}
        out2 = resume_run(repo, home_p, "--next", "code").stdout
        assert {p.name for p in base.iterdir()} == before, "중단됐는데 새 라운드 디렉토리가 생겼다"
        assert "중단됐다" in out2, out2

        # ③ 준비만 된 라운드(round.json 없음)가 최신이어도 차단은 유지된다
        (base / "code-2").mkdir()
        (base / "code-2" / "context.md").write_text("## 리뷰 대상\n\nx\n")
        out3 = resume_run(repo, home_p).stdout
        assert "중단됐다" in out3 and "run_round.py" not in out3, \
            "준비만 된 라운드에서 실행 안내가 나갔다:\n" + out3

        # ④ 본문을 읽을 수 없어도(비 UTF-8) 차단·경로 안내는 산다 — 존재가 곧 상태다
        marker.write_bytes(b"\xff\xfe invalid \x80")
        out_bad = resume_run(repo, home_p).stdout
        assert "중단됐다" in out_bad and str(marker) in out_bad, \
            "본문 읽기 실패가 차단 안내를 죽였다:\n" + out_bad
        assert "본문을 읽을 수 없다" in out_bad, out_bad
        marker.write_text("웹화 이후 재설계로 중단.\norigin: https://example.com/org/repo.git branch: branch — UNIQUE-TAIL-77")

        # ⑤ 마커 삭제(재개)면 기존 동작으로 돌아간다
        marker.unlink()
        out4 = resume_run(repo, home_p).stdout
        assert "중단됐다" not in out4 and ("TODO(resume)" in out4 or "실행되지 않았다" in out4), out4

    print("PASS — ABORTED 차단(경로·본문 전체, --next 거부, 준비 라운드, 재개), "
          "same-origin 감지(정규화·repo_root·게이트별 후보·계획문서 제외·계약위반 방어·손상 격리) 모두 정상")


if __name__ == "__main__":
    main()
