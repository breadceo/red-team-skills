#!/usr/bin/env python3
"""엔진 설정의 우선순위·축별 배정·argv 조립을 확인한다 — 여기가 틀리면 라운드가 엉뚱한 엔진으로 돈다.

usage: python3 test_engine_config.py
"""
import importlib, json, os, sys, tempfile
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

        # argv: codex 는 acpx 를 앞세우고 effort 를 CODEX_CONFIG env 로 준다
        codex, env = rr.engine_cmd("codex", "PROMPT", "/repo", "gpt-5.6-sol", "high")
        assert codex[-3:] == ["codex", "exec", "PROMPT"], codex
        assert "--cwd" in codex and "/repo" in codex
        assert "--model" in codex and "gpt-5.6-sol" in codex, codex
        assert json.loads(env["CODEX_CONFIG"]) == {"model_reasoning_effort": "high"}, env
        _, env0 = rr.engine_cmd("codex", "P", "/repo", None, None)
        assert "CODEX_CONFIG" not in env0 or env0["CODEX_CONFIG"] == os.environ.get("CODEX_CONFIG")

        # claude 는 프롬프트를 -p 로, effort 를 --effort 로 준다
        claude, _ = rr.engine_cmd("claude", "PROMPT", "/repo", "opus", "high")
        assert claude[:3] == ["claude", "-p", "PROMPT"], claude
        assert "--model" in claude and "opus" in claude, claude
        assert claude[claude.index("--effort") + 1] == "high", claude
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
        assert rj["assignments"]["a-code"] == {"engine": "codex", "model": "gpt-5.6-sol",
                                               "effort": "high", "tier": "deep"}, rj["assignments"]
        assert rj["assignments"]["b3-visibility"]["engine"] == "claude"
        assert all(v == "GO" for v in rj["reviewers"].values()), rj["reviewers"]

    print("ok")


if __name__ == "__main__":
    main()
