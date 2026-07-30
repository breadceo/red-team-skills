#!/usr/bin/env python3
"""블라인드 분류 결과를 채점한다.

usage:
  score.py --answers answers.json --predictions preds.json

preds.json:
  [{"qid": 0, "labels": ["real-defect"], "note": "…"}, ...]

정답이 라벨 집합이므로 정확도를 두 가지로 낸다.
  strict  — 집합이 완전히 같음
  overlap — 하나라도 겹침 (복합 판정 응답에서 주된 판정을 맞혔는지)

그리고 **안전 지표**를 따로 본다: 정답이 `real-defect` 인데 반박 계열
(already-applied / duplicate / by-design / out-of-scope / reviewer-mistaken)로 분류한 것.
이 오류는 맞는 지적을 밀어내 리뷰어 신뢰를 깎으므로 다른 오류보다 비싸다.
"""
import argparse, json
from collections import Counter
from pathlib import Path

REBUTTAL = {"already-applied", "duplicate", "by-design", "out-of-scope", "reviewer-mistaken"}
# 이 티켓에서 고치지 않고 미루는 판정들 — 놓치면 스코프 크립이 된다
DEFER = {"out-of-scope", "counter-proposal", "by-design"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", default=None)
    ap.add_argument("--predictions", default=None)
    ap.add_argument("--log", default=None,
                    help="~/.red-team/pr-triage-log.jsonl 을 채점한다 (실사용 누적분). "
                         "확인 게이트가 남긴 라벨이라 오염이 없다")
    a = ap.parse_args()

    if a.log:
        log = [json.loads(l) for l in Path(a.log).read_text().splitlines() if l.strip()]
        if not log:
            raise SystemExit(f"{a.log} 이 비었다 — 아직 확인 게이트 기록이 없다.")
        gold = {i: {"labels": x["confirmed"]} for i, x in enumerate(log)}
        preds = [{"qid": i, "labels": x["predicted"]} for i, x in enumerate(log)]
        from collections import Counter as _C
        print(f"실사용 누적 {len(log)}건 채점 "
              f"(교정 {sum(1 for x in log if x.get('corrected'))}건) · "
              f"PR {len(set((x.get('repo'), x.get('pr')) for x in log))}개")
        print("확정 라벨 분포:", dict(_C(l for x in log for l in x["confirmed"])), "\n")
    else:
        if not (a.answers and a.predictions):
            raise SystemExit("--log 또는 (--answers + --predictions) 가 필요하다.")
        gold = {x["qid"]: x for x in json.loads(Path(a.answers).read_text())["answers"]}
        preds = json.loads(Path(a.predictions).read_text())
        preds = preds["predictions"] if isinstance(preds, dict) else preds

    strict = overlap = 0
    unsafe, creep, missed, confusion = [], [], [], Counter()
    for p in preds:
        g = gold.get(p["qid"])
        if not g:
            continue
        gl, pl = set(g["labels"]), set(p["labels"])
        strict += gl == pl
        overlap += bool(gl & pl)
        confusion[(",".join(sorted(gl)), ",".join(sorted(pl)))] += 1
        if "real-defect" in gl and not (pl & {"real-defect"}) and (pl & REBUTTAL):
            unsafe.append((p["qid"], sorted(gl), sorted(pl)))
        # 반대 방향 오류 — 미룰 것을 고칠 것으로 본다. 수정이 자동인 loop 에서는
        # 이 티켓 범위 밖 결함까지 고치기 시작해 PR 이 비대해진다(스코프 크립).
        if (gl & DEFER) and not (pl & DEFER):
            creep.append((p["qid"], sorted(gl), sorted(pl)))
        if not (gl & pl):
            missed.append((p["qid"], sorted(gl), sorted(pl)))

    n = len([p for p in preds if p["qid"] in gold])
    print(f"채점 {n}문항")
    print(f"  strict  {strict}/{n}  ({strict/max(n,1)*100:.0f}%)   라벨 집합 완전 일치")
    print(f"  overlap {overlap}/{n}  ({overlap/max(n,1)*100:.0f}%)   주된 판정 일치")
    print(f"\n[안전 A] 맞는 지적을 반박으로 분류: {len(unsafe)}건"
          + ("  ← 0 이어야 한다" if not unsafe else "  ← 리뷰어 신뢰를 깎는다"))
    for qid, gl, pl in unsafe:
        print(f"    q{qid}: 정답 {gl} → 예측 {pl}")
    print(f"\n[안전 B] 미룰 것을 고칠 것으로 분류: {len(creep)}/{n}건"
          + ("" if not creep else "  ← 수정 자동 loop 에서 스코프 크립이 된다"))
    for qid, gl, pl in creep:
        print(f"    q{qid}: 정답 {gl} → 예측 {pl}")
    if missed:
        print(f"\n완전 불일치 {len(missed)}건:")
        for qid, gl, pl in missed:
            print(f"    q{qid}: 정답 {gl} → 예측 {pl}")
    print("\n혼동 (정답 → 예측):")
    for (g, p), c in confusion.most_common(12):
        print(f"  {c:2}  {g:34} → {p}")


if __name__ == "__main__":
    main()
