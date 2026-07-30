#!/usr/bin/env python3
"""PR 리뷰 코멘트를 시간순으로 모아 분류 대상을 골라낸다.

usage:
  fetch_comments.py [--pr N] [--repo owner/name] [--cwd path]
                    [--bot-marker 🤖] [--out <path>]

이 스크립트는 **분류하지 않는다.** 수집·정렬·미응답 후보 표시까지만 하고,
"어떤 지적이 아직 열려 있나"는 모델이 본문을 읽고 판정한다 —
봇이 번호 매긴 여러 지적 중 일부만 남는 경우가 흔해서 기계적으로는 못 가른다.

주의: 리뷰 봇이 **작성자 계정으로** 코멘트를 올리는 경우가 있다.
그래서 `author == me` 만으로는 리뷰와 내 응답이 갈리지 않는다 — 봇 마커를 함께 본다.
"""
import argparse, json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

# `red-team` 은 이 스킬의 **필수 의존**이다(선택 사항이 아니다). 경로 파생을 복제하지 않고
# import 하는 이유는, 두 스킬이 같은 `~/.red-team/runs/<repo>/<branch>/` 를 읽고 쓰기 때문이다 —
# 규칙이 갈라지면 트리아지 커서와 red-team 라운드가 서로 다른 디렉토리를 가리키고,
# 그 순간 "이미 처리한 코멘트" 목록이 조용히 사라진다.
# 설치 위치를 가정하지 않는다 — 마켓플레이스는 전역(`~/.claude/skills/`)과
# 프로젝트 로컬(`<repo>/.claude/skills/`) 양쪽에 설치한다. 형제 디렉토리를 먼저 보는 것이
# 두 경우를 한 번에 덮는다(이 파일이 <skills>/pr-triage/scripts/ 에 있으므로).
RED_TEAM_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "red-team" / "scripts",
    Path.home() / ".claude" / "skills" / "red-team" / "scripts",
    Path.cwd() / ".claude" / "skills" / "red-team" / "scripts",
]
for _c in RED_TEAM_CANDIDATES:
    if (_c / "run_round.py").exists():
        sys.path.insert(0, str(_c))
        break
try:
    from run_round import branch_dir  # 경로 파생은 red-team 과 한 규칙을 쓴다
except ImportError:
    branch_dir = None

RED_TEAM_MISSING = (
    "`red-team` 스킬이 필요하다 — pr-triage 는 그 위에서 돈다.\n"
    "  찾아본 곳:\n"
    + "\n".join(f"    {c}" for c in RED_TEAM_CANDIDATES)
    + "\n  마켓플레이스에서 받았다면 `red-team` 도 같은 위치에 설치한다"
    "(https://github.com/breadceo/red-team-skills)."
)


# ── 트리아지 상태 ──────────────────────────────────────────────────────────
# loop 로 돌 때 "무엇을 이미 처리했나" 를 알아야 한다. 커서가 없으면 매 폴링마다
# 같은 코멘트를 다시 분류하고, 같은 회신을 또 올릴 위험이 생긴다.
#   triaged  — 분류·처리가 끝난 코멘트 id (사람이 승인해 게시까지 끝난 것)
#   notified — 감시가 이미 알린 id (알림 중복만 막는다. triaged 와 별개로 둔다 —
#              알림 직후 세션이 죽어도 처리 대상이 사라지지 않아야 한다)

def state_path(cwd: str, pr: int) -> Path:
    if branch_dir is None:
        sys.exit(RED_TEAM_MISSING)
    return branch_dir(cwd) / f"pr-{pr}-triage.json"


def load_state(cwd: str, pr: int) -> dict:
    p = state_path(cwd, pr)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return {"pr": pr, "triaged": [], "notified": []}


