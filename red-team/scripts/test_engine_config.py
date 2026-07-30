#!/usr/bin/env python3
"""엔진 설정의 우선순위와 argv 조립을 확인한다 — 여기가 틀리면 라운드가 엉뚱한 엔진으로 돈다.

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
            rr.resolve_engine(None)
            raise AssertionError("설정 없이 진행하면 안 된다")
        except SystemExit as e:
            assert "--set-engine" in str(e), e

        rr.set_engine("codex")
        assert json.loads(rr.CONFIG.read_text())["engine"] == "codex"
        assert rr.resolve_engine(None) == "codex"

        # 우선순위: --engine > 환경변수 > config
        os.environ["RED_TEAM_ENGINE"] = "claude"
        assert rr.resolve_engine(None) == "claude"
        assert rr.resolve_engine("codex") == "codex"
        del os.environ["RED_TEAM_ENGINE"]

        # argv: codex 는 acpx 를 앞세우고, claude 는 프롬프트를 -p 로 준다
        codex = rr.engine_cmd("codex", "PROMPT", "/repo", None)
        assert codex[-3:] == ["codex", "exec", "PROMPT"], codex
        assert "--cwd" in codex and "/repo" in codex

        claude = rr.engine_cmd("claude", "PROMPT", "/repo", "opus")
        assert claude[:3] == ["claude", "-p", "PROMPT"], claude
        assert "--model" in claude and "opus" in claude, claude
        # hook·skill 은 끄고, 인증까지 끊는 --bare 는 쓰지 않는다(실측: Not logged in 으로 죽는다)
        assert "--disable-slash-commands" in claude, claude
        assert claude[claude.index("--settings") + 1] == '{"hooks":{}}', claude
        assert "--bare" not in claude, claude
        # 리뷰어는 읽기만 한다 — 쓰기 도구가 붙으면 리뷰가 코드를 고칠 수 있다
        allowed = claude[claude.index("--allowedTools") + 1]
        assert "Edit" not in allowed and "Write" not in allowed, allowed

        try:
            rr.engine_cmd("gemini", "P", "/repo", None)
            raise AssertionError("모르는 엔진은 거절해야 한다")
        except SystemExit:
            pass

    print("ok")


if __name__ == "__main__":
    main()
