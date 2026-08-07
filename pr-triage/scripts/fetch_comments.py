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
    from run_round import branch_dir, note_owner  # 경로 파생은 red-team 과 한 규칙을 쓴다
except ImportError:
    branch_dir = note_owner = None

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
    if note_owner is not None:
        # 커서 전용 디렉토리(round.json 없음)도 origin 변경 승계의 양성 증거를 가진다
        # (red-team code-12 P1) — 없으면 owner 변경 시 triaged/notified 가 조용히 초기화된다.
        note_owner(p.parent, cwd)
    st["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    # 쓰는 사이 red-team 이 구 레이아웃을 새 키로 이전(rename)했을 수 있다 — 경로를
    # 재파생해 달라졌으면 새 위치로 옮겨 쓴다. 안 하면 상태가 구·신 위치로 갈라져 처리
    # 완료가 미처리로 되살아난다(red-team code-7 P1). 이 쓰기가 최신 병합본이므로
    # 이전된 사본은 덮는다. ms 단위 잔여 창은 수용한다(문서 프로토콜로 못 닫는 매체 한계).
    cur = state_path(cwd, pr)
    if cur != p:
        cur.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.replace(cur)
        except FileNotFoundError:
            if not cur.exists():  # rename 이 파일까지 옮겼다면 이미 원하는 위치다(code-8 P2)
                raise
        p = cur
    return p


def merge_state(cwd: str, pr: int, updates: dict) -> dict:
    """상태 쓰기 공통 규칙 — save 직전 디스크를 **다시 읽고** 병합해 쓴다.

    시작 시점 스냅샷을 되쓰면 병행 watch 의 `notified` 나 `--mark-triaged` 의 `triaged` 를
    통째로 덮는다(PR #872 실사고 — watch_comments.save_notified 가 같은 패턴이다).
    리스트 키는 합집합, `fp_replies` 는 keep-first — **디스크 기존 키가 우선**한다.
    1회차 전문 반박이 그 fp 의 앵커다: 2회차 요약 회신이 같은 fp 를 달고 와도
    앵커가 요약으로 밀리면 안 된다.
    """
    st = load_state(cwd, pr)
    for k, v in updates.items():
        if k == "fp_replies":
            merged = dict(v)
            merged.update(st.get("fp_replies") or {})   # 디스크 우선 = keep-first
            st["fp_replies"] = merged
        elif isinstance(v, list):
            st[k] = sorted(set(st.get(k) or []) | set(v))
        else:
            st[k] = v
    save_state(cwd, pr, st)
    return st


# 분류 기록은 **PR 경계를 넘어** 누적돼야 한다 — 한 PR 에서는 임계값에 도달하지 않는다.
# append-only 로그라서 last.json 같은 '가변 포인터'와 다르다: 낡을 진실이 없고,
# 과거 분류는 다른 곳에서 파생할 수도 없다. 그래서 전역 파일이 정당하다.
LOG = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team")) / "pr-triage-log.jsonl"
# 검토를 물을 시점 — 문서에 적어두면 아무도 세지 않으므로 기록 시점에 스크립트가 알린다.
REVIEW_AT_TOTAL = (30, 100)     # 30: 안전 A·B 비율 / 100: 드문 라벨
REVIEW_AT_CORRECTED = (5, 20)   # 교정 5건이면 구체적 실패 사례가 모인다

# 봇 종결 실패 텔레메트리 — 회신에 답했는데 봇이 같은 fingerprint 로 또 올린 기록.
# **오분류 신호가 아니다** — 봇은 회신이 옳아도 재게시하므로 pr-triage-log.jsonl 에
# 섞지 않는다. 섞으면 두 소비처(log_count 임계값, evals/score.py)에 가드가 필요해진다 —
# 분리가 가드보다 단순하다. 경로는 LOG 와 같은 파생식이다(테스트 격리·env 오버라이드가
# 갈라지면 안 된다 — 리터럴 `~/.red-team` 하드코딩 금지).
REPOSTS = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team")) / "pr-triage-reposts.jsonl"
REPOST_REVIEW_AT = (10, 30)     # 누적되면 봇 팀에 종결 경로 신설을 요청할 근거가 된다


def repost_count():
    if not REPOSTS.exists():
        return 0
    return sum(1 for l in REPOSTS.read_text().splitlines() if l.strip())


def log_count():
    if not LOG.exists():
        return 0, 0
    rows = [json.loads(l) for l in LOG.read_text().splitlines() if l.strip()]
    return len(rows), sum(1 for r in rows if r.get("corrected"))


def _sections(text: str):
    """`## ` 단위로 (heading, body) 로 쪼갠다."""
    parts = re.split(r"^(## .+)$", text, flags=re.M)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]


