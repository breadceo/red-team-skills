#!/usr/bin/env python3
"""신규 리뷰 코멘트가 올라오면 한 줄씩 내보낸다. `Monitor` 도구에 물려 쓴다.

usage:
  watch_comments.py [--pr N] [--repo owner/name] [--cwd path]
                    [--interval 300] [--max-empty-hours 24]

한 줄 = 한 알림. 아무 일 없으면 아무것도 내보내지 않으므로 모델이 헛되게 깨지 않는다.
`/loop <간격>` 으로 주기 실행하는 것보다 이쪽이 낫다 — 코멘트가 없는 동안은 모델을 안 쓴다.

**알림 커서와 처리 커서를 따로 둔다.** 알림은 중복만 막으면 되고(`notified`),
처리 여부는 사람이 승인해 게시한 뒤에 표시한다(`triaged`). 알림 직후 세션이 죽어도
처리 대상이 사라지지 않아야 한다.
"""
import argparse, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_comments import detect, gh, gh_json, is_bot, load_state, save_state


def gh_auth_ok() -> bool:
    return subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0


def save_notified(cwd, pr, repo, seen):
    """저장 직전에 디스크를 다시 읽는다.

    감시는 몇 시간씩 살아 있고 그동안 `fetch_comments.py --mark-triaged` 가 같은 파일에
    `triaged` 를 쓴다. 시작 시점의 `st` 를 그대로 되쓰면 그 사이의 처리 표시가 통째로
    날아간다 — 실제로 PR #872 에서 45건이 16건으로 되돌아갔다.
    감시는 `notified` 에 추가만 하므로 합집합으로 병합하면 충분하다.
    """
    st = load_state(cwd, pr)
    st["notified"] = sorted(set(st.get("notified", [])) | set(seen))
    st["repo"] = repo
    save_state(cwd, pr, st)


def incoming(repo, pr, me, marker):
    out = []
    for c in gh_json(f"repos/{repo}/issues/{pr}/comments"):
        out.append(("top-level", c["id"], c["user"]["login"], c.get("body") or "",
                    c.get("html_url")))
    for c in gh_json(f"repos/{repo}/pulls/{pr}/comments"):
        out.append(("inline", c["id"], c["user"]["login"], c.get("body") or "",
                    c.get("html_url")))
    for r in gh_json(f"repos/{repo}/pulls/{pr}/reviews"):
        if (r.get("body") or "").strip():
            out.append(("review", r["id"], r["user"]["login"], r["body"], r.get("html_url")))
    res = []
    for src, cid, author, body, url in out:
        bot = is_bot(body, marker)
        if author == me and not bot:      # 내 응답은 알리지 않는다
            continue
        res.append((src, cid, author + ("(bot)" if bot else ""), body, url))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, default=None)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--bot-marker", default="🤖")
    ap.add_argument("--interval", type=int, default=300, help="폴링 간격(초). 기본 5분")
    ap.add_argument("--max-empty-hours", type=float, default=24,
                    help="이 시간 동안 신규가 없으면 종료한다 (무한 감시 방지)")
    ap.add_argument("--once", action="store_true",
                    help="한 번만 확인하고 끝낸다. `/loop` 로 주기 실행할 때·테스트할 때 쓴다")
    a = ap.parse_args()

    cwd = a.cwd or os.getcwd()
    repo, pr = detect(cwd, a.repo, a.pr)
    me = (gh("api", "user", "-q", ".login") or "").strip()

    st = load_state(cwd, pr)
    seen = set(st.get("notified", []))
    # 최초 실행에서는 기존 코멘트를 전부 알리지 않는다 — 알림 폭주를 막고,
    # 과거 코멘트는 `fetch_comments.py --new-only` 로 한 번에 본다.
    if not seen:
        seen = {cid for _, cid, _, _, _ in incoming(repo, pr, me, a.bot_marker)}
        save_notified(cwd, pr, repo, seen)
        print(f"[watch] {repo}#{pr} 감시 시작 — 기존 {len(seen)}건은 알리지 않는다 "
              f"(미처리분은 fetch_comments.py --new-only 로 확인)", flush=True)

    idle = 0.0
    while idle < a.max_empty_hours * 3600:
        try:
            fresh = [x for x in incoming(repo, pr, me, a.bot_marker) if x[1] not in seen]
        except (SystemExit, Exception):
            # gh 일시 실패로 감시를 죽이지 않는다 — 단, 인증 만료는 조용히 삼키면
            # "코멘트 없음" 으로 오인된 채 max-empty-hours 를 다 채운다(issue #12).
            if not gh_auth_ok():
                print(f"[watch] {repo}#{pr} gh 인증이 유효하지 않아 감시를 종료한다 — "
                      f"`gh auth login` 으로 재인증한 뒤 다시 시작한다.", flush=True)
                sys.exit(1)
            fresh = []
        for src, cid, who, body, url in fresh:
            seen.add(cid)
            head = " ".join(body.split())[:160]
            print(f"[pr-triage] {repo}#{pr} 신규 {src} 코멘트 · {who} · id={cid}\n"
                  f"  {head}\n  {url}", flush=True)
        if fresh:
            save_notified(cwd, pr, repo, seen)
            idle = 0.0
        else:
            idle += a.interval
        if a.once:
            return
        time.sleep(a.interval)

    print(f"[watch] {a.max_empty_hours}시간 동안 신규 코멘트가 없어 감시를 종료한다.", flush=True)


if __name__ == "__main__":
    main()
