#!/usr/bin/env python3
"""과거 PR 에서 (리뷰 코멘트 → 내 응답) 쌍을 모아 실제 분류 체계와 분포를 낸다.

usage:
  harvest_replies.py --repos <org>/<repo-a>,<org>/<repo-b>
                     [--top 30] [--out pairs.json] [--show-label real-defect]

목적은 pr-triage 의 분류 카테고리가 실측을 덮는지, 각 분류의 빈도가 얼마인지 재는 것.
**내 응답이 정답 라벨이다** — 과거 응답에서 판정 문구를 추출해 ground truth 로 쓴다.

한 응답에 여러 판정이 섞이는 경우가 흔하므로(예: "2번은 수정, 1번은 후속 티켓") 라벨은
집합으로 다룬다. 단일 라벨을 강제하면 분포가 왜곡된다.
"""
import argparse, json, re, subprocess, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))  # 설치 위치를 가정하지 않는다
from fetch_comments import is_bot  # 봇 판별은 스킬과 같은 규칙을 쓴다

BOT = "🤖"

# 구체적인 것부터 본다 — real-defect 는 표현이 넓어 마지막에 둔다.
LABELS = [
    ("duplicate",         r"\(중복\)|앞선 답변|이미 답변|앞서 (?:동일|같)|중복 지적"),
    ("already-applied",   r"이미\s*(?:반영|처리|해결|고침|수정|해소)|이전 커밋|이전 기준|"
                          r"직전 커밋에서|최신 커밋.{0,10}이전|해소됨|리팩터 직전"),
    ("out-of-scope",      r"범위 밖|후속 티켓|이관|별도 티켓|별도로 분리|다른 층위"),
    ("by-design",         r"by[- ]?design|그대로 두|의도된 동작|의도적으로"),
    ("counter-proposal",  r"유지 제안|채택하지|제안(?:하신)?(?:은|을).{0,12}(?:않|대신)|"
                          r"대안(?:을|으로)|다른 방식으로"),
    ("reviewer-mistaken", r"오독|잘못 (?:읽|보)|사실이 아니|해당하지 않습니다|"
                          r"누락은 발생하지 않"),
    # 리뷰 응답이 아닌 코멘트 — 진행 보고·자기 정정. 분류 대상이 아니다.
    ("status-report",     r"^\s*(?:정정합니다|.{0,40}push했습니다)|계측을? 보강|QA 체크리스트"),
    ("real-defect",       r"유효|맞습니다|맞고|정확[합하]|true positive|타당|"
                          r"확인해 (?:수정|반영)|수정했습니다|반영했습니다|"
                          r"✅\s*반영|—\s*반영|\*\*반영\*\*|반영해서 push|구조적으로.{0,6}정리|수렴"),
]


