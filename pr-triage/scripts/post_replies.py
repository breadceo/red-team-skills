#!/usr/bin/env python3
"""승인된 응답 초안을 GitHub 에 올린다. **기본은 dry-run** — `--confirm` 없이는 올리지 않는다.

usage:
  post_replies.py --repo owner/name --pr N --replies <replies.json>   # 미리보기
  post_replies.py ... --confirm                                       # 실제 게시

replies.json 형식:
  [{"target_id": 12345, "source": "inline", "body": "...",
    "fps": ["<fp>", ...],       # 선택 — 이 회신이 다루는 봇 fingerprint 문자열 리스트.
                                #   fetch_comments 출력의 fps 에서 **fp 값만** 복사한다
                                #   (객체 배열을 통째로 붙이면 검증에서 거부된다).
                                #   단수 fp 필드는 없다 — 마커 2개짜리 코멘트에 답하는
                                #   회신은 두 fp 를 모두 다룬다.
    "reaction": "-1"},          # 선택 — 원 코멘트에 붙일 리액션("-1"|"+1").
   ...]                         #   fps 가 비어 있지 않은 항목에만 허용된다.

`source` 가 `inline` 이면 그 코멘트 스레드에 답글로 붙고, 그 외(top-level·review)는
PR 에 새 코멘트로 올린다 — top-level 코멘트와 리뷰 본문에는 스레드 답글 API 가 없다.

스크립트로 만든 이유는 스레드를 잘못 짚는 것을 막기 위해서다. 잘못된 반박이나 엉뚱한 위치의
답글은 리뷰어의 신뢰를 깎으므로, 되돌릴 수 없는 쓰기는 한 곳에서만 한다.

검증은 **게시 전에 전수**로 한다 — 하나라도 걸리면 게시 0건인 채 멈추므로 안전하다:
- **회신 body 가 is_bot 판정에 걸리면 거부한다.** 봇 본문을 raw 로 인용하면(fp 마커,
  선두 🤖, 선두 auto-review 서명 주석 — is_bot 의 전 분기) 내 회신이 '리뷰'로 오분류되어
  커서가 후퇴하고 fp_seq 가 부풀고 재게시 텔레메트리가 허위 기록되고 감시가 내 회신을
  알린다. 읽기 파싱과 같은 함수(fetch_comments 의 is_bot/fp_markers)를 import 해 쓴다 —
  게이트와 파싱의 규칙 동치가 이 봉쇄의 전제라 별도 구현을 금지한다.
- **reaction 은 fps 필수** — fp 는 hermes 류 봇 코멘트에만 존재하므로 사람 리뷰어 코멘트
  👎 는 이 **필수 필드 게이트**로 막힌다(fps 값 자체는 모델의 정직한 복사를 신뢰한다 —
  target 재조회로 마커 존재를 대조하지는 않는다).
- **source=review + reaction 거부** — 리뷰 본문에는 리액션 API 가 없다(404).
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

# fetch_comments 와 같은 규칙·같은 상태 파일을 쓴다 — 경로 파생(red-team 형제 탐색 포함)과
# 마커 스캔을 복제하지 않고 import 한다. 복제하면 두 스크립트가 다른 디렉토리·다른 판정을
# 조용히 가리킨다(watch_comments 가 같은 방식이다).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_comments
from fetch_comments import is_bot, load_state, merge_state, save_state  # noqa: F401

REACTIONS = ("-1", "+1")


def validate(items, marker):
    """게시 전 전수 검증. 걸리면 그 자리에서 exit — 부분 게시 상태를 만들지 않는다."""
    if not isinstance(items, list) or not items:
        sys.exit("replies.json 이 비었거나 리스트가 아니다.")
    for i, it in enumerate(items):
        body = str(it.get("body") or "")
        if not body.strip():
            sys.exit(f"[{i}] body 가 빈 항목이 있다: {it}")
        # inline 회신은 target_id 없이는 어느 스레드에 붙일지 post() 가 알 수 없다 —
        # 검증 없이 게시하면 그 항목만 조용히 잘못된 경로로 나가거나 KeyError 로 죽어
        # "일부만 게시"가 남는다. reaction 도 같은 target_id 필드로 원 코멘트를 가리키므로
        # 동일하게 게이트한다.
        if it.get("source") == "inline" and not it.get("target_id"):
            sys.exit(f"[{i}] inline 회신은 target_id 가 필수다")
        if it.get("reaction") and not it.get("target_id"):
            sys.exit(f"[{i}] reaction 은 target_id 가 필수다")
        if is_bot(body, marker):
            sys.exit(f"[{i}] 회신 본문이 봇 판정(is_bot)에 걸린다 — fp 마커·선두 {marker}·"
                     "봇 서명 주석을 본문에 넣지 않는다(fps 는 replies.json 필드로만 나른다). "
                     "이대로 올리면 내 회신이 '리뷰'로 오분류되어 커서가 후퇴한다.")
        # None 도 허용한다(빈 리스트로 취급) — replies.json 을 만드는 쪽이 "다루는 fp 없음"을
        # 명시적으로 null 로 적어도 막지 않는다. get(..., []) 는 키가 있고 값이 None 이면
        # None 을 그대로 돌려주므로 or [] 로 다시 접어야 한다.
        fps = it.get("fps") or []
        # 원소는 비어 있지 않은 문자열만 — fetch 출력의 객체 배열([{fp, fp_seq, …}])을
        # 통째로 복사하면 게시는 다 되고 일괄 기록 시점에 TypeError 로
        # "전부 게시·전부 미기록"이 된다. 게시 전에 타입으로 막는다.
        if not isinstance(fps, list) or not all(isinstance(f, str) and f.strip() for f in fps):
            sys.exit(f"[{i}] fps 는 비어 있지 않은 문자열 리스트여야 한다 — fetch 출력에서 "
                     f"fp 값만 복사한다(객체 배열 금지): {it.get('fps')!r}")
        r = it.get("reaction")
        if r is None:
            continue
        if r not in REACTIONS:
            sys.exit(f"[{i}] reaction 은 {REACTIONS} 만 허용된다: {r!r}")
        if not fps:
            sys.exit(f"[{i}] reaction 은 fps 가 있는 항목에만 허용된다 — fp 는 봇 코멘트에만 "
                     "존재하므로 이 필수 필드 게이트가 사람 리뷰어 코멘트 리액션을 막는다.")
        if it.get("source") == "review":
            sys.exit(f"[{i}] review 본문에는 리액션 API 가 없다(404) — reaction 을 뺀다.")


def post(repo, pr, item, confirm):
    """게시하고 결과를 구조체로 돌려준다 — fp_replies 기록에 id·html_url 이 필요하다."""
    if item.get("source") == "inline":
        path = f"repos/{repo}/pulls/{pr}/comments/{item['target_id']}/replies"
    else:
        path = f"repos/{repo}/issues/{pr}/comments"
    if not confirm:
        return {"ok": True, "dry": True, "msg": f"[dry-run] POST {path}"}
    p = subprocess.run(["gh", "api", "--method", "POST", path, "-f", f"body={item['body']}"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return {"ok": False, "msg": f"✗ 실패 {path}\n  {p.stderr.strip()}"}
    try:
        data = json.loads(p.stdout)
        return {"ok": True, "id": data.get("id"), "url": data.get("html_url"),
                "msg": f"✓ {data.get('html_url')}"}
    except json.JSONDecodeError:
        return {"ok": True, "id": None, "url": None, "msg": "✓ 게시됨"}


def react(repo, pr, item, confirm):
    """원 코멘트에 리액션을 단다. 실패는 회신 성공과 **별도로** 보고한다 (실물 검증된 경로)."""
    kind = "pulls" if item.get("source") == "inline" else "issues"
    path = f"repos/{repo}/{kind}/comments/{item['target_id']}/reactions"
    if not confirm:
        return f"[dry-run] 리액션 {item['reaction']} 예정 → POST {path}"
    p = subprocess.run(["gh", "api", "--method", "POST", path,
                        "-f", f"content={item['reaction']}"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return f"✗ 리액션 실패 {path}\n  {p.stderr.strip()}"
    return f"✓ 리액션 {item['reaction']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--replies", required=True)
    ap.add_argument("--cwd", default=None,
                    help="트리아지 상태 파일(fp_replies)을 파생할 저장소 경로. 생략 시 현재 위치")
    ap.add_argument("--bot-marker", default="🤖",
                    help="fetch_comments 와 같은 값이어야 한다 — 회신 본문이 이 마커 기준 "
                         "is_bot 판정에 걸리면 게시를 거부한다")
    ap.add_argument("--confirm", action="store_true", help="실제로 게시한다")
    a = ap.parse_args()

    cwd = a.cwd or os.getcwd()
    items = json.loads(Path(a.replies).read_text())
    validate(items, a.bot_marker)
    verified_repo_id = None
    claim_before = claim_after = None
    posted, react_fails, id_missing = [], [], []
    post_succeeded = False
    try:
        if a.confirm and fetch_comments.branch_dir is not None \
                and any(it.get("fps") for it in items):
            verified_repo_id, claim_before, claim_after = fetch_comments.claim_repo(
                cwd, a.pr, a.repo, with_snapshot=True)

        print(f"{a.repo}#{a.pr} · {len(items)}건 "
              f"{'게시한다' if a.confirm else '미리보기 (게시하지 않는다)'}\n")
        for i, it in enumerate(items):
            print(f"── [{i}] target={it.get('target_id')} source={it.get('source', 'top-level')}"
                  + (f" fps={it['fps']}" if it.get("fps") else ""))
            print("\n".join("   " + l for l in str(it["body"]).splitlines()[:12]))
            if len(str(it["body"]).splitlines()) > 12:
                print("   …")
            r = post(a.repo, a.pr, it, a.confirm)
            print("   " + r["msg"])
            if r["ok"] and not r.get("dry"):
                post_succeeded = True
            if r["ok"] and not r.get("dry") and it.get("fps"):
                if r.get("id"):
                    posted.append((it, r.get("id"), r.get("url")))
                else:
                    # 비-JSON 응답이라 id 를 못 얻은 성공 — reply_id=None 으로 기록하면
                    # 다음 fetch 의 fp_reply_url 조회가 깨진다. 리액션 실패처럼 회신 성공과
                    # 별도로 경고한다(기록 대신 보고).
                    id_missing.append(i)
            # 회신이 실패했으면 리액션도 보류한다 — 1회차 계약은 "전문 반박 + 👎 동시"라서,
            # 반박이 못 올라간 채 👎 만 붙으면 근거 없는 반응만 남는다.
            if r["ok"] and it.get("reaction"):
                msg = react(a.repo, a.pr, it, a.confirm)
                print("   " + msg)
                if msg.startswith("✗"):
                    react_fails.append((i, msg.splitlines()[0]))
            print()
    finally:
        if claim_after is not None and not post_succeeded:
            restored = fetch_comments.rollback_repo_claim(
                cwd, a.pr, claim_before, claim_after)
            if restored:
                print("repository claim 복원 — 성공한 게시가 없어 이전 상태로 되돌렸다.")
            else:
                print("⚠ 게시 중 다른 writer가 상태를 변경해 repository claim을 되돌리지 않았다.")
        # 상태 기록은 **모든 게시가 끝난 뒤 일괄**이되, 루프 중 미검증 예외로 여기 도달해도
        # 반드시 실행한다 — finally 가 아니면 이미 게시된 분(posted)이 fp_replies 에 하나도
        # 남지 않는 "전부 게시·전부 미기록"이 된다. keep-first 정책이라 여기서 다시 돌아도
        # 중복 기록 위험은 없다. branch_dir 가드가 먼저다: state_path() 는 red-team 미발견 시
        # sys.exit 하므로, 가드 없이 부르면 경고가 아니라 즉사가 된다.
        if posted:
            if fetch_comments.branch_dir is None:
                print("⚠ red-team 을 찾지 못해 fp_replies 를 기록하지 못했다 — 게시는 끝났다.\n"
                      "  다음 fetch 에서 이 fp 가 fp_replied 아님으로 보이면 과거 회신을 수동 "
                      "앵커하고 상태를 백필한다 (pr-triage/SKILL.md 7절).")
            else:
                fp_replies = {}
                for it, cid, url in posted:
                    for fp in it["fps"]:   # 항목의 fps **전원**을 기록한다
                        # 배치 안에서도 first-write-wins — merge_state 가 디스크 기존 키를
                        # 우선하므로(keep-first) 1회차 전문 반박 앵커가 요약으로 밀리지 않는다.
                        fp_replies.setdefault(fp, {"reply_id": cid, "reply_url": url})
                merge_state(cwd, a.pr, {"fp_replies": fp_replies, "repo": a.repo,
                                        "repo_id": verified_repo_id})
                print(f"fp_replies 기록 {len(fp_replies)}건 (keep-first — 이미 있는 fp 는 덮지 않는다)")

    if id_missing:
        print(f"⚠ id 없는 성공 응답(비-JSON) {len(id_missing)}건 — fp_replies 에 기록하지 못했다: "
              + ", ".join(f"[{i}]" for i in id_missing))
    if react_fails:
        print(f"⚠ 리액션 실패 {len(react_fails)}건 — 회신 게시와는 별개다. 필요하면 수동으로 단다:")
        for i, msg in react_fails:
            print(f"  [{i}] {msg}")
    if not a.confirm:
        print("실제 게시: 같은 명령에 --confirm 을 붙인다.")


if __name__ == "__main__":
    main()