# hermes 류 봇이 코멘트마다 심는 fingerprint 마커. **비탐욕**이라 한 본문의 마커 여러 개를
# 각각 잡고(탐욕이면 첫 `<!--` 부터 마지막 `-->` 까지 삼킨다), fp 값의 공백·쉼표를 허용하며
# 위치(말미)를 가정하지 않는다 — #9588 실물 6건(다중 마커·공백 fp·footer 후행)으로 검증됐다.
FP_RE = re.compile(r"<!--\s*hermes:fp=(.+?)\s*-->")
# 코드펜스(``` 로 시작·끝나는 라인 사이) 를 통째로 제거한다 — fp_markers 가 이 다음에
# blockquote 를 걷어내고 스캔하므로, 여기서 지운 구간도 인용과 동일하게 마커 스캔 대상에서
# 빠진다. 비탐욕 `.*?` 로 첫 닫는 펜스에서 멈춘다(중첩 펜스는 실물에서 보지 못했다).
FENCE_RE = re.compile(r"^```[^\n]*\n.*?^```[ \t]*$", re.M | re.S)


def fp_markers(body: str) -> list:
    """본문의 hermes fingerprint 마커를 전부 뽑는다 — **마커 스캔의 단일 정의**다.

    blockquote 라인(strict `^>` — GitHub quote-reply 실물은 전부 컬럼 0 이라 충분하다)과
    코드펜스(``` … ```) 블록 내부를 걷어낸 뒤 스캔한다 — 제3자가 코드블록으로 봇 코멘트를
    그대로 인용해도(이슈 본문에 붙여넣기 등) 마커가 있다는 이유만으로 그 사람이 봇으로
    오판정되면 안 된다 — 인용(blockquote)과 같은 취급이다.
    판정을 관대하게(들여쓴 인용 등) 넓히지 않는다 — post_replies 의 게시 게이트가 이 함수를
    그대로 import 해 쓰므로, 여기가 관대해지면 쓰기 게이트(관대)와 읽기 파싱(엄격)이 갈라져
    게이트를 통과한 본문이 다음 fetch 에서 봇으로 판정되는 드리프트가 생긴다.
    """
    body = FENCE_RE.sub("", body)
    kept = "\n".join(l for l in body.splitlines() if not l.startswith(">"))
    return [m.group(1) for m in FP_RE.finditer(kept)]


def is_bot(body: str, marker: str) -> bool:
    """리뷰 봇이 올린 코멘트인가.

    봇이 **작성자 계정으로** 올리는 경우가 있어 작성자만으로는 못 가른다.
    형식도 레포마다 다르다 — 어떤 레포는 본문이 바로 마커로 시작하지만
    어떤 레포는 `<!-- …-pr-auto-review:… -->` HTML 주석을 먼저 붙인다.
    앞쪽 HTML 주석을 걷어낸 뒤 마커를 보고, 주석 자체가 봇 서명이면 그것으로도 판정한다.

    **fp 마커 자체가 🤖 보다 강한 봇 서명이다** — hermes 는 사람 리뷰어 계정으로 🤖 없이
    재게시하는 실물이 있다(#9588 의 fp 코멘트 11건 중 4건이 산문으로 시작). 비-blockquote
    라인의 마커만 본다(fp_markers 가 인용을 걷어낸다) — 내 회신의 인용은 봇 판정이 아니다.
    """
    if re.match(r"\s*<!--\s*[\w./#-]*(?:auto-review|autoreview|bot)\b", body, re.I):
        return True
    if fp_markers(body):
        return True
    head = re.sub(r"^\s*(?:<!--.*?-->\s*)+", "", body, flags=re.S)
    return head.lstrip().startswith(marker)


# pulls/{pr}/files API 는 파일 3000개까지만 내려준다. 상한에 근접하면 잘린 목록일 수 있고,
# 잘린 목록 기준 `in_diff=false` 는 틀린 표시다 — 플래그 전체를 null 로 강등하고 경고한다.
FILES_API_CAP = 3000
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def line_in_hunk(patch, line, side):
    """inline 코멘트의 line 이 그 파일 patch 의 new-side hunk 범위 안인가.

    판정은 side=RIGHT 만 한다 — LEFT(삭제 라인)의 line 은 old-side 좌표라 new-side hunk 와
    비교할 수 없다. null 규칙: line 이 null(outdated 코멘트) / side 가 RIGHT 아님 /
    해당 파일에 patch 없음(바이너리·대형 diff) → null (모른다는 뜻이지 범위 밖이 아니다).
    """
    if line is None or side != "RIGHT" or not patch:
        return None
    for m in HUNK_RE.finditer(patch):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        if start <= line < start + count:
            return True
    return False


