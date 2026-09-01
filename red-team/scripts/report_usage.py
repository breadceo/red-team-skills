#!/usr/bin/env python3
"""라운드들의 round.json 을 가로로 합쳐 배정별·축별 비용 대비 성과를 표로 만든다.

usage: report_usage.py [필터]     # 필터는 repo/브랜치 경로 조각 (예: ceo-client)

분자는 `classification == regression` 이다 — non-regression 지적을 세면 말 많은
모델(FP 높은)이 과대평가된다. 진짜 "값"(사람이 반영한 것)은 decisions.md 에 있으니
여기서는 존재 여부만 세고, 반영 수 조인은 필요해지면 붙인다.

cheap 축의 findings 0 은 낭비가 아니라 커버리지 보험이다 — 표를 축별로 갈라 보여주는
이유가 그것이고, 배정을 줄일지는 사람이 판단한다.

게이트 종류별 표는 **plan 라운드가 0인 namespace 를 눈에 띄게 하는 표**다 — 변경 단위
생략률이 아니다(라운드 수는 변경 수가 아니다). 그룹 키에 루트를 넣는 이유와 자동 병합을
하지 않는 이유는 `references/evidence.md`.
"""
import json, os, sys
from pathlib import Path

_HOME = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team"))
RUNS_ROOTS = (_HOME / "runs2", _HOME / "runs")  # v2 루트 + 아직 이전 안 된 구 루트

PLAN, CODE, OTHER = 0, 1, 2


def rows(filt: str | None):
    """리뷰어 실행 단위로 평탄화.

    (구버전 라운드 수, 행 목록, decisions 있는 라운드 수, 라운드 수,
     게이트별 카운트 {(루트, namespace): [plan, code, 미분류]}, 파싱 실패 수)
    앞 4개의 순서·인덱스는 기존 소비처(test_archive_runs.py)가 쓰므로 바꾸지 않는다.
    """
    legacy, out, decided, rounds, unparsed = 0, [], 0, 0, 0
    gates: dict[tuple[str, str], list[int]] = {}
    for root, rj in sorted((r, rj) for r in RUNS_ROOTS for rj in r.glob("*/*/*/round.json")):
        rel = rj.relative_to(root)
        if filt and filt not in str(rel.parent):
            continue
        # 라운드 종류·라운드 수는 경로만으로 센다 — 파싱 실패 라운드가 누락되면
        # 그 namespace 가 조용히 0% 로 보인다.
        rounds += 1
        decided += (rj.parent / "decisions.md").exists()
        name = rj.parent.name
        kind = PLAN if name.startswith("plan") else CODE if name.startswith("code") else OTHER
        gates.setdefault((root.name, rel.parts[0]), [0, 0, 0])[kind] += 1
        try:
            d = json.loads(rj.read_text())
        except (OSError, json.JSONDecodeError):
            unparsed += 1
            continue
        if "assignments" not in d:  # 축별 배정 이전 라운드 — 배정 집계 불가
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
    return legacy, out, decided, rounds, gates, unparsed


def table(title: str, groups: dict):
    print(f"\n{title}")
    w = max((len(k) for k in groups), default=10) + 2
    print(f"{'':{w}}{'실행':>5} {'tokens':>9} {'cost':>9} {'find':>5} {'reg':>4} {'$/reg':>7}")
    for k, g in sorted(groups.items(), key=lambda kv: -kv[1]['cost']):
        per = f"{g['cost'] / g['regression']:.2f}" if g["regression"] and g["costed"] else "—"
        cost = f"${g['cost']:.2f}" if g["costed"] else "—"
        print(f"{k:{w}}{g['n']:>5} {g['tokens'] / 1000:>8.0f}k {cost:>9} "
              f"{g['findings']:>5} {g['regression']:>4} {per:>7}")


def gate_table(gates: dict):
    """namespace 별 plan/code 라운드 수와 plan 비율.

    그룹 키는 (루트 이름, 최상위 namespace) 다 — namespace 문자열만 쓰면 `runs/x` 와
    `runs2/x` 가 자동 합산되고, 짧은 이름을 substring 필터로 반복 호출해 합계를 내면
    `ceo-client` 가 `zigbang__ceo-client` 까지 잡아 중복 집계된다. 자동 병합은 하지
    않는다 — suffix 일치는 소유 증거가 아니다.
    """
    labels = {(root, ns): f"{ns} (구)" if root == "runs" else ns for root, ns in gates}
    rated = [(k, g) for k, g in gates.items() if g[PLAN] + g[CODE]]
    if not rated:
        print("\n게이트 종류별 — 인식된 plan/code 라운드가 없다.")
        return
    print("\n게이트 종류별 (namespace · plan 라운드가 0인 곳을 본다)")
    w = max(len(labels[k]) for k, _ in rated) + 2
    print(f"{'':{w}}{'plan':>5} {'code':>5} {'plan%':>7}")
    for k, g in sorted(rated, key=lambda kv: (kv[1][PLAN] / (kv[1][PLAN] + kv[1][CODE]),
                                              -(kv[1][PLAN] + kv[1][CODE]))):
        pct = g[PLAN] / (g[PLAN] + g[CODE]) * 100
        print(f"{labels[k]:{w}}{g[PLAN]:>5} {g[CODE]:>5} {pct:>6.0f}%")
    tp, tc = (sum(g[i] for _, g in rated) for i in (PLAN, CODE))
    print(f"{'합계':{w - 2}}{tp:>5} {tc:>5} {tp / (tp + tc) * 100:>6.0f}%")
    print("분모 = 인식된 plan/code 라운드(파싱 실패 포함) — 헤더·배정 표와 분모가 다르다.\n"
          "`(구)` 는 runs/ 출처다. 자동 병합하지 않으므로 한 저장소가 두 개 이상의 행으로\n"
          "갈릴 수 있고, 마커 없는 짧은 키도 원격이 없어 fallback 된 v2 키일 수 있다 —\n"
          "합산은 사람이 확인하고 한다. **변경 단위 생략률이 아니다** (라운드 수 ≠ 변경 수).")


def main():
    filt = sys.argv[1] if len(sys.argv) > 1 else None
    legacy, data, decided, rounds, gates, unparsed = rows(filt)
    if not rounds:
        sys.exit(f"집계할 라운드가 없다 ({' · '.join(map(str, RUNS_ROOTS))}"
                 + (f", 필터={filt}" if filt else "") + ").")

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

    other = sum(g[OTHER] for g in gates.values())
    notes = [f"decisions.md 있는 라운드 {decided}건",
             f"배정 집계 {rounds - unparsed - legacy}건"]
    if unparsed:
        notes.append(f"파싱 실패 {unparsed}건")
    if legacy:
        notes.append(f"구버전이라 배정 집계 제외 {legacy}건")
    if other:
        notes.append(f"plan/code 아닌 라운드 {other}건")
    print(f"라운드 {rounds}건 ("
          + (f"필터={filt}, " if filt else "") + ", ".join(notes) + ")")
    if data:
        table("배정별 (engine/model/effort)", group(lambda r: r["key"]))
        table("축별 (axis · tier)", group(lambda r: f"{r['axis']} · {r['tier']}"))
        print("\nreg = classification==regression. cheap 축의 0 은 커버리지 보험이다 — "
              "finding 수만으로 배정을 줄이지 않는다.")
    gate_table(gates)


if __name__ == "__main__":
    main()
