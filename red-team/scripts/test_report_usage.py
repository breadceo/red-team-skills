#!/usr/bin/env python3
"""report_usage 집계 검증 — 구버전 라운드 제외, regression 분자, 축·배정별 합산,
게이트 종류별 plan/code 비율(비병합·미분류·파싱 실패 포함).

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

        # 게이트 종류별 절의 픽스처 — 자동 병합하지 않는다는 계약을 검사한다.
        #  runs2/zigbang__ceo-client : plan 1 / code 1
        #  runs/ceo-client (구)      : code 1        ← 짧은 이름과 합쳐지지 않아야 한다
        #  runs/dup                  : plan 1        ← runs2/dup 과 합쳐지지 않아야 한다
        #  runs2/dup                 : code 1
        #  runs2/broken              : plan 1 (손상 JSON — 새 표에는 잡힌다)
        #  runs2/misc                : eval-1 (미분류)
        v2 = Path(td) / "runs2"
        for ns, rid, body in [
            ("zigbang__ceo-client", "plan-1", round_json(a, [])),
            ("zigbang__ceo-client", "code-1", round_json(a, [])),
            ("dup", "code-1", round_json(a, [])),
            ("broken", "plan-1", "{not json"),
            ("misc", "eval-1", round_json(a, [])),
        ]:
            d = v2 / ns / "br" / rid
            d.mkdir(parents=True)
            (d / "round.json").write_text(body)
        for ns, rid in [("ceo-client", "code-1"), ("dup", "plan-1")]:
            d = Path(td) / "runs" / ns / "br" / rid
            d.mkdir(parents=True)
            (d / "round.json").write_text(round_json(a, []))

        os.environ["RED_TEAM_HOME"] = td
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        sys.argv = ["report_usage.py", "repo/feature-X"]
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

        sys.argv = ["report_usage.py"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            report_usage.main()
        out = buf.getvalue()

        # 게이트 종류별 — (루트, namespace) 키라 자동 병합이 없다
        def gate_row(label):
            line = next(l for l in out.splitlines() if l.startswith(label + " "))
            return [int(x) for x in line.split()[-3:-1]]  # plan, code

        assert gate_row("zigbang__ceo-client") == [1, 1], out
        assert gate_row("ceo-client (구)") == [0, 1], out       # 짧은 이름과 병합 금지
        assert gate_row("dup (구)") == [1, 0], out              # 양 루트 동일 namespace 분리
        assert gate_row("dup") == [0, 1], out
        assert gate_row("broken") == [1, 0], out               # 손상 JSON 도 새 표에는 잡힌다
        assert "misc" not in out.split("게이트 종류별")[1], out  # 미분류만 있으면 행이 없다
        # 합계는 중복 집계되지 않는다 — repo/feature-X 의 3라운드 + 위 픽스처 7라운드
        assert "라운드 10건" in out, out
        assert "파싱 실패 1건" in out and "plan/code 아닌 라운드 1건" in out, out
        assert "구버전이라 배정 집계 제외 1건" in out and "배정 집계 8건" in out, out
        total = next(l for l in out.splitlines() if l.startswith("합계"))
        assert [int(x) for x in total.split()[-3:-1]] == [4, 5], total

        # 필터가 안 맞으면 빈 결과로 종료한다
        sys.argv = ["report_usage.py", "no-such-repo"]
        try:
            with redirect_stdout(io.StringIO()):
                report_usage.main()
            raise AssertionError("빈 결과는 SystemExit 이어야 한다")
        except SystemExit:
            pass

        # 배정 데이터가 없어도(구버전·손상·미분류만) 라운드가 있으면 헤더·표가 나온다
        def run(filt):
            sys.argv = ["report_usage.py", filt]
            buf = io.StringIO()
            with redirect_stdout(buf):
                report_usage.main()
            return buf.getvalue()

        # 손상 JSON 뿐: 배정 표는 없지만 헤더와 게이트 표는 나온다
        o = run("broken")
        assert "라운드 1건" in o and "파싱 실패 1건" in o, o
        assert "배정별" not in o and "합계" in o, o
        # 미분류 뿐: 헤더에 세어지고 게이트 표는 안내로 대체된다
        o = run("misc")
        assert "라운드 1건" in o and "plan/code 아닌 라운드 1건" in o, o
        assert "plan/code 라운드가 없다" in o, o

    print("ok")


if __name__ == "__main__":
    main()