def gh_json(path):
    p = subprocess.run(["gh", "api", path, "--paginate", "--slurp"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return []
    try:
        return [i for page in json.loads(p.stdout) for i in page]
    except json.JSONDecodeError:
        return []


def clean(s, n=200):
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"```.*?```", " [코드] ", s, flags=re.S)
    return re.sub(r"\s+", " ", s).strip()[:n]


def label(body):
    """응답 본문에서 판정 라벨 집합을 뽑는다. 여러 판정이 섞이면 여러 개가 나온다."""
    return [name for name, pat in LABELS if re.search(pat, body, re.I)] or ["unlabeled"]


def pr_commits(repo, pr):
    return [{"sha": c["sha"][:8],
             "date": (c.get("commit", {}).get("committer") or {}).get("date") or "",
             "msg": (c.get("commit", {}).get("message") or "").splitlines()[0][:80]}
            for c in gh_json(f"repos/{repo}/pulls/{pr}/commits")]


def pr_comments(repo, pr, me):
    items = []
    for c in gh_json(f"repos/{repo}/issues/{pr}/comments"):
        items.append(dict(src="top-level", id=c["id"], author=c["user"]["login"],
                          at=c["created_at"], body=c.get("body") or "", url=c.get("html_url")))
    for c in gh_json(f"repos/{repo}/pulls/{pr}/comments"):
        items.append(dict(src="inline", id=c["id"], author=c["user"]["login"],
                          at=c["created_at"], body=c.get("body") or "",
                          path=c.get("path"), line=c.get("line"), url=c.get("html_url")))
    for r in gh_json(f"repos/{repo}/pulls/{pr}/reviews"):
        if (r.get("body") or "").strip():
            items.append(dict(src="review", id=r["id"], author=r["user"]["login"],
                              at=r.get("submitted_at") or "", body=r["body"],
                              state=r.get("state"), url=r.get("html_url")))
    items.sort(key=lambda x: (x["at"], x["id"]))
    commits = pr_commits(repo, pr)
    for it in items:
        it["bot"] = is_bot(it["body"], BOT)
        it["mine"] = it["author"] == me and not it["bot"]
        # 실제 스킬이 쓰는 신호 — 이 코멘트 이후 푸시된 커밋. 블라인드 문항에 반드시 넣는다.
        later = [c for c in commits if c["date"] and c["date"] > it["at"]]
        it["commits_after"] = [f"{c['sha']} {c['msg']}" for c in later]
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos", required=True, help="쉼표 구분 owner/name")
    ap.add_argument("--top", type=int, default=30, help="코멘트 많은 PR 상위 N개")
    ap.add_argument("--out", default=None)
    ap.add_argument("--show-label", default=None, help="이 라벨의 예시를 출력한다")
    ap.add_argument("--limit-scan", type=int, default=40, help="레포별로 훑을 최근 PR 수")
    a = ap.parse_args()

    me = subprocess.run(["gh", "api", "user", "-q", ".login"],
                        capture_output=True, text=True).stdout.strip()
    repos = [r.strip() for r in a.repos.split(",") if r.strip()]

    cand = []
    for repo in repos:
        nums = subprocess.run(["gh", "pr", "list", "--repo", repo, "--author", me,
                               "--state", "all", "--limit", str(a.limit_scan),
                               "--json", "number", "-q", ".[].number"],
                              capture_output=True, text=True).stdout.split()
        cand += [(repo, int(n)) for n in nums]
    print(f"후보 PR {len(cand)}개 · 나={me} · 코멘트 수집 중…", flush=True)

    per_pr = []
    for repo, pr in cand:
        items = pr_comments(repo, pr, me)
        if any(i["mine"] for i in items):
            per_pr.append((len(items), repo, pr, items))
    per_pr.sort(reverse=True, key=lambda x: x[0])
    per_pr = per_pr[:a.top]

    pairs, lab_count, multi, unl = [], Counter(), 0, []
    n_in = n_mine = 0
    by_repo = Counter()
    for _, repo, pr, items in per_pr:
        prev = []
        for it in items:
            if it["mine"]:
                n_mine += 1
                by_repo[repo] += 1
                labs = label(it["body"])
                lab_count.update(labs)
                multi += len(labs) > 1
                if labs == ["unlabeled"]:
                    unl.append((repo, pr, clean(it["body"], 120)))
                pairs.append(dict(repo=repo, pr=pr, reply_id=it["id"], reply=it["body"],
                                  labels=labs,
                                  reviews=[dict(id=r["id"], author=r["author"], src=r["src"],
                                                body=r["body"], path=r.get("path"),
                                                line=r.get("line"), url=r.get("url"),
                                                commits_after=r.get("commits_after", []))
                                           for r in prev]))
                prev = []
            else:
                n_in += 1
                prev.append(it)

    print(f"\nPR {len(per_pr)}개 · 리뷰 코멘트 {n_in}건 · 내 응답 {n_mine}건 "
          f"· 쌍 {len(pairs)}개 (복합 라벨 {multi}건)")
    print("레포별 응답:", dict(by_repo))
    print(f"\n{'라벨':22} 건수   비율")
    for k, v in lab_count.most_common():
        print(f"  {k:20} {v:4}   {v/max(n_mine,1)*100:5.1f}%")
    if unl:
        print(f"\n라벨 미분류 {len(unl)}건 — 카테고리 공백 후보:")
        for repo, pr, s in unl[:8]:
            print(f"  · {repo}#{pr}  {s}")

    if a.show_label:
        print(f"\n{'='*100}\n[{a.show_label}] 예시")
        for p in [x for x in pairs if a.show_label in x["labels"]][:6]:
            print(f"\n── {p['repo']}#{p['pr']} · 리뷰 {len(p['reviews'])}건 · {p['labels']}")
            for r in p["reviews"][:1]:
                print(f"   리뷰[{r['author']}] {clean(r['body'], 150)}")
            print(f"   응답 {clean(p['reply'], 200)}")

    if a.out:
        Path(a.out).write_text(json.dumps(
            dict(me=me, repos=repos, n_prs=len(per_pr), n_reviews=n_in, pairs=pairs),
            ensure_ascii=False, indent=2))
        print(f"\n저장: {a.out}")


if __name__ == "__main__":
    main()
