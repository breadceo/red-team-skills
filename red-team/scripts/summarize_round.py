#!/usr/bin/env python3
"""round.json 을 채점 가능한 형태로 요약한다 — 기계적 지표 + findings 일람.

recall(어느 골든 결함에 대응하나)은 의미 판정이라 사람/모델이 라벨한다.
이 스크립트는 그 라벨을 붙이기 위한 표를 만든다.

usage: summarize_round.py <round.json> [<round.json> ...]
"""
import json, sys
from pathlib import Path


def summarize(p: Path):
    d = json.loads(p.read_text())
    fs = d.get("findings", [])
    reg = [f for f in fs if f.get("classification") == "regression"]
    print(f"\n{'='*100}\n{p.parent.parent.name}/{p.parent.name}  —  verdict={d.get('verdict')}")
    print(f"리뷰어별 verdict: {d.get('reviewers')}")
    parse_fail = [r for r, v in d.get("reviewers", {}).items() if v == "PARSE-FAIL"]
    print(f"findings {len(fs)}건 · regression {len(reg)} (P1 {sum(1 for f in reg if f.get('severity')=='P1')}"
          f" / P2 {sum(1 for f in reg if f.get('severity')=='P2')})"
          f" · non-regression {len(fs)-len(reg)} · PARSE-FAIL {parse_fail or '없음'}")
    print(f"{'-'*100}")
    print(f"{'#':<3} {'reviewer':<20} {'sev':<4} {'class':<13} {'conf':<7} file")
    for i, f in enumerate(fs):
        print(f"{i:<3} {f.get('reviewer',''):<20} {f.get('severity',''):<4} "
              f"{f.get('classification',''):<13} {f.get('confidence',''):<7} {f.get('file','')}")
        print(f"      claim: {f.get('claim','')}")
    return d


if __name__ == "__main__":
    for a in sys.argv[1:]:
        summarize(Path(a))