def save_state(cwd: str, pr: int, st: dict) -> Path:
    p = state_path(cwd, pr)
    p.parent.mkdir(parents=True, exist_ok=True)
    st["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    return p


# 분류 기록은 **PR 경계를 넘어** 누적돼야 한다 — 한 PR 에서는 임계값에 도달하지 않는다.
# append-only 로그라서 last.json 같은 '가변 포인터'와 다르다: 낡을 진실이 없고,
# 과거 분류는 다른 곳에서 파생할 수도 없다. 그래서 전역 파일이 정당하다.
LOG = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team")) / "pr-triage-log.jsonl"
# 검토를 물을 시점 — 문서에 적어두면 아무도 세지 않으므로 기록 시점에 스크립트가 알린다.
REVIEW_AT_TOTAL = (30, 100)     # 30: 안전 A·B 비율 / 100: 드문 라벨
REVIEW_AT_CORRECTED = (5, 20)   # 교정 5건이면 구체적 실패 사례가 모인다


def log_count():
    if not LOG.exists():
        return 0, 0
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    return len(rows), sum(1 for r in rows if r.get("corrected"))


def _sections(text: str):
    """`## ` 단위로 (heading, body) 로 쪼갠다."""
    parts = re.split(r"^(## .+)$", text, flags=re.M)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]


def is_bot(body: str, marker: str) -> bool:
    """리뷰 봇이 올린 코멘트인가.

    봇이 **작성자 계정으로** 올리는 경우가 있어 작성자만으로는 못 가른다.
    형식도 레포마다 다르다 — 어떤 레포는 본문이 바로 마커로 시작하지만
    어떤 레포는 `<!-- …-pr-auto-review:… -->` HTML 주석을 먼저 붙인다.
    앞쪽 HTML 주석을 걷어낸 뒤 마커를 보고, 주석 자체가 봇 서명이면 그것으로도 판정한다.
    """
    if re.match(r"\s*<!--\s*[\w./#-]*(?:auto-review|autoreview|bot)\b", body, re.I):
        return True
    head = re.sub(r"^\s*(?:<!--.*?-->\s*)+", "", body, flags=re.S)
    return head.lstrip().startswith(marker)


def gh(*args, check=True):
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0:
        if check:
            sys.exit(f"gh {' '.join(args[:3])} 실패:\n{p.stderr.strip()}")
        return None
    return p.stdout


def gh_json(path):
    out = gh("api", path, "--paginate", "--slurp")
    try:
        pages = json.loads(out)
    except json.JSONDecodeError:
        sys.exit(f"gh api {path} 응답을 파싱할 수 없다.")
    return [item for page in pages for item in page]


