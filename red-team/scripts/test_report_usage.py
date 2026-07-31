#!/usr/bin/env python3
"""report_usage 집계 검증 — 구버전 라운드 제외, regression 분자, 축·배정별 합산.

usage: python3 test_report_usage.py
"""
import importlib, io, json, os, sys, tempfile
from contextlib import redirect_stdout
from pathlib import Path


def round_json(assignments, findings):
    return json.dumps({"assignments": assignments, "findings": findings,
                       "reviewers": {}, "verdict": "GO"})


def main():
    with tempfile.TemporaryDirectory() as td:
        runs = Path(td) / "runs" / "repo" / "feature-X"
        # 신형 라운드 2개 + 구버전 1개
        a = {"a-code": {"engine": "codex", "model": "gpt-5.6-sol", "effort": "high", "tier": "deep",
                        "tokens": {"input": 90_000, "output": 10_000, "total": 100_000, "cost_usd": 0.75}},
             "b3-visibility": {"engine": "claude", "model": "sonnet", "effort": "medium", "tier": "cheap",
                               "tokens": None}}  # 토큰 집계 실패 케이스
        f1 = [{"reviewer": "a-code", "classification": "regression"},
              {"reviewer": "a-code", "classification": "out-of-scope"}]
        (runs / "code-1").mkdir(parents=True)
        (runs / "code-1" / "round.json").write_text(round_json(a, f1))
        (runs / "code-1" / "decisions.md").write_text("## 반영\n- x\n")
        (runs / "code-2").mkdir()
        (runs / "code-2" / "round.json").write_text(round_json(a, f1))
        (runs / "plan-1").mkdir()
        (runs / "plan-1" / "round.json").write_text(json.dumps({"findings": [], "reviewers": {}}))

        os.environ["RED_TEAM_HOME"] = td
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        sys.argv = ["report_usage.py"]
        import report_usage
        report_usage = importlib.reload(report_usage)
        buf = io.StringIO()
        with redirect_stdout(buf):
            report_usage.main()
        out = buf.getvalue()

        assert "라운드 3건" in out and "집계 제외 1건" in out, out       # 구버전 분리
        assert "decisions.md 있는 라운드 1건" in out, out
        # 배정별: sol 2회 실행, 200k tok, $1.50, findings 4 중 regression 2 → $0.75/reg
        assert "codex/gpt-5.6-sol/high" in out and "200k" in out and "$1.50" in out, out
        assert "0.75" in out, out
        # 토큰이 없던 배정은 cost — 로 표시
        line = next(l for l in out.splitlines() if "claude/sonnet/medium" in l)
        assert "—" in line, line
        # 축별 표
        assert "a-code · deep" in out and "b3-visibility · cheap" in out, out

        # 필터가 안 맞으면 빈 결과로 종료한다
        sys.argv = ["report_usage.py", "no-such-repo"]
        try:
            with redirect_stdout(io.StringIO()):
                report_usage.main()
            raise AssertionError("빈 결과는 SystemExit 이어야 한다")
        except SystemExit:
            pass

    print("ok")


if __name__ == "__main__":
    main()
