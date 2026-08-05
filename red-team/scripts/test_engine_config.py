#!/usr/bin/env python3
"""엔진 설정의 우선순위·축별 배정·argv 조립을 확인한다 — 여기가 틀리면 라운드가 엉뚱한 엔진으로 돈다.

usage: python3 test_engine_config.py
"""
import importlib, json, os, subprocess, sys, tempfile
from pathlib import Path


def load(home: Path):
    os.environ["RED_TEAM_HOME"] = str(home)
    os.environ.pop("RED_TEAM_ENGINE", None)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_round
    return importlib.reload(run_round)


def main():
    with tempfile.TemporaryDirectory() as td:
        rr = load(Path(td))

        # config 없고 환경변수도 없으면 라운드를 돌리지 않고 최초 설정으로 돌려보낸다
        try:
            rr.resolve_engines(None)
            raise AssertionError("설정 없이 진행하면 안 된다")
        except SystemExit as e:
            assert "--set-engine" in str(e), e

        rr.set_engine("codex")
        cfg = json.loads(rr.CONFIG.read_text())
        assert cfg["engines"] == ["codex"] and cfg["engine"] == "codex", cfg
        assert rr.resolve_engines(None) == ["codex"]

        # 복수 저장 — 첫 항목이 기본, 중복은 접힌다
        rr.set_engine("codex,claude,codex")
        assert rr.resolve_engines(None) == ["codex", "claude"]

        # 구버전 config(단수 engine 키만) 도 읽는다
        rr.CONFIG.write_text(json.dumps({"engine": "claude"}))
        assert rr.resolve_engines(None) == ["claude"]

        # 우선순위: --engine > 환경변수 > config. --engine 도 콤마 목록을 받는다
        os.environ["RED_TEAM_ENGINE"] = "codex"
        assert rr.resolve_engines(None) == ["codex"]
        assert rr.resolve_engines("claude") == ["claude"]
        assert rr.resolve_engines("codex,claude") == ["codex", "claude"]
        del os.environ["RED_TEAM_ENGINE"]

        try:
            rr.parse_engines("gemini")
            raise AssertionError("모르는 엔진은 거절해야 한다")
        except SystemExit:
            pass

        # 축별 배정: prefer 는 가용 목록에 있을 때만 존중되고, 없으면 첫 엔진으로 폴백
        both = ["codex", "claude"]
        assert rr.assign("a-code", "code", both, None, None) == ("codex", "gpt-5.6-sol", "high", "deep")
        assert rr.assign("b2-interaction", "code", both, None, None) == ("claude", "opus", "high", "deep")
        assert rr.assign("b4-null-propagation", "code", both, None, None) == ("claude", "sonnet", "medium", "mid")
        assert rr.assign("b1-state-matrix", "code", both, None, None) == ("codex", "gpt-5.6-luna", "medium", "cheap")
        assert rr.assign("b3-visibility", "code", both, None, None) == ("claude", "sonnet", "medium", "cheap")
        assert rr.assign("a-plan", "plan", both, None, None) == ("codex", "gpt-5.6-sol", "high", "deep")
        # codex 단독 사용자 — claude prefer 축이 codex tier 로 폴백한다
        assert rr.assign("b2-interaction", "code", ["codex"], None, None) == ("codex", "gpt-5.6-sol", "high", "deep")
        assert rr.assign("b3-visibility", "code", ["codex"], None, None) == ("codex", "gpt-5.6-luna", "medium", "cheap")
        # GATES 밖 커스텀 축은 안전한 쪽(deep)으로
        assert rr.assign("custom-axis", "code", both, None, None)[3] == "deep"
        # CLI override 는 전 리뷰어 강제
        assert rr.assign("b3-visibility", "code", both, "opus", "max")[1:3] == ("opus", "max")

        # claude 하위 티어에 haiku 를 쓰지 않는다(사용자 결정)
        assert not any("haiku" in (m or "") for m, _ in rr.TIERS["claude"].values())

        # config assignments 오버라이드: 전체 지정이 tier 기본을 이긴다
        ov = {"b2-interaction": {"engine": "claude", "model": "sonnet", "effort": "high"}}
        assert rr.assign("b2-interaction", "code", both, None, None, ov) == \
            ("claude", "sonnet", "high", "deep")
        # 부분 오버라이드: engine 만 바꾸면 그 엔진의 tier 기본 model/effort 로 재계산
        ov2 = {"b2-interaction": {"engine": "codex"}}
        assert rr.assign("b2-interaction", "code", both, None, None, ov2) == \
            ("codex", "gpt-5.6-sol", "high", "deep")
        # 가용 밖 엔진 오버라이드는 통째로 무시 — --set-engine 한 방 전환이 이겨야 한다
        assert rr.assign("b2-interaction", "code", ["codex"], None, None, ov) == \
            ("codex", "gpt-5.6-sol", "high", "deep")
        # CLI --model/--effort 는 config 오버라이드보다도 세다
        assert rr.assign("b2-interaction", "code", both, "opus", "max", ov)[1:3] == ("opus", "max")

        # set_assignment 저장/복귀 라운드트립
        rr.set_engine("codex,claude")
        rr.set_assignment("b2-interaction=claude/sonnet/high")
        cfg = json.loads(rr.CONFIG.read_text())
        assert cfg["assignments"]["b2-interaction"] == \
            {"engine": "claude", "model": "sonnet", "effort": "high"}, cfg
        assert cfg["engines"] == ["codex", "claude"], "set_assignment 이 engines 를 건드렸다"
        rr.set_assignment("b2-interaction=")
        assert "b2-interaction" not in json.loads(rr.CONFIG.read_text())["assignments"]
        try:
            rr.set_assignment("b2-interaction=gemini/x/y")
            raise AssertionError("모르는 엔진 오버라이드는 거절해야 한다")
        except SystemExit:
            pass

        # show_assignments: 오버라이드 표시 + 추천(기본) 병기 + 축 성격(why) 출력
        import io
        from contextlib import redirect_stdout
        rr.set_assignment("b3-visibility=codex/gpt-5.6-luna/low")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rr.show_assignments()
        s = buf.getvalue()
        assert "✏ 오버라이드 (추천: claude/sonnet/medium)" in s, s
        assert "대비비 계산" in s and "[code 게이트]" in s and "[plan 게이트]" in s, s
        rr.set_assignment("b3-visibility=")

        # argv: codex 는 acpx 를 앞세우고 effort 를 CODEX_CONFIG env 로 준다
        codex, env = rr.engine_cmd("codex", "PROMPT", "/repo", "gpt-5.6-sol", "high")
        assert codex[-3:] == ["codex", "exec", "PROMPT"], codex
        assert "--cwd" in codex and "/repo" in codex
        assert "--model" in codex and "gpt-5.6-sol" in codex, codex
        assert codex[codex.index("--format") + 1] == "json", codex  # 토큰 집계용 스트림
        assert json.loads(env["CODEX_CONFIG"]) == {"model_reasoning_effort": "high"}, env
        _, env0 = rr.engine_cmd("codex", "P", "/repo", None, None)
        assert "CODEX_CONFIG" not in env0 or env0["CODEX_CONFIG"] == os.environ.get("CODEX_CONFIG")

        # claude 는 프롬프트를 -p 로, effort 를 --effort 로 준다
        claude, _ = rr.engine_cmd("claude", "PROMPT", "/repo", "opus", "high")
        assert claude[:3] == ["claude", "-p", "PROMPT"], claude
        assert "--model" in claude and "opus" in claude, claude
        assert claude[claude.index("--effort") + 1] == "high", claude
        assert claude[claude.index("--output-format") + 1] == "json", claude  # 토큰 집계용
        # hook·skill 은 끄고, 인증까지 끊는 --bare 는 쓰지 않는다(실측: Not logged in 으로 죽는다)
        assert "--disable-slash-commands" in claude, claude
        assert claude[claude.index("--settings") + 1] == '{"hooks":{}}', claude
        assert "--bare" not in claude, claude
        # 리뷰어는 읽기만 한다 — 쓰기 도구가 붙으면 리뷰가 코드를 고칠 수 있다
        allowed = claude[claude.index("--allowedTools") + 1]
        assert "Edit" not in allowed and "Write" not in allowed, allowed

        try:
            rr.engine_cmd("gemini", "P", "/repo", None, None)
            raise AssertionError("모르는 엔진은 거절해야 한다")
        except SystemExit:
            pass

        # parse_output: claude 는 JSON 래핑에서 result 와 usage 를 꺼낸다
        wrapped = json.dumps({"result": "리뷰 본문", "total_cost_usd": 0.28,
                              "usage": {"input_tokens": 2, "cache_creation_input_tokens": 46816,
                                        "cache_read_input_tokens": 0, "output_tokens": 4}})
        text, tok = rr.parse_output("claude", "경고 한 줄\n" + wrapped + "\n", "sonnet")
        assert text == "리뷰 본문" and tok == {"input": 46818, "output": 4,
                                              "total": 46822, "cost_usd": 0.28}, (text, tok)

        # parse_output: codex 는 ACP 스트림에서 chunk 연결 + result.usage 를 꺼낸다
        stream = "\n".join(json.dumps(x) for x in [
            {"jsonrpc": "2.0", "method": "session/update",
             "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "리뷰 "}}}},
            {"jsonrpc": "2.0", "method": "session/update",
             "params": {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "본문"}}}},
            {"jsonrpc": "2.0", "id": 2, "result": {"stopReason": "end_turn",
             "usage": {"totalTokens": 23406, "inputTokens": 14441, "cachedReadTokens": 8960,
                       "outputTokens": 5, "thoughtTokens": 0}}},
        ])
        text, tok = rr.parse_output("codex", stream, "gpt-5.6-sol")
        assert text == "리뷰 본문", text
        # inputTokens 는 캐시 제외 — input = 14441+8960 = 23401.
        # 비용: 14441*5 + 8960*0.5 + 5*30 = 76835 → $0.0768
        assert tok == {"input": 23401, "output": 5, "total": 23406, "cost_usd": 0.0768}, tok
        # 단가표에 없는 모델은 토큰만 집계하고 비용은 비운다
        _, tok2 = rr.parse_output("codex", stream, "unknown-model")
        assert tok2["cost_usd"] is None, tok2

        # 래핑 파싱 실패 시 원문 폴백 — 라운드가 죽는 대신 토큰 집계만 빠진다
        text, tok = rr.parse_output("claude", "그냥 텍스트", None)
        assert text == "그냥 텍스트" and tok is None
        text, tok = rr.parse_output("codex", '```json\n{"verdict":"GO","findings":[]}\n```', None)
        assert "verdict" in text and tok is None

        # e2e: 엔진을 가짜로 갈아끼우고 코드 게이트 한 라운드 — 혼합 배정이 round.json 에 남는가
        fake = ("echo '```json'; echo '{\"verdict\":\"GO\",\"findings\":[]}'; echo '```'")
        rr.engine_cmd = lambda e, p, c, m, ef: (["/bin/sh", "-c", fake], dict(os.environ))
        rr.set_engine("codex,claude")
        out = Path(td) / "e2e"
        ctx = Path(td) / "ctx.md"
        ctx.write_text("## 리뷰 대상\n(테스트)\n")
        sys.argv = ["run_round.py", "--cwd", td, "--context", str(ctx),
                    "--gate", "code", "--out", str(out)]
        rr.main()
        rj = json.loads((out / "round.json").read_text())
        assert rj["verdict"] == "GO" and rj["engine"] == "codex+claude", rj
        assert set(rj["assignments"]) == set(rj["reviewers"]) and len(rj["reviewers"]) == 5
        a = rj["assignments"]["a-code"]
        assert (a["engine"], a["model"], a["effort"], a["tier"]) == \
            ("codex", "gpt-5.6-sol", "high", "deep"), rj["assignments"]
        assert a["tokens"] is None  # 가짜 엔진은 래핑이 없으니 토큰 집계가 빠진다 (폴백 경로)
        assert rj["assignments"]["b3-visibility"]["engine"] == "claude"
        assert all(v == "GO" for v in rj["reviewers"].values()), rj["reviewers"]

        # --merge-into: 한 축을 NO-GO 로 오염시킨 뒤 그 축만 재실행해 원 라운드를 치유한다
        rj["reviewers"]["b1-state-matrix"] = "PARSE-FAIL"
        rj["access_errors"]["b1-state-matrix"] = 3
        rj["findings"] = [{"reviewer": "b1-state-matrix", "classification": "regression",
                           "severity": "P1", "claim": "낡은 지적"},
                          {"reviewer": "a-code", "classification": "pre-existing",
                           "severity": "P2", "claim": "남아야 한다"}]
        (out / "round.json").write_text(json.dumps(rj, ensure_ascii=False))
        (out / "b1-state-matrix.txt").write_text("이전 raw")
        sys.argv = ["run_round.py", "--gate", "code",
                    "--merge-into", str(out), "--reviewers", "b1-state-matrix"]
        rr.main()
        m = json.loads((out / "round.json").read_text())
        # 재실행한 축의 낡은 findings 는 걷히고, 다른 축 findings 는 그대로 남는다
        assert [f["claim"] for f in m["findings"]] == ["남아야 한다"], m["findings"]
        assert m["reviewers"]["b1-state-matrix"] == "GO" and len(m["reviewers"]) == 5, m["reviewers"]
        assert "b1-state-matrix" not in m["access_errors"], m["access_errors"]
        assert m["verdict"] == "GO" and m["counts"]["non_regression"] == 1, (m["verdict"], m["counts"])
        # 감사 기록: 교체 사실이 남고 이전 raw 출력이 보존된다
        assert m["reruns"][0]["reviewer"] == "b1-state-matrix", m.get("reruns")
        assert m["reruns"][0]["was"] == "PARSE-FAIL" and m["reruns"][0]["was_access_errors"] == 3
        sup = list(out.glob("b1-state-matrix.superseded-*.txt"))
        assert len(sup) == 1 and sup[0].read_text() == "이전 raw", sup
        assert m["repo_cwd"] == str(Path(td).resolve()), "병합이 repo_cwd 를 잃었다"

        # 전원 PARSE-FAIL 로 남는 병합은 GO 가 아니라 INVALID (재계산에서도 규칙 유지)
        m2 = dict(m, reviewers={k: "PARSE-FAIL" for k in m["reviewers"]}, findings=[])
        assert rr.recompute(m2)["verdict"] == "INVALID", m2["verdict"]

        # 일부만 PARSE-FAIL 이면 verdict 는 GO 로 나오지만 그 축이 못 본 상태이므로 coverage=partial 이다
        # (축이 빠진 GO 를 통과로 보지 않는 MoE 규칙과 같은 취급 — 경고문을 읽었는지에 의존하지 않는다)
        m3 = rr.recompute(dict(m, findings=[],
                               reviewers=dict(m["reviewers"], **{"b3-visibility": "PARSE-FAIL"})))
        assert m3["verdict"] == "GO", m3["verdict"]
        assert m3["coverage"] == "partial", m3
        # 원인을 갈라 남긴다 — skipped(호출 안 됨) 와 unparsed(PARSE-FAIL) 는 치유 안내가 다르다
        assert m3["unparsed"] == ["b3-visibility"] and m3["skipped"] == [], (m3["unparsed"], m3["skipped"])

        # 그 축을 재실행해 결과가 들어오면 full 로 돌아온다 (top-up 과 같은 회복 경로)
        m4 = rr.recompute(dict(m3, reviewers=dict(m3["reviewers"], **{"b3-visibility": "GO"})))
        assert m4["coverage"] == "full" and m4["unparsed"] == [], m4

        # --- MoE: coverage / --lean / top-up ---
        # 전체 축 라운드는 full 로 기록된다 (위 e2e·병합 라운드)
        assert m["coverage"] == "full" and m["skipped"] == [] and m["unparsed"] == [], \
            (m.get("coverage"), m.get("skipped"), m.get("unparsed"))

        # 직전 라운드가 없으면 lean 도 전체 축(베이스라인)이다
        lr, _why = rr.lean_reviewers("code", td)
        assert lr == list(rr.GATES["code"]), (lr, _why)

        # 직전 라운드에 regression 을 심으면 core + 그 축만 켜진다 (pre-existing 은 켜지 않는다)
        prev = rr.branch_dir(td) / "code-1"
        prev.mkdir(parents=True)
        (prev / "round.json").write_text(json.dumps({
            "gate": "code", "reviewers": {k: "GO" for k in rr.GATES["code"]},
            "findings": [{"reviewer": "b4-null-propagation", "classification": "regression"},
                         {"reviewer": "b3-visibility", "classification": "pre-existing"}]}))
        lr, _why = rr.lean_reviewers("code", td)
        assert lr == ["a-code", "b4-null-propagation"], (lr, _why)

        # e2e lean 라운드: 축이 빠진 GO 는 partial 로 기록되고 top-up 명령을 안내한다
        out2 = Path(td) / "e2e-lean"
        sys.argv = ["run_round.py", "--cwd", td, "--context", str(ctx),
                    "--gate", "code", "--out", str(out2), "--lean"]
        buf2 = io.StringIO()
        with redirect_stdout(buf2):
            rr.main()
        rj2 = json.loads((out2 / "round.json").read_text())
        assert set(rj2["reviewers"]) == {"a-code", "b4-null-propagation"}, rj2["reviewers"]
        assert rj2["verdict"] == "GO" and rj2["coverage"] == "partial", rj2
        assert rj2["skipped"] == ["b1-state-matrix", "b2-interaction", "b3-visibility"], rj2["skipped"]
        s2 = buf2.getvalue()
        assert "게이트 통과가 아니다" in s2 and "--merge-into" in s2, s2

        # top-up: 빠진 축을 같은 라운드에 병합하면 coverage 가 full 로 돌아온다
        sys.argv = ["run_round.py", "--gate", "code", "--merge-into", str(out2),
                    "--reviewers", ",".join(rj2["skipped"])]
        rr.main()
        rj3 = json.loads((out2 / "round.json").read_text())
        assert rj3["coverage"] == "full" and rj3["skipped"] == [], rj3
        assert len(rj3["reviewers"]) == 5 and rj3["verdict"] == "GO", rj3["reviewers"]

        # config 의 moe:true 는 lean 을 기본으로 만들고, --full 이 1회성 해제다
        cfgm = json.loads(rr.CONFIG.read_text())
        cfgm["moe"] = True
        rr.CONFIG.write_text(json.dumps(cfgm))
        out3 = Path(td) / "e2e-moe"
        sys.argv = ["run_round.py", "--cwd", td, "--context", str(ctx),
                    "--gate", "code", "--out", str(out3)]
        rr.main()
        assert set(json.loads((out3 / "round.json").read_text())["reviewers"]) == \
            {"a-code", "b4-null-propagation"}
        out4 = Path(td) / "e2e-moe-full"
        sys.argv = ["run_round.py", "--cwd", td, "--context", str(ctx),
                    "--gate", "code", "--out", str(out4), "--full"]
        rr.main()
        assert len(json.loads((out4 / "round.json").read_text())["reviewers"]) == 5
        cfgm["moe"] = False
        rr.CONFIG.write_text(json.dumps(cfgm))

        # 가드: --lean 은 --reviewers/--merge-into/--full 과 함께 쓸 수 없다
        for argv in (["--cwd", td, "--context", str(ctx), "--lean", "--reviewers", "a-code"],
                     ["--gate", "code", "--lean", "--merge-into", str(out2), "--reviewers", "a-code"],
                     ["--cwd", td, "--context", str(ctx), "--lean", "--full"]):
            sys.argv = ["run_round.py"] + argv
            try:
                rr.main()
                raise AssertionError(f"거절해야 한다: {argv}")
            except SystemExit as e:
                assert e.code != 0, argv

        # --- diff 스냅샷 ---
        # git 저장소가 아니면 diff 가 비고, 첨부는 조용히 생략된다
        assert rr.diff_snapshot(td, None) == ""

        repo = Path(td) / "repo"
        repo.mkdir()
        env_git = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        subprocess.run(["git", "init", "-q"], cwd=repo, env=env_git, check=True)
        (repo / "f.txt").write_text("old-line\n")
        subprocess.run(["git", "add", "."], cwd=repo, env=env_git, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "init"], cwd=repo, env=env_git, check=True)
        (repo / "f.txt").write_text("new-line\n")

        out5 = Path(td) / "e2e-diff"
        sys.argv = ["run_round.py", "--cwd", str(repo), "--context", str(ctx),
                    "--gate", "code", "--out", str(out5)]
        rr.main()
        assert (out5 / "diff.md").exists(), "diff.md 가 라운드에 보존되지 않았다"
        prompt = (out5 / "a-code.prompt.md").read_text()
        assert "## Diff 스냅샷" in prompt and "-old-line" in prompt and "+new-line" in prompt, \
            "리뷰어 프롬프트에 diff 스냅샷이 없다"
        # context.md 는 사람 문서다 — diff 가 섞이면 낡은 채 다음 라운드로 이관된다
        assert "Diff 스냅샷" not in (out5 / "context.md").read_text()

        # merge-into(top-up·재실행)는 원 라운드의 스냅샷을 그대로 본다 — 다시 뜨지 않는다
        (repo / "f.txt").write_text("changed-after-round\n")
        sys.argv = ["run_round.py", "--gate", "code", "--merge-into", str(out5),
                    "--reviewers", "b1-state-matrix"]
        rr.main()
        mp = (out5 / "b1-state-matrix.prompt.md").read_text()
        assert "+new-line" in mp and "changed-after-round" not in mp, \
            "병합 재실행이 원 라운드와 다른 diff 를 봤다"

        # 계획 게이트에는 diff 를 붙이지 않고, --diff-base 도 거절한다
        out6 = Path(td) / "e2e-plan"
        sys.argv = ["run_round.py", "--cwd", str(repo), "--context", str(ctx),
                    "--gate", "plan", "--out", str(out6)]
        rr.main()
        assert "Diff 스냅샷" not in (out6 / "a-plan.prompt.md").read_text()
        sys.argv = ["run_round.py", "--cwd", str(repo), "--context", str(ctx),
                    "--gate", "plan", "--out", str(Path(td) / "e2e-plan2"), "--diff-base", "HEAD"]
        try:
            rr.main()
            raise AssertionError("계획 게이트의 --diff-base 는 거절해야 한다")
        except SystemExit as e:
            assert e.code != 0

        # 상한을 넘는 diff 는 첨부하지 않는다 (라운드는 그대로 돈다)
        (repo / "big.txt").write_text("x" * (rr.DIFF_CAP + 100))
        subprocess.run(["git", "add", "."], cwd=repo, env=env_git, check=True)
        out7 = Path(td) / "e2e-bigdiff"
        sys.argv = ["run_round.py", "--cwd", str(repo), "--context", str(ctx),
                    "--gate", "code", "--out", str(out7)]
        buf7 = io.StringIO()
        with redirect_stdout(buf7):
            rr.main()
        assert "상한" in buf7.getvalue(), buf7.getvalue()
        assert "Diff 스냅샷" not in (out7 / "a-code.prompt.md").read_text()
        assert json.loads((out7 / "round.json").read_text())["verdict"] == "GO"

        # 가드: --merge-into 는 --out·전원 재실행·다른 컨텍스트를 거절한다
        for argv in (["--merge-into", str(out), "--reviewers", "b1-state-matrix", "--out", str(out)],
                     ["--merge-into", str(out)],
                     ["--merge-into", str(out), "--reviewers", "a-code", "--context", str(ctx)]):
            sys.argv = ["run_round.py", "--gate", "code"] + argv
            try:
                rr.main()
                raise AssertionError(f"거절해야 한다: {argv}")
            except SystemExit as e:
                assert e.code != 0, argv

    print("ok")


if __name__ == "__main__":
    main()