def detect(cwd, repo, pr):
    if not repo:
        url = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"],
                             capture_output=True, text=True).stdout.strip()
        m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
        if not m:
            sys.exit(f"origin 에서 저장소를 못 찾았다: {url or '(origin 없음)'}")
        repo = m.group(1)
    if not pr:
        out = gh("pr", "view", "--repo", repo, "--json", "number", "-q", ".number", check=False)
        if not out:
            sys.exit("현재 브랜치의 열린 PR 을 못 찾았다 — --pr 로 번호를 준다.")
        pr = int(out.strip())
    return repo, int(pr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, default=None)
    ap.add_argument("--repo", default=None, help="owner/name (생략 시 origin)")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--bot-marker", default="🤖",
                    help="이 문자열로 시작하는 코멘트는 작성자 계정이어도 '리뷰'로 본다")
    ap.add_argument("--out", default=None, help="JSON 저장 경로 (생략 시 stdout 요약만)")
    ap.add_argument("--new-only", action="store_true",
                    help="아직 처리하지 않은 리뷰 코멘트만 보여준다 (loop 용)")
    ap.add_argument("--mark-triaged", default=None,
                    help="처리 완료로 표시할 코멘트 id 목록 (쉼표 구분). 게시 후에 호출한다")
    ap.add_argument("--log-classification", default=None,
                    help="확인 게이트 결과를 커서 파일에 누적한다. JSON 배열 또는 파일 경로: "
                         '[{"id":123,"predicted":["real-defect"],'
                         '"confirmed":["out-of-scope","real-defect"],"note":"…"}]. '
                         "**정답이 예측 뒤에 생기므로 오염이 구조적으로 불가능한 라벨이다**")
    ap.add_argument("--show-scope", action="store_true",
                    help="context.md 의 '스코프 밖'·'후속 티켓' 절을 그대로 출력한다. "
                         "분류 확인 화면에 붙일 입력이다 — 대조는 사람이 한다")
    a = ap.parse_args()

    cwd = a.cwd or os.getcwd()
    repo, pr = detect(cwd, a.repo, a.pr)

    if a.log_classification:
        raw = a.log_classification
        if not raw.lstrip().startswith("["):
            raw = Path(raw).read_text()
        items = json.loads(raw)
        before_n, before_c = log_count()
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            for it in items:
                pred, conf = sorted(it.get("predicted", [])), sorted(it.get("confirmed", []))
                f.write(json.dumps({**it, "repo": repo, "pr": pr, "predicted": pred,
                                    "confirmed": conf, "corrected": pred != conf,
                                    "at": stamp}, ensure_ascii=False) + "\n")
        after_n, after_c = log_count()
        print(f"분류 기록 {len(items)}건 → {LOG}")
        print(f"  누적 {after_n}건 · 교정 {after_c}건 "
              f"({after_c/max(after_n,1)*100:.0f}%)")
        # 임계값을 이번 기록에서 넘었으면 알린다. 상태를 따로 두지 않아도 정확히 한 번 발동한다.
        hits = [f"교정 {t}건" for t in REVIEW_AT_CORRECTED if before_c < t <= after_c]
        hits += [f"누적 {t}건" for t in REVIEW_AT_TOTAL if before_n < t <= after_n]
        if hits:
            print(f"\n▶ 검토 시점에 도달했다 ({', '.join(hits)}). 사용자에게 재측정·반영 여부를 묻는다:\n"
                  f"  python3 {Path(__file__).resolve().parent.parent}/evals/score.py --log {LOG}")
        return

    if a.mark_triaged:
        ids = [int(x) for x in re.findall(r"\d+", a.mark_triaged)]
        st = load_state(cwd, pr)
        st["triaged"] = sorted(set(st.get("triaged", [])) | set(ids))
        st["repo"] = repo
        print(f"처리 완료 표시 {len(ids)}건 → 누적 {len(st['triaged'])}건\n{save_state(cwd, pr, st)}")
        return

    me = (gh("api", "user", "-q", ".login") or "").strip()

    items = []
    for c in gh_json(f"repos/{repo}/pulls/{pr}/comments"):
        items.append({"source": "inline", "id": c["id"], "author": c["user"]["login"],
                      "created_at": c["created_at"], "body": c.get("body") or "",
                      "path": c.get("path"), "line": c.get("line"),
                      "in_reply_to": c.get("in_reply_to_id"), "url": c.get("html_url")})
    for c in gh_json(f"repos/{repo}/issues/{pr}/comments"):
        items.append({"source": "top-level", "id": c["id"], "author": c["user"]["login"],
                      "created_at": c["created_at"], "body": c.get("body") or "",
                      "url": c.get("html_url")})
    for r in gh_json(f"repos/{repo}/pulls/{pr}/reviews"):
        if (r.get("body") or "").strip():
            items.append({"source": "review", "id": r["id"], "author": r["user"]["login"],
                          "created_at": r.get("submitted_at") or "", "body": r["body"],
                          "state": r.get("state"), "url": r.get("html_url")})

    items.sort(key=lambda x: (x["created_at"], x["id"]))
    for it in items:
        bot = is_bot(it["body"], a.bot_marker)
        it["bot_marker"] = bot
        # 내 응답 = 내 계정이고 봇 마커가 없는 것. 봇이 내 계정으로 올리는 경우를 가른다.
        it["is_my_reply"] = (it["author"] == me and not bot)
        it["is_incoming"] = not it["is_my_reply"]

    # 내 마지막 응답 이후의 incoming 은 확실히 미응답이다.
    # 그 앞쪽은 부분적으로만 답했을 수 있어 모델이 본문을 읽고 판정한다.
    last_reply = max((i for i, it in enumerate(items) if it["is_my_reply"]), default=-1)
    for i, it in enumerate(items):
        it["after_my_last_reply"] = it["is_incoming"] and i > last_reply

    # 코멘트 이후 푸시된 커밋 수. 실측에서 '이미 반영됨' 응답의 주된 원인이
    # "리뷰가 낡은 커밋을 봤다" 였다(6개 PR 35응답 중 5건). 기계적으로 셀 수 있으니 세어둔다.
    commits = [{"sha": c["sha"][:8],
                "date": (c.get("commit", {}).get("committer") or {}).get("date") or "",
                "msg": (c.get("commit", {}).get("message") or "").splitlines()[0]}
               for c in gh_json(f"repos/{repo}/pulls/{pr}/commits")]
    for it in items:
        later = [c for c in commits if c["date"] and c["date"] > it["created_at"]]
        it["commits_after"] = [c["sha"] for c in later]

    # 이미 처리한 코멘트를 표시한다. --new-only 면 그것들을 목록에서 뺀다.
    st = load_state(cwd, pr) if branch_dir else {"triaged": []}
    done = set(st.get("triaged", []))
    for it in items:
        it["triaged"] = it["id"] in done
    if a.new_only:
        items = [it for it in items if it["is_incoming"] and not it["triaged"]]

    # red-team 의사결정 기록이 있으면 위치를 알려준다 (분류 근거로 쓴다)
    record = None
    try:
        from resume import latest_round  # noqa: E402
        base = branch_dir(cwd)
        if (found := latest_round(base)):
            rd = found[0]
            record = {"round_dir": str(rd),
                      "context_md": str(rd / "context.md") if (rd / "context.md").exists() else None,
                      "decisions_md": str(rd / "decisions.md") if (rd / "decisions.md").exists() else None}
        elif base.exists():
            record = {"round_dir": None, "branch_dir": str(base)}
    except Exception:
        pass  # red-team 기록이 없어도 동작해야 한다

    result = {"repo": repo, "pr": pr, "me": me, "bot_marker": a.bot_marker,
              "record": record, "commits": commits, "comments": items}
    out_path = Path(a.out) if a.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    n_in = sum(1 for i in items if i["is_incoming"])
    n_open = sum(1 for i in items if i["after_my_last_reply"])
    n_todo = sum(1 for i in items if i["is_incoming"] and not i["triaged"])
    print(f"{repo}#{pr} · 나={me} · 코멘트 {len(items)}건 "
          f"({'미처리 리뷰만' if a.new_only else f'리뷰 {n_in} / 내 응답 {len(items)-n_in}'})")
    print(f"미처리 리뷰 코멘트: {n_todo}건 · 내 마지막 응답 이후: {n_open}건")
    if record and record.get("round_dir"):
        print(f"의사결정 기록: {record['round_dir']}")
        print(f"  context.md   : {'있음' if record['context_md'] else '없음'}")
        print(f"  decisions.md : {'있음' if record['decisions_md'] else '없음'}")
    else:
        print("의사결정 기록: 없음 — 코드로만 검증한다")

    if a.show_scope:
        # 목록 출력은 기계가 정확히 할 수 있다. 코멘트와의 대조는 산문 대 산문이라 못 한다 —
        # 그래서 여기까지만 하고 판단은 사람 확인 게이트로 넘긴다.
        found = False
        for key in ("context_md", "decisions_md"):
            path = (record or {}).get(key)
            if not path:
                continue
            for h, b in _sections(Path(path).read_text()):
                if h.startswith(("## 스코프 밖", "## 후속 티켓")):
                    print(f"\n{'─'*70}\n{Path(path).name} · {h}\n{b.rstrip()}")
                    found = True
        if not found:
            print("\n⚠ 스코프 밖·후속 티켓 절을 찾지 못했다 — 범위 판단 근거가 기록에 없다.")
    if out_path:
        print(f"저장: {out_path}")
    print()
    for i, it in enumerate(items):
        mark = "✓" if it.get("triaged") else \
               ("→" if it["after_my_last_reply"] else (" " if it["is_incoming"] else "·"))
        who = "나" if it["is_my_reply"] else it["author"] + ("(bot)" if it["bot_marker"] else "")
        head = re.sub(r"\s+", " ", it["body"])[:70]
        loc = f" {it.get('path')}:{it.get('line')}" if it.get("path") else ""
        # APPROVED 같은 review state 를 보여준다 — 승인 코멘트는 응답 대상이 아닌 경우가 많다.
        # 다만 본문에 실질 지적이 담긴 APPROVED 도 있으므로 걸러내지 않고 표시만 한다.
        st = f" <{it['state']}>" if it.get("state") else ""
        # +N = 이 코멘트 이후 푸시된 커밋 수. 크면 리뷰가 낡은 코드를 봤을 가능성이 있다.
        aft = f" +{len(it['commits_after'])}c" if it["is_incoming"] and it["commits_after"] else ""
        print(f"{mark} [{i:2}] {it['source']:9} {who:24}{st}{aft}{loc} {head}")


if __name__ == "__main__":
    main()