def gh(*args, check=True):
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0:
        if check:
            sys.exit(f"gh {' '.join(args[:3])} 실패:\n{p.stderr.strip()}")
        return None
    return p.stdout


def gh_json(path, check=True):
    out = gh("api", path, "--paginate", "--slurp", check=check)
    if out is None:
        return None   # check=False 호출자만 받는다 — 그 외 경로는 gh() 가 이미 exit 했다
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
    ap.add_argument("--show-files", action="store_true",
                    help="PR 의 변경 파일 목록을 출력한다. top-level 봇 코멘트(경로를 본문에 "
                         "쓰는 류)와의 대조 입력이다 — 목록까지 기계, 대조는 모델+사람 "
                         "(--show-scope 와 같은 역할 분담)")
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
                      "path": c.get("path"), "line": c.get("line"), "side": c.get("side"),
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

    # ── diff 대조 플래그 — inline 코멘트 한정 ──────────────────────────────
    # files API 는 여기서 **한 번만** 부르고, 플래그 계산과 --show-files 출력이 같은 응답을
    # 공유한다. top-level(issue) 코멘트에는 계산하지 않는다(null) — 위치 좌표가 없다.
    # 플래그는 표시일 뿐 **필터하지 않는다** — 범위 밖 지적도 사실관계는 유효할 수 있다.
    # files API 는 diff 대조에만 쓰이는 **부가** 신호다 — 실패해도 전체 fetch 를 죽이지
    # 않는다(check=False). 실패하면 "모른다=null" 로 강등한다 — 기존 3000건 상한 강등과
    # 같은 경로를 재사용한다(잘렸는지 아예 못 받았는지는 소비 측에서 구분할 필요가 없다).
    files = gh_json(f"repos/{repo}/pulls/{pr}/files", check=False)
    files_failed = files is None
    if files_failed:
        files = []
        print("⚠ 변경 파일 목록 조회 실패(gh api 오류) — diff 플래그(in_diff·line_in_hunk)를 "
              "전부 null 로 강등했다.")
    patches = {f["filename"]: f.get("patch") for f in files}
    files_truncated = len(files) >= FILES_API_CAP - 100
    for it in items:
        if it["source"] != "inline" or files_truncated or files_failed:
            it["in_diff"] = it["line_in_hunk"] = None
        else:
            it["in_diff"] = it.get("path") in patches
            it["line_in_hunk"] = line_in_hunk(patches.get(it.get("path")),
                                              it.get("line"), it.get("side"))

    # ── fingerprint 파싱 ───────────────────────────────────────────────────
    # 파싱 자격: is_incoming 이고 (bot_marker 이거나 본문에 fp 마커 존재). bot_marker 만으로
    # 한정하면 hermes 가 사람 계정으로 🤖 없이 올린 재게시가 탈락한다(#9588 에서 11건 중 4건).
    # is_my_reply 는 파싱하지 않고, fp_markers 가 blockquote 를 걷어내므로 인용도 안전하다.
    # 스키마는 **리스트다** — 한 코멘트에 마커 2개 실물이 있다(#9588 에 4건: 한 fp 로는 3번째
    # 게시 + 다른 fp 로는 초회 게시가 공존). 단수 fp 필드는 어디에도 두지 않는다.
    # fp_seq(fp, comment 쌍 단위)·fp_replied·재게시 감지는 **--new-only 필터 전** 전체 items
    # 기준으로 계산한다 — 필터 뒤에 세면 처리된 1회차가 빠져 회차가 줄어든다.
    st = load_state(cwd, pr) if branch_dir else {"triaged": []}
    fp_replies = st.get("fp_replies") or {}
    seq, first_id = {}, {}
    for it in items:
        markers = fp_markers(it["body"]) if it["is_incoming"] else []
        fps = []
        # 같은 코멘트 안에 동일 마커가 2회 나타나도 그 코멘트에서의 등장은 1회로 센다 —
        # dict.fromkeys 로 등장 순서를 유지한 채 dedup 한다. dedup 없이 markers 그대로
        # 돌면 한 코멘트가 같은 fp 의 seq 를 2번 올려 fp_seq 가 부풀어 오른다.
        for fp in dict.fromkeys(markers):
            seq[fp] = seq.get(fp, 0) + 1
            first_id.setdefault(fp, it["id"])
            rep = fp_replies.get(fp) or {}
            fps.append({"fp": fp, "fp_seq": seq[fp], "fp_first_id": first_id[fp],
                        "fp_replied": fp in fp_replies,
                        "fp_reply_url": rep.get("reply_url")})
        it["fps"] = fps

    # ── 봇 종결 실패 텔레메트리 — fp_replied 인 fp 의 재게시 ──────────────
    # **오분류 신호가 아니다** — 봇은 회신이 옳아도 재게시한다. 내 회신(fp_replies 의
    # reply_id)보다 **뒤에** 생성된 코멘트만 재게시다 — 내가 답한 원본까지 세면 허위 기록이다.
    # 회신을 목록에서 못 찾으면(삭제 등) 시점을 판정할 수 없으므로 기록하지 않는다.
    logged = set(st.get("repost_logged") or [])
    by_id = {it["id"]: it for it in items}
    repost_rows = []
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for it in items:
        for e in it["fps"]:
            reply = by_id.get((fp_replies.get(e["fp"]) or {}).get("reply_id"))
            key = f"{e['fp']}:{it['id']}"   # dedup — 같은 재게시를 fetch 마다 다시 세지 않는다
            if not e["fp_replied"] or not reply or key in logged \
                    or it["created_at"] <= reply["created_at"]:
                continue
            logged.add(key)
            repost_rows.append({"fp": e["fp"], "comment_id": it["id"],
                                "prior_reply": e["fp_reply_url"],
                                "repo": repo, "pr": pr, "at": stamp})
    if repost_rows:
        before = repost_count()
        REPOSTS.parent.mkdir(parents=True, exist_ok=True)
        with REPOSTS.open("a") as f:
            for row in repost_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        after = repost_count()
        if branch_dir:
            merge_state(cwd, pr, {"repost_logged": sorted(logged), "repo": repo})
        print(f"봇 재게시(회신 후) {len(repost_rows)}건 기록 → {REPOSTS} · 누적 {after}건")
        # crossing 판정(이전 < t <= 이후)이라 정확히 한 번 발동한다 — 기존 REVIEW_AT_* 와 동일.
        hits = [t for t in REPOST_REVIEW_AT if before < t <= after]
        if hits:
            print(f"▶ 봇 종결 실패 누적 {after}건 (임계 {', '.join(map(str, hits))}건 도달) — "
                  f"봇 팀 전달(종결 경로 신설 요청)을 검토한다.")

    # 이미 처리한 코멘트를 표시한다. --new-only 면 그것들을 목록에서 뺀다.
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
    if files_truncated:
        print(f"⚠ 변경 파일 {len(files)}개 — API 상한({FILES_API_CAP})에 근접해 목록이 잘렸을 수 "
              "있다. diff 플래그(in_diff·line_in_hunk)를 전부 null 로 강등했다.")
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

    if a.show_files:
        # 목록까지는 기계가 정확하다. top-level 봇 코멘트(경로를 본문에 쓰는 aws 류)와의
        # 대조는 산문이 끼므로 모델+사람이 한다 — --show-scope 와 같은 역할 분담이다.
        if files_failed:
            print("\n변경 파일 조회 실패 — gh api 오류로 목록을 가져오지 못했다.")
        else:
            print(f"\n변경 파일 {len(files)}개"
                  + (" ⚠ API 상한 근접 — 잘린 목록일 수 있다" if files_truncated else "") + ":")
            for f in files:
                print(f"  {f['filename']}")
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
        # ↻N = 이 fingerprint 의 게시 회차(fp_seq). **회차의 유일한 진실이다** — 봇 산문의
        # "N번째" 나 기억으로 세지 않는다(#9588 실물: 산문 "3번째" vs fp 기준 2번째).
        fpm = "".join(f" ↻{e['fp_seq']}" for e in it.get("fps", []))
        # ∉diff/∉hunk = inline 코멘트의 위치가 변경 파일/new-side hunk 밖. 표시일 뿐
        # 걸러내지 않는다 — 범위 밖 지적도 사실관계는 유효할 수 있다.
        flag = " ∉diff" if it.get("in_diff") is False else \
               (" ∉hunk" if it.get("line_in_hunk") is False else "")
        print(f"{mark} [{i:2}] {it['source']:9} {who:24}{st}{aft}{loc}{fpm}{flag} {head}")


if __name__ == "__main__":
    main()
