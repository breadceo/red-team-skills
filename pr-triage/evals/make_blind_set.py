#!/usr/bin/env python3
"""분류 정확도 측정용 블라인드 표본을 만든다.

usage:
  make_blind_set.py --pairs pairs.json --n 24 --out blind.json [--seed 7]

**정답(내 과거 응답)을 가린 문항만 내보낸다.** 응답을 보면서 분류하면 정답을 베끼는 것이라
정확도가 아니라 독해력을 재게 된다. 정답은 `answers.json` 에 따로 저장해 두고 채점 때 합친다.

표본은 라벨별로 고르게 뽑는다(stratified) — 자연 분포대로 뽑으면 real-defect 가 절반을 넘어
드문 분류의 정확도를 못 잰다. 대신 '분포'는 harvest 쪽 수치를 쓴다.
"""
import argparse, json, random, re
from collections import defaultdict
from pathlib import Path


def clean(s, n=1400):
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    return s.strip()[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--exclude-prs", default="",
                    help="정답을 이미 본 PR 번호 (쉼표). 오염된 문항을 표본에서 뺀다 — "
                         "정답을 본 채로 분류하면 정확도가 아니라 기억력을 재게 된다")
    ap.add_argument("--out", required=True, help="문항 (정답 없음)")
    ap.add_argument("--answers", default=None, help="정답 (기본: <out> 옆 answers.json)")
    a = ap.parse_args()

    data = json.loads(Path(a.pairs).read_text())
    # 리뷰 코멘트가 붙어 있는 쌍만 문항이 된다 — 리뷰 없이 올린 내 코멘트는 분류 대상이 아니다
    skip = {int(x) for x in re.findall(r"\d+", a.exclude_prs)}
    usable = [p for p in data["pairs"]
              if p["reviews"] and p["labels"] != ["unlabeled"] and p["pr"] not in skip]
    if skip:
        dropped = len([p for p in data["pairs"] if p["pr"] in skip])
        print(f"오염 제외: PR {sorted(skip)} → 쌍 {dropped}개 제외, 남은 후보 {len(usable)}개")
    by_label = defaultdict(list)
    for p in usable:
        for l in p["labels"]:
            by_label[l].append(p)

    rnd = random.Random(a.seed)
    picked, seen = [], set()
    labels = sorted(by_label, key=lambda l: len(by_label[l]))  # 드문 라벨부터 채운다
    while len(picked) < a.n:
        added = False
        for l in labels:
            pool = [p for p in by_label[l] if p["reply_id"] not in seen]
            if not pool or len(picked) >= a.n:
                continue
            p = rnd.choice(pool)
            seen.add(p["reply_id"])
            picked.append(p)
            added = True
        if not added:
            break

    questions, answers = [], []
    for i, p in enumerate(picked):
        questions.append(dict(
            qid=i, repo=p["repo"], pr=p["pr"],
            reviews=[dict(author=r["author"], src=r["src"], path=r.get("path"),
                          line=r.get("line"), body=clean(r["body"]), url=r.get("url"),
                          # 실제 스킬은 이 신호를 갖는다 — 빼면 already-applied 판정이 불리해진다
                          commits_after=r.get("commits_after", []))
                     for r in p["reviews"]]))
        answers.append(dict(qid=i, repo=p["repo"], pr=p["pr"], reply_id=p["reply_id"],
                            labels=p["labels"], reply=clean(p["reply"], 2500)))

    out = Path(a.out)
    out.write_text(json.dumps(dict(n=len(questions), questions=questions),
                              ensure_ascii=False, indent=2))
    ans = Path(a.answers) if a.answers else out.parent / "answers.json"
    ans.write_text(json.dumps(dict(answers=answers), ensure_ascii=False, indent=2))

    from collections import Counter
    print(f"문항 {len(questions)}개 → {out}\n정답    → {ans}  (채점 전에 열지 않는다)")
    print("라벨 분포:", dict(Counter(l for x in answers for l in x["labels"])))
    print("레포 분포:", dict(Counter(q["repo"] for q in questions)))


if __name__ == "__main__":
    main()
