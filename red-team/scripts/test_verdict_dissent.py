"""축 verdict 불일치(`verdict_dissent`) 기록·표면화 검증 — issue #23.

라운드 verdict 는 findings 에서만 도출한다(근거 없는 NO-GO 가 게이트를 세우면 안 된다).
그래서 축이 findings 없이 `NO-GO` 를 내면 그 반대가 집계에서 사라진다 — 실측(b2b PR #915
code-10)에서 5축 전원 NO-GO 인 라운드가 `GO` 로 찍혔다. 여기서 지키는 계약은 둘이다:
`recompute` 가 불일치를 라운드에 남기고, `resume.py` 가 그 GO 를 게이트 통과로 안내하지 않는다.
"""
import json, os, pathlib, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 설치 위치를 가정하지 않는다
from run_round import recompute

RESUME = pathlib.Path(__file__).resolve().parent / "resume.py"
CODE_AXES = ["a-code", "b1-state-matrix", "b2-interaction", "b3-visibility", "b4-null-propagation"]


def merged(reviewers: dict, findings=()) -> dict:
    return {"gate": "code", "reviewers": dict(reviewers), "findings": list(findings)}


def p1(reviewer="a-code"):
    return {"severity": "P1", "classification": "regression", "claim": "x", "reviewer": reviewer}


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


def main():
    # ① 실측 재현 — 전원 NO-GO + findings 0 → verdict 는 GO 지만 불일치가 남는다
    r = recompute(merged({a: "NO-GO" for a in CODE_AXES}))
    assert r["verdict"] == "GO", r["verdict"]          # 계산식은 바뀌지 않았다
    assert r["coverage"] == "full", r["coverage"]      # 축은 다 돌았다 — partial 로 못 잡는다
    assert r["verdict_dissent"] == sorted(CODE_AXES), r["verdict_dissent"]

    # ② 전원 GO → 불일치 없음
    r = recompute(merged({a: "GO" for a in CODE_AXES}))
    assert r["verdict"] == "GO" and r["verdict_dissent"] == [], r["verdict_dissent"]

    # ③ regression 이 있어 라운드가 NO-GO 면 불일치는 의미가 없다 — 이미 통과가 아니다
    r = recompute(merged({a: "NO-GO" for a in CODE_AXES}, findings=[p1()]))
    assert r["verdict"] == "NO-GO" and r["verdict_dissent"] == [], r["verdict_dissent"]

    # ④ 전원 PARSE-FAIL(INVALID)에도 불일치는 비어 있다
    r = recompute(merged({a: "PARSE-FAIL" for a in CODE_AXES}))
    assert r["verdict"] == "INVALID" and r["verdict_dissent"] == [], r["verdict_dissent"]

    # ⑤ 병합(top-up)이 그 축을 GO 로 바꾸면 자동으로 비워진다 — coverage 와 같은 성질
    m = merged({a: ("NO-GO" if a == "a-code" else "GO") for a in CODE_AXES})
    assert recompute(m)["verdict_dissent"] == ["a-code"]
    m["reviewers"]["a-code"] = "GO"
    assert recompute(m)["verdict_dissent"] == [], "병합 후에도 불일치가 남았다"

    # ⑥ resume.py 는 이 GO 를 게이트 통과로 안내하지 않는다 (partial 과 같은 자리에서 막힌다)
    with tempfile.TemporaryDirectory() as t:
        home = pathlib.Path(t)
        repo = make_repo(home)
        rd = home / "runs" / "org__repo" / "branch" / "code-1"
        rd.mkdir(parents=True)
        (rd / "context.md").write_text("## 의도\nx\n")
        (rd / "decisions.md").write_text("## 반영\n- 없음\n## 보류\n")
        rj = recompute({"gate": "code", "reviewers": {a: "NO-GO" for a in CODE_AXES},
                        "findings": [], "repo_cwd": str(repo)})
        rj["access_errors"] = {}
        (rd / "round.json").write_text(json.dumps(rj, ensure_ascii=False))
        env = dict(os.environ, RED_TEAM_HOME=str(home))
        out = subprocess.run([sys.executable, str(RESUME)], cwd=repo, env=env,
                             capture_output=True, text=True).stdout
        assert "게이트 통과가 아니다" in out and "a-code" in out, \
            "축 전원 NO-GO 인 GO 가 차단되지 않았다:\n" + out
        assert "이 게이트는 통과(GO)했다" not in out, "통과로 안내했다:\n" + out
        # --next 경로도 같은 자리에서 걸린다 — 다음 게이트로 넘어가면 안 된다
        out2 = subprocess.run([sys.executable, str(RESUME), "--next", "code"], cwd=repo,
                              env=env, capture_output=True, text=True).stdout
        assert "게이트 통과가 아니다" in out2, "--next 가 불일치를 우회했다:\n" + out2

    print("PASS — verdict_dissent 기록(전원 NO-GO·전원 GO·NO-GO 라운드·INVALID·병합 후 해소) "
          "및 resume.py 차단(기본·--next) 정상")


if __name__ == "__main__":
    main()
