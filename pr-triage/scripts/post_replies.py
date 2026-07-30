#!/usr/bin/env python3
"""승인된 응답 초안을 GitHub 에 올린다. **기본은 dry-run** — `--confirm` 없이는 올리지 않는다.

usage:
  post_replies.py --repo owner/name --pr N --replies <replies.json>   # 미리보기
  post_replies.py ... --confirm                                       # 실제 게시

replies.json 형식:
  [{"target_id": 12345, "source": "inline", "body": "..."}, ...]

`source` 가 `inline` 이면 그 코멘트 스레드에 답글로 붙고, 그 외(top-level·review)는
PR 에 새 코멘트로 올린다 — top-level 코멘트와 리뷰 본문에는 스레드 답글 API 가 없다.

스크립트로 만든 이유는 스레드를 잘못 짚는 것을 막기 위해서다. 잘못된 반박이나 엉뚱한 위치의
답글은 리뷰어의 신뢰를 깎으므로, 되돌릴 수 없는 쓰기는 한 곳에서만 한다.
"""
import argparse, json, subprocess, sys
from pathlib import Path


def post(repo, pr, item, confirm):
    body = item["body"]
    if item.get("source") == "inline":
        path = f"repos/{repo}/pulls/{pr}/comments/{item['target_id']}/replies"
    else:
        path = f"repos/{repo}/issues/{pr}/comments"
    if not confirm:
        return f"[dry-run] POST {path}"
    p = subprocess.run(["gh", "api", "--method", "POST", path, "-f", f"body={body}"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return f"✗ 실패 {path}\n  {p.stderr.strip()}"
    try:
        return f"✓ {json.loads(p.stdout).get('html_url')}"
    except json.JSONDecodeError:
        return "✓ 게시됨"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--replies", required=True)
    ap.add_argument("--confirm", action="store_true", help="실제로 게시한다")
    a = ap.parse_args()

    items = json.loads(Path(a.replies).read_text())
    if not isinstance(items, list) or not items:
        sys.exit("replies.json 이 비었거나 리스트가 아니다.")
    for it in items:
        if "body" not in it or not str(it["body"]).strip():
            sys.exit(f"body 가 빈 항목이 있다: {it}")

    print(f"{a.repo}#{a.pr} · {len(items)}건 "
          f"{'게시한다' if a.confirm else '미리보기 (게시하지 않는다)'}\n")
    for i, it in enumerate(items):
        print(f"── [{i}] target={it.get('target_id')} source={it.get('source', 'top-level')}")
        print("\n".join("   " + l for l in str(it["body"]).splitlines()[:12]))
        if len(str(it["body"]).splitlines()) > 12:
            print("   …")
        print("   " + post(a.repo, a.pr, it, a.confirm) + "\n")
    if not a.confirm:
        print("실제 게시: 같은 명령에 --confirm 을 붙인다.")


if __name__ == "__main__":
    main()
