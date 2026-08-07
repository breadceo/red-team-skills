#!/usr/bin/env python3
"""라운드들의 round.json 을 가로로 합쳐 배정별·축별 비용 대비 성과를 표로 만든다.

usage: report_usage.py [필터]     # 필터는 repo/브랜치 경로 조각 (예: ceo-client)

분자는 `classification == regression` 이다 — non-regression 지적을 세면 말 많은
모델(FP 높은)이 과대평가된다. 진짜 "값"(사람이 반영한 것)은 decisions.md 에 있으니
여기서는 존재 여부만 세고, 반영 수 조인은 필요해지면 붙인다.

cheap 축의 findings 0 은 낭비가 아니라 커버리지 보험이다 — 표를 축별로 갈라 보여주는
이유가 그것이고, 배정을 줄일지는 사람이 판단한다.
"""
import json, os, sys
from pathlib import Path

_HOME = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team"))
RUNS_ROOTS = (_HOME / "runs2", _HOME / "runs")  # v2 루트 + 아직 이전 안 된 구 루트


def rows(filt: str | None):
    """리뷰어 실행 단위로 평탄화. (구버전 라운드 수, 행 목록, decisions 있는 라운드 수, 라운드 수)"""
    legacy, out, decided, rounds = 0, [], 0, 0
    for root, rj in sorted((r, rj) for r in RUNS_ROOTS for rj in r.glob("*/*/*/round.json")):
        rel = rj.relative_to(root)
        if filt and filt not in str(rel.parent):
            continue
        try:
            d = json.loads(rj.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        rounds += 1
        decided += (rj.parent / "decisions.md").exists()
        if "assignments" not in d:  # 축별 배정 이전 라운드 — 집계 불가
            legacy += 1
            continue
        per_axis = {}
        for f in d.get("findings", []):
            k = f.get("reviewer", "?")
            per_axis.setdefault(k, [0, 0])
            per_axis[k][0] += 1
            per_axis[k][1] += f.get("classification") == "regression"
        for axis, a in d["assignments"].items():
            found, reg = per_axis.get(axis, (0, 0))
            t = a.get("tokens") or {}
            out.append({"axis": axis, "tier": a.get("tier", "?"),
                        "key": f"{a.get('engine', '?')}/{a.get('model') or 'default'}/{a.get('effort') or 'default'}",
                        "tokens": t.get("total", 0), "cost": t.get("cost_usd") or 0.0,
                        "costed": t.get("cost_usd") is not None,
                        "findings": found, "regression": reg})
    return legacy, out, decided, rounds


def table(title: str, groups: dict):
    print(f"\n{title}")
    w = max((len(k) for k in groups), default=10) + 2
    print(f"{'':{w}}{'실행':>5} {'tokens':>9} {'cost':>9} {'find':>5} {'reg':>4} {'$/reg':>7}")
    for k, g in sorted(groups.items(), key=lambda kv: -kv[1]['cost']):
        per = f"{g['cost'] / g['regression']:.2f}" if g["regression"] and g["costed"] else "—"
        cost = f"${g['cost']:.2f}" if g["costed"] else "—"
        print(f"{k:{w}}{g['n']:>5} {g['tokens'] / 1000:>8.0f}k {cost:>9} "
              f"{g['findings']:>5} {g['regression']:>4} {per:>7}")


def main():
    filt = sys.argv[1] if len(sys.argv) > 1 else None
    legacy, data, decided, rounds = rows(filt)
    if not data:
        sys.exit(f"집계할 라운드가 없다 ({' · '.join(map(str, RUNS_ROOTS))}"
                 + (f", 필터={filt}" if filt else "") + "). "
                 + (f"구버전 라운드 {legacy}건은 assignments 가 없어 집계 불가다." if legacy else ""))

    def group(keyf):
        gs = {}
        for r in data:
            g = gs.setdefault(keyf(r), {"n": 0, "tokens": 0, "cost": 0.0, "costed": False,
                                        "findings": 0, "regression": 0})
            g["n"] += 1
            g["tokens"] += r["tokens"]
            g["cost"] += r["cost"]
            g["costed"] = g["costed"] or r["costed"]
            g["findings"] += r["findings"]
            g["regression"] += r["regression"]
        return gs

    scope = f"필터={filt}, " if filt else ""
    print(f"라운드 {rounds}건 ({scope}decisions.md 있는 라운드 {decided}건"
          + (f", 구버전이라 집계 제외 {legacy}건" if legacy else "") + ")")
    table("배정별 (engine/model/effort)", group(lambda r: r["key"]))
    table("축별 (axis · tier)", group(lambda r: f"{r['axis']} · {r['tier']}"))
    print("\nreg = classification==regression. cheap 축의 0 은 커버리지 보험이다 — "
          "finding 수만으로 배정을 줄이지 않는다.")


if __name__ == "__main__":
    main()
