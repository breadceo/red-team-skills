#!/usr/bin/env python3
"""red-team 이어받기 — 지금 어디인지 알려주고, 다음 라운드 컨텍스트를 만들어 준다.

작업 중인 워크트리에서 이것만 실행하면 된다:
    python3 <red-team-skill>/scripts/resume.py

게이트를 넘어갈 때(계획 GO → 구현 완료 → 코드 게이트):
    python3 .../resume.py --next code

**포인터 파일을 두지 않는다.** 라운드의 키는 경로 자체이고
(`~/.red-team/runs2/<owner>__<repo>/<branch키>/`), 그건 저장소 위치에서 나온다.
별도 포인터를 두면 (a) 어긋날 수 있는 두 번째 진실이 생기고
(b) eval·실험 런이 진짜 작업 포인터를 덮는 사고가 난다(실제로 났다).

`--next` 가 하는 일은 사람이 잊으면 루프가 수렴하지 않는 부분이다 —
직전 라운드 context.md 를 복사하고 decisions.md 의 `반영`을 `이미 반영된 지적` 절에,
`후속 티켓`을 `스코프 밖` 절에 옮겨 붙인다. 이 누적이 재제기를 막는다.
"""
import argparse, json, os, re, shlex, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_round  # --dry-run 이 마이그레이션 스위치(run_round.MIGRATE)를 끄기 위해
from run_round import branch_dir, git, GATES, sections, TODO  # 경로 파생·절 규격은 한 곳에서만 한다

HOME_DIR = Path(__file__).resolve().parent.parent  # 스킬 디렉토리
# 검색은 v2 루트(runs2/)와 구 루트(runs/) 둘 다 본다 — 아직 이전되지 않은 작업도
# 키로 찾을 수 있어야 하고, 발견 후의 이전은 대상 확정 후 선행 이전이 한다.
_HOME = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team"))
RUNS_ROOTS = (_HOME / "runs2", _HOME / "runs")
INV = "## 변경 대상 인벤토리"
INV_MARK, INV_END = "<!-- resume:inventory -->", "<!-- /resume:inventory -->"
INV_RE = re.compile(re.escape(INV_MARK) + r".*?" + re.escape(INV_END) + r"\n*", re.S)
STALE_AFTER = 5   # 이 라운드부터 "표는 계획 시점 판" 배너를 함께 붙인다
UNTRACKED_CAP = 20


def shell_command(*args) -> str:
    return shlex.join(str(arg) for arg in args)


def round_command(cwd, gate, round_dir, reviewers) -> str:
    return shell_command("python3", HOME_DIR / "scripts" / "run_round.py",
                         "--cwd", cwd or "<워크트리 경로>", "--gate", gate,
                         "--merge-into", round_dir, "--reviewers", ",".join(reviewers))


def resolve_base(key: str | None, cwd: str):
    """어느 `runs/<repo>/<branch>/` 를 볼지 정한다.

    key 가 없으면 현재 워크트리에서 파생한다. key(티켓·브랜치 조각)가 있으면 runs/ 를 훑어
    그 조각을 담은 브랜치 디렉토리를 찾는다 — 워크트리 밖에서도 진입할 수 있고,
    cwd 와 어긋나면 알려준다(엉뚱한 워크트리에서 구현하는 사고를 막는다).
    """
    # 키 조회는 순수 읽기다(기본값) — 여기서 cwd 의 구 라운드가 이전되면 다른 작업을
    # 조회만 해도 부수효과가 난다(code-5 P2). 이전은 대상 확정 후 선행 이전 한 곳에서만.
    derived = branch_dir(cwd)
    if not key:
        return derived, None
    k = key.lower()
    # 검색 순서: 완전 일치(parent/name) → 브랜치명 substring → 전체키 substring.
    # 완전 일치가 먼저여야 다중 후보 안내에 표시된 구 경로 식별자를 그대로 재입력했을 때
    # 그 문자열을 substring 으로 담은 새 경로와 또 겹치지 않는다(code-5 P2).
    # 폴백을 항상 켜면 티켓 키가 repo 키에도 걸려 유일하던 검색이 깨진다(code-4 P2).
    dirs = [d for r in RUNS_ROOTS for d in r.glob("*/*") if d.is_dir()]
    # 식별자는 루트 포함 3부(`runs2/owner__repo/branch키`)까지 exact 로 받는다 — 이전이
    # 거부된 legacy 와 새 runs2 라운드가 같은 parent/name 으로 공존하면 2부 식별자로는
    # 어느 쪽도 유일 선택할 수 없다(code-8 P1). 안내가 3부를 보여주므로 재입력이 항상 먹힌다.
    hits = sorted(d for d in dirs if f"{d.parent.name}/{d.name}".lower() == k
                  or _full_id(d).lower() == k) or \
        sorted(d for d in dirs if k in d.name.lower()) or \
        sorted(d for d in dirs if k in f"{d.parent.name}/{d.name}".lower())
    if not hits:
        avail = sorted(_full_id(d) for d in dirs)
        sys.exit(f"'{key}' 에 맞는 라운드 디렉토리가 없다.\n  있는 것: "
                 + (", ".join(avail) if avail else "(없음)"))
    if len(hits) > 1:
        sys.exit(f"'{key}' 가 여러 곳에 맞는다. 더 구체적으로 준다(그대로 재입력 가능):\n  "
                 + "\n  ".join(_full_id(d) for d in hits))
    return hits[0], (derived if hits[0] != derived else None)


def _full_id(d: Path) -> str:
    """루트 포함 3부 식별자 — 두 루트에 같은 parent/name 이 공존해도 유일하다(code-8 P1)."""
    return f"{d.parent.parent.name}/{d.parent.name}/{d.name}"


def _belongs(path: str, base: Path) -> bool:
    """path 워크트리가 base 라운드 디렉토리의 주인인가 — 이름이 아니라 **파생 동등성**으로
    판정한다. 이름만 비교하면 같은 브랜치명의 다른 저장소 워크트리를 확정해 남의 runs 를
    오염시키고(code-5·6 P1), 새 키를 문자 그대로 이름으로 쓴 브랜치가 오선택된다.
    구·신 경로가 공존하면 무변경 해석은 새 경로를 돌려주므로 legacy 키 동등성도 인정한다.
    """
    try:
        if branch_dir(path) == base:
            return True
        # 구 세대(gen1·gen0) 키 동등성 — v2 경로가 이미 있으면 무변경 해석이 그쪽을
        # 돌려주므로, 구 루트에 남은 base 는 세대별 키로 대조한다.
        return any(c == base for c in
                   run_round._legacy_candidates(path, run_round._current_branch(path)))
    except OSError:
        return False


def worktree_for(base: Path, cwd: str, rounds_dir: Path | None):
    """이 브랜치의 워크트리 경로를 찾는다 — run_round.py 의 --cwd 로 쓸 값이다.

    ① 이미 그 워크트리 안이면 cwd ② 라운드 기록(round.json 의 repo_cwd)
    ③ 같은 저장소의 다른 체크아웃에서 `git worktree list` 로 조회.
    ②·③ 모두 _belongs 소속 검증을 통과해야 한다 — ② 를 존재 여부만으로 믿으면 낡은
    repo_cwd 가 무관한 저장소를 확정해 그쪽 legacy 를 선행 이전시킨다(code-6 P1).
    """
    if branch_dir(cwd) == base:  # 비교는 순수 읽기(기본값) — 이전은 선택 확정 후
        return cwd, "현재 디렉토리"
    if rounds_dir and (rj := rounds_dir / "round.json").exists():
        try:
            p = json.loads(rj.read_text()).get("repo_cwd")
        except json.JSONDecodeError:
            p = None
        if isinstance(p, str) and p and Path(p).is_dir() and _belongs(p, base):
            return p, "라운드 기록(round.json)"
    out = git(cwd, "worktree", "list", "--porcelain")
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
            if Path(path).is_dir() and _belongs(path, base):
                return path, "git worktree list"
    return None, None


def round_dirs(base: Path, gate: str) -> list[Path]:
    """`<gate>-<번호>` 라운드 디렉토리 목록. **이름 규칙은 여기서만 정의한다** —
    pr-triage 도 이걸 불러 code 라운드 수를 센다(경로·기록 규칙을 한쪽만 고치면 갈라진다).
    """
    return [d for d in base.glob(f"{gate}-*")
            if d.is_dir() and re.fullmatch(rf"{gate}-\d+", d.name)]


def latest_round(base: Path):
    """가장 최근 라운드와 그 상태를 찾는다.

    round.json 이 있으면 '실행됨'(정렬 키 = round.json mtime),
    없으면 '준비만 됨'(= --next 로 만들었고 아직 안 돌린 것, 키 = 디렉토리 mtime).
    번호나 게이트 순서로 정렬하지 않는 이유 — code 게이트를 돌다가 계획으로 되돌아갈 수도 있다.
    """
    rounds = []
    for gate in GATES:
        for d in round_dirs(base, gate):
            rj = d / "round.json"
            ran = rj.exists()
            rounds.append(((rj if ran else d).stat().st_mtime, d, gate, ran))
    if not rounds:
        return None
    rounds.sort()
    return rounds[-1][1:]


def render(secs: list[tuple[str, str]]) -> str:
    return "".join((h + "\n" if h else "") + b for h, b in secs)


def block(decisions: str, keyword: str) -> str:
    """decisions.md 에서 `## <keyword> …` 절의 본문을 꺼낸다."""
    for h, b in sections(decisions):
        if h.startswith(f"## {keyword}"):
            return b.strip()
    return ""


def pending(decisions: str) -> str:
    """`보류` 절에 실제 항목이 남아 있으면 그 내용을 돌려준다."""
    b = block(decisions, "보류")
    # 불릿은 마커 뒤 공백까지 요구한다(CommonMark) — `---`·`***` 는 수평선이지 항목이 아니다.
    # 이걸 항목으로 세면 미결이 없는데 `--next` 가 막히고, 원인이 문서 관례라 잘 안 보인다.
    lines = [l for l in b.splitlines()
             if re.match(r"\s*[-*+]\s+\S", l) and "없" not in l and "비운다" not in l]
    return "\n".join(lines)


def round_no(rd: Path) -> int:
    """`code-7` → 7. latest_round 가 `<gate>-\\d+` 만 돌려주므로 접미는 항상 숫자다."""
    return int(rd.name.rsplit("-", 1)[1])


def abort_offer(base: Path) -> str:
    """교착일 수 있는 자리에서 `ABORTED` 종결을 **함께 제시하는** 문구.

    제시일 뿐 판정이 아니다 — 라운드를 자동으로 끝내지 않는다. "남은 P1 이 코드 밖
    판단(기획·PO·타 팀)을 기다린다" 는 사실 자체가 산문이라 기계가 볼 수 없고, 기계가 보는
    것(`보류` 가 안 빔 · 라운드 수)은 수렴 실패에서도 같은 값이다. 자동 판정은 원리적으로
    오탐하고 오탐 비용이 비대칭이다 — 조기 종료는 결함을 안 채로 종결시키고, 제시 오탐은
    안내 한 줄이다. 근거: `references/recovery.md` 「교착이면 ABORTED 를 먼저 제시한다」.
    """
    return (
        "\n▶ 남은 P1 이 코드 밖 판단(기획·PO·타 팀)을 기다리는 것이면 라운드를 더 도는 것이\n"
        "  답이 아니다 — 그것은 수렴 실패가 아니라 교착이고, 정당한 종결은 중단(ABORTED)이다.\n"
        f"  고르는 것은 사람이다. 접기로 했으면 이 파일에 사유를 쓴다:\n    {base / 'ABORTED'}\n"
        f"  본문은 {HOME_DIR / 'assets' / 'aborted-template.md'} 를 복사해 채운다 —\n"
        "  `미해결 P1` 절을 비우지 않는다(그 목록이 pr-triage 가 리뷰어 지적을 가르는 근거다)."
    )


PLAN_DOC_RE = re.compile(r"^plan.*\.(md|markdown)$", re.I)  # zax 는 PLAN.md, 초안은 plan.markdown 도 허용


def _origin_paths(rdir: Path) -> set[str]:
    """한 라운드의 regression P1 원인 경로 집합(저장소 루트 기준 상대 경로로 정규화).

    경로 후보는 게이트별로 다르다 — 계획 게이트 finding 의 `file` 은 구조적으로
    계획·컨텍스트 문서(plan-*.md, redteam-context.md 등)를 가리키므로 `origin_file` 만
    쓰고, 코드 게이트만 `origin_file` 우선 + `file` 폴백이다. 어느 쪽이든 비어 있지 않은
    문자열만 후보이고(LLM 의 계약 위반 방어), 계획 문서 basename 은 원인 파일이 아니므로
    제외한다. 구조가 어긋난 기록은 호출부의 예외 격리가 라운드째 걷어낸다.
    """
    rj = json.loads((rdir / "round.json").read_text())
    root = rj.get("repo_root")
    if not root:  # 구계약 라운드 — repo_cwd 에서 파생 시도, git 실패 시 repo_cwd 폴백
        cwd = rj.get("repo_cwd") or ""
        root = (git(cwd, "rev-parse", "--show-toplevel") if cwd else "") or cwd
    plan_gate = rdir.name.startswith("plan-")
    paths = set()
    for f in rj.get("findings", []):
        if f.get("classification") != "regression" or f.get("severity") != "P1":
            continue
        cands = (f.get("origin_file"),) if plan_gate else (f.get("origin_file"), f.get("file"))
        for v in cands:
            if not (isinstance(v, str) and v.strip()):
                continue
            p = re.sub(r":\d+(?:[-:]\d+)?$", "", v.strip())  # `:줄`·`:줄-줄` 접미 제거
            if os.path.isabs(p) and root:
                try:
                    p = str(Path(p).resolve().relative_to(Path(root).resolve()))
                except ValueError:
                    pass  # 저장소 밖 절대경로는 그대로 둔다
            p = p.removeprefix("./")
            if p and not PLAN_DOC_RE.fullmatch(Path(p).name):
                paths.add(p)
                break  # 유효 경로를 얻었을 때만 종료 — origin_file 이 계획 문서로 제외되면
                       # 다음 후보(file)로 폴백해야 실제 코드 경로의 교집합이 유지된다
    return paths


def same_origin_p1(base: Path, gate: str) -> list[str]:
    """같은 게이트 최근 3라운드에서 P1 이 전부 같은 파일이면 그 경로들 — 구조적 결합 신호.

    비차단 경고 전용이라 어떤 실패도 resume 를 죽이면 안 된다: round.json 은 원자적
    쓰기가 아니라 잘린 파일이 남을 수 있고(UnicodeDecodeError 포함 — ValueError 서브클래스),
    수동 편집으로 구조가 어긋난 기록(AttributeError)도 실재한다. 읽을 수 없는 라운드가
    최근 3개에 끼면 '3라운드 연속' 을 판정할 수 없으므로 경고만 생략한다.
    """
    try:
        # '최근 3' 은 라운드 번호 순이다 — 번호가 곧 생성 순서다. mtime 을 쓰면
        # 오래된 라운드의 --merge-into 재실행(round.json 재기록)이 그 라운드를 '최근' 으로
        # 끌어올려 연속 판정이 왜곡된다(code-1 지적).
        rounds = sorted((d for d in base.glob(f"{gate}-*")
                         if re.fullmatch(rf"{gate}-\d+", d.name) and (d / "round.json").exists()),
                        key=lambda d: int(d.name.rsplit("-", 1)[1]))[-3:]
    except OSError:
        return []
    if len(rounds) < 3:
        return []
    sets = []
    for d in rounds:
        try:
            s = _origin_paths(d)
        except (OSError, ValueError, TypeError, AttributeError):
            return []  # 그 라운드를 판정에 쓸 수 없다 — 경고만 생략, resume 는 계속
        if not s:
            return []  # P1 없는 라운드가 끼면 '연속' 이 아니다
        sets.append(s)
    return sorted(set.intersection(*sets))


def worktree_inventory(cwd: str | None) -> tuple[int, list[str]]:
    """작업 트리에서 **셀 수 있는 것만** 센다 — 변경 파일 수와 untracked 목록.

    컨텍스트의 `변경 대상 인벤토리` 는 스스로 "전수" 를 주장하는데, 라운드가 쌓이는 동안
    새 파일이 합류하면 그 주장이 조용히 깨진다(실측: 변경 12→13, 테스트 5→6). 사람은
    이월된 표를 다시 세지 않으므로, 세는 일은 스크립트가 매 라운드 한다.
    """
    out = git(cwd, "status", "--porcelain", "-uall") if cwd else ""
    changed, new = 0, []
    for line in out.splitlines():
        st, _, path = line.strip().partition(" ")
        if st == "??":
            new.append(path.strip())
        elif st:
            changed += 1
    return changed, new


def inventory_banner(changed: int, new: list[str], n: int) -> str:
    """`## 변경 대상 인벤토리` 머리에 붙는, **매 라운드 재생성되는** 블록.

    사람이 갱신하는 절이 아니므로 손으로 넣게 두면 라운드 17쯤에나 들어간다(실측).
    """
    body = []
    if changed or new:
        body += [f"> **작업 트리 실측** (`git status --porcelain -uall`): "
                 f"변경 **{changed}** · 신규(untracked) **{len(new)}**. "
                 f"아래 표·목록의 수와 다르면 **표가 낡은 것이다.**",
                 "> **커밋된 변경은 이 수에 잡히지 않는다** — 리뷰 대상이 "
                 "`git diff <base>..HEAD` 면 이 수는 작업 트리 몫뿐이다."]
        if new:
            body += [">"] + [f"> - `{p}`" for p in new[:UNTRACKED_CAP]]
            if len(new) > UNTRACKED_CAP:
                body.append(f"> - …외 {len(new) - UNTRACKED_CAP}개")
    if n >= STALE_AFTER:
        body += [">"] if body else []
        body.append("> 이 절의 표는 **계획 시점 판**이다 — 이후 라운드가 서술을 뒤집었을 수 "
                    "있다. 표와 코드가 어긋나면 `이미 반영된 지적` 절과 코드가 우선이다. "
                    "**이 표의 역할은 서술의 정확성이 아니라 변경 표면의 전수성**"
                    "(빠진 파일이 없다)이다.")
    if not body:
        return ""
    # 끝에 빈 줄 하나 — 뒤따르는 표가 HTML 주석 블록에 먹히지 않게 한다
    return "\n".join([INV_MARK, *body, INV_END]) + "\n\n"


PER_ROUND = ("## 리뷰 대상", "## 검증 상태", "## 이번 라운드에서 특히 볼 것")
# `특히 볼 것` 은 서술이 아니라 **살아있는 지시**다 — 낡으면 리뷰어가 그 라운드가
# 방금 없앤 것을 검사한다(#42: 삭제된 라인번호 12개 대조 지시가 이월됐다).
# 인벤토리 표(서술, 코드가 이김)와 달리 여기는 TODO 로 갱신을 요구한다.
KEEP_FULL = 2  # '이미 반영된 지적'에서 전문을 유지할 최근 라운드 블록 수


def fold_applied(body: str) -> tuple[str, int]:
    """오래된 `### <round> 에서 반영` 블록을 톱레벨 불릿 첫 줄만 남기고 접는다.

    컨텍스트는 리뷰어 5명 × 매 라운드에 실리므로 반영 기록 누적이 곧 토큰 누적이다.
    재제기 억제에 필요한 것은 매칭 가능한 식별자 한 줄이지 결정 전문이 아니고,
    전문은 각 라운드 decisions.md 에 그대로 있다 — 기록 삭제가 아니라 가시성 압축이다.
    단, 잘못 접어 재제기가 하나라도 살아나면 라운드 하나가 통째로 추가돼 절감분을
    다 되먹는다. 그래서 최근 KEEP_FULL 개는 전문 유지, 접는 쪽도 항목을 지우지 않는다.
    """
    parts = re.split(r"^(### .+)$", body, flags=re.M)
    blocks = [(parts[i], parts[i + 1]) for i in range(1, len(parts), 2)]
    if len(blocks) <= KEEP_FULL:
        return body, 0
    out, saved = [parts[0]], 0
    for idx, (h, b) in enumerate(blocks):
        if idx >= len(blocks) - KEEP_FULL or "(요약" in h:  # 최근 것·이미 접힌 것은 그대로
            out += [h, b]
            continue
        kept = [l for l in b.splitlines() if l.startswith("- ")]
        saved += max(0, len(b.strip().splitlines()) - len(kept))
        out += [h.rstrip() + " (요약 — 전문은 그 라운드 decisions.md)",
                "\n\n" + "\n".join(kept) + "\n\n"]
    return "".join(out), saved


def collapse_per_round(secs):
    """라운드마다 갱신되는 절이 중복되면 **마지막 것만** 남긴다.

    `## 리뷰 대상` 이 두 번 있고 위쪽이 이전 라운드 기재("계획 게이트 — 구현 0%")면
    리뷰어가 둘 다 읽고 무엇을 리뷰하는지 헷갈린다. 가시성 문제가 아니라 리뷰 정확성 문제다.
    버려지는 내용은 직전 라운드 디렉토리에 그대로 남아 있으므로 기록이 사라지지는 않는다.
    """
    last = {}
    for i, (h, _) in enumerate(secs):
        for p in PER_ROUND:
            if h.startswith(p):
                last[p] = i
    keep, dropped = [], []
    for i, (h, b) in enumerate(secs):
        stale = any(h.startswith(p) and last[p] != i for p in PER_ROUND if p in last)
        (dropped if stale else keep).append((h, b))
    return keep, dropped


def carry_forward(prev_ctx: str, decisions: str, prev_round: str, banner: str = "") -> str:
    """직전 컨텍스트에 직전 라운드의 결정을 **누적**한다.

    decisions.md 는 그 라운드 몫만 담는 규격이므로 두 절 모두 append 다.
    replace 하면 2라운드 이상 이어갈 때 이전 누적이 사라지고, 리뷰어가 이미 반영한 지적을
    다시 제기해 루프가 수렴하지 않는다.
    """
    applied = block(decisions, "반영")
    deferred = block(decisions, "후속 티켓")
    secs, dropped = collapse_per_round(sections(prev_ctx))
    for h, b in dropped:
        print(f"   ↳ 철 지난 중복 절 제거: {h} ({len(b.strip().splitlines())}줄) "
              f"— 직전 라운드 기재이므로 그 라운드 디렉토리에 그대로 남아 있다")
    for i, (h, b) in enumerate(secs):
        if h.startswith("## 이미 반영된 지적") and applied:
            body = re.sub(r"^\s*없음[^\n]*\n?", "", b.strip(), flags=re.M)
            merged = f"\n{body}\n\n### {prev_round} 에서 반영\n\n{applied}\n".replace("\n\n\n", "\n\n")
            merged, saved = fold_applied(merged)
            if saved:
                print(f"   ↳ 오래된 반영 기록 {saved}줄을 1줄 인덱스로 접었다 "
                      f"(최근 {KEEP_FULL}개 라운드 블록은 전문 유지, 전문은 각 라운드 decisions.md)")
            secs[i] = (h, merged)
        elif h.startswith("## 스코프 밖") and deferred:
            secs[i] = (h, b.rstrip() + f"\n\n### {prev_round} 에서 후속 티켓으로 분리\n\n{deferred}\n")
        elif h.startswith(INV) and banner:
            # 옛 배너는 통째로 걷어내고 다시 넣는다 — 라운드마다 쌓이면 그것대로 stale 이다
            secs[i] = (h, "\n" + banner + INV_RE.sub("", b).lstrip("\n"))
        elif h.startswith(PER_ROUND) and TODO not in b:
            # 라운드마다 다시 붙으면 마커가 쌓인다 — 이미 있으면 그대로 둔다
            secs[i] = (h, f"\n{TODO}\n" + b.lstrip("\n"))
    return render(secs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--next", dest="next_gate", choices=["plan", "code"],
                    help="다음 라운드 디렉토리와 컨텍스트를 만든다")
    ap.add_argument("key", nargs="?", default=None,
                    help="티켓·브랜치 조각 (예: TICKET-123). 생략 시 현재 워크트리에서 파생")
    ap.add_argument("--cwd", default=None, help="대상 저장소 (생략 시 현재 디렉토리)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        # dry-run 은 무변경이어야 한다 — branch_dir 의 구 레이아웃 이전(rename)까지 끈다.
        # 새 경로가 없으면 구 경로를 읽기 전용으로 해석한다(code-3 P1).
        run_round.MIGRATE = False

    cwd = a.cwd or os.getcwd()
    base, mismatch = resolve_base(a.key, cwd)
    # 중단 검사가 모든 안내·조기 return 보다 앞이다 — partial top-up·PARSE-FAIL 재실행·
    # 준비만 된 라운드 실행·pending·--next 어느 것도 중단된 티켓에는 안내하지 않는다.
    # 상태의 진실은 마커 파일 하나다(산문 파싱은 오인 부류가 끝이 없어 버렸다 — plan-3~7).
    if (ab := base / "ABORTED").exists():
        print(f"⛔ 이 티켓의 리뷰 루프는 중단됐다 — {ab}:")
        # 존재가 곧 상태다 — 본문 읽기 실패(권한·비 UTF-8)가 차단 안내를 죽이면 안 된다
        try:
            body = ab.read_text().strip()
        except (OSError, ValueError) as e:  # ValueError ⊇ UnicodeDecodeError
            body = f"(본문을 읽을 수 없다: {e})"
        if body:
            print(body)  # 전체를 자르지 않는다 — 끝에 적힌 식별 정보(원격 URL·브랜치)가 잘리면 안 된다
        print("  다음 라운드를 만들지 않는다. 재개하려면(사유가 해소됐을 때만) 그 파일을 지우고 다시 실행한다.")
        return
    found = latest_round(base)
    if found is None:
        # '기록이 없다'와 '거부된 구 기록이 있다'를 가른다(code-10 P1) — 외부 소유(또는
        # 확인 실패)로 이전이 거부된 상태를 침묵하면 사용자는 기록이 사라졌다고 오인한다.
        blocked = ""
        if not a.key and (bk := run_round.migration_blocked(cwd)):
            state, f = bk
            why = ("다른 저장소 소유로 보이는" if state == "foreign"
                   else "소유 확인(git)에 실패한")  # 확인 실패를 불일치로 단정하지 않는다(code-11 P1)
            blocked = (f"\n  ⚠ 구 레이아웃에 {why} 기록이 있어 이전이 거부된 상태다"
                       f" — 기록의 워크트리: {f}\n"
                       f"    필요하면 그 기록을 수동 정리한 뒤 다시 실행한다.")
        sys.exit(f"{base} 에 라운드가 없다.{blocked}\n"
                 f"  여기가 맞는 저장소인지 확인하고(현재: {cwd}), 티켓 이름으로 찾으려면\n"
                 f"  `resume.py <티켓키>` 로 준다. 첫 라운드는 SKILL.md 의 '라운드 실행'부터 시작한다.")
    rd, gate, ran = found
    repo_cwd, how = worktree_for(base, cwd, rd)
    # 키로 구 레이아웃 디렉토리를 선택했으면 **여기서** 선행 이전한다 — 뒤에서 만들
    # context.md·--out 경로가 구 디렉토리를 가리키면, 안내된 run_round 가 실행 시작
    # 시점의 자동 이전으로 그 경로를 날려 즉시 실패한다(code-1 P2, 리뷰어 실측 exit 1).
    if repo_cwd and (derived := branch_dir(repo_cwd, migrate=True)) != base \
            and not base.exists() and derived.is_dir():  # 쓰기 지점 ② — 대상 확정 후에만
        rd, base = derived / rd.name, derived
    if mismatch is not None:
        print(f"⚠ 현재 디렉토리는 {mismatch.name} 인데 '{a.key}' 는 {base.name} 이다.")
        print(f"  {'구현·리뷰는 다음 워크트리에서 해야 한다: ' + repo_cwd if repo_cwd else '해당 워크트리를 못 찾았다 — 그 워크트리로 이동해 다시 실행한다.'}\n")
    # 안내 키는 사용자가 준 것이 아니라 **최종 base** 에서 다시 만든다 — 선행 이전으로
    # 경로가 옮겨지면 원래 키는 더 이상 어떤 디렉토리와도 맞지 않는다(code-6 P1).
    # 루트 포함 3부 식별자는 exact 검색 대상이라 두 루트가 겹쳐도 유일하다(code-8 P1).
    keysuf = f" {_full_id(base)}" if a.key else ""
    dec_path, ctx_path = rd / "decisions.md", rd / "context.md"
    verdict, counts = "(미실행)", None
    if ran:
        rj = json.loads((rd / "round.json").read_text())
        verdict, counts = rj.get("verdict", "?"), rj.get("counts")
        # 경고만 한다(진행을 막지는 않는다) — 대신 치유 명령을 준다. 이 문구가 "무효" 를
        # 단정하던 동안 사용자가 round.json 을 손으로 고쳤고, 감사 기록이 그만큼 망가졌다.
        if rj.get("access_errors"):
            print(f"⚠ 이 라운드에 파일접근오류가 있다 {rj['access_errors']} — 그 리뷰어 결과는 믿을 수 없다.\n"
                  f"  그 축만 다시 돌려 이 라운드에 병합한다(round.json 을 손으로 고치지 않는다):\n"
                  f"    {round_command(repo_cwd, gate, rd, rj['access_errors'])}")
        if (pf := [r for r, v in (rj.get("reviewers") or {}).items() if v == "PARSE-FAIL"]):
            print(f"⚠ PARSE-FAIL 리뷰어가 있다 {pf} — 그 축이 빠진 판정은 반쪽짜리다.\n"
                  f"  1회 재실행해 병합한다:\n"
                  f"    {round_command(repo_cwd, gate, rd, pf)}")

    print(f"저장소   : {repo_cwd or '(못 찾음)'}" + (f"  [{how}]" if how else ""))
    print(f"직전 라운드: {rd.name}  (gate={gate}, verdict={verdict})")
    print(f"경로     : {rd}")
    if counts:
        print(f"counts   : {counts}")
    if (so := same_origin_p1(base, gate)):
        print(f"⚠ P1 이 3라운드 연속 같은 파일에서 나온다: {', '.join(so)} — 구조적 결합 신호다.\n"
              f"  표면을 더 고치지 말고 SKILL.md 루프 절의 same-origin P1 항목을 본다"
              f"(어댑터 격리, 또는 생존성 7·8행 재심 → 중단/피벗).")
    if not ctx_path.exists():
        print("⚠ context.md 가 없다 — 이 라운드는 이어받을 수 없다.")
    if not ran:
        print("\n▶ 이 라운드는 준비만 됐고 아직 실행되지 않았다.")
        if repo_cwd:
            print(f"  {ctx_path} 의 '{TODO}' 표시된 절을 채운 뒤 실행한다:\n  "
                  + shell_command("python3", HOME_DIR / "scripts" / "run_round.py",
                                  "--cwd", repo_cwd, "--gate", gate,
                                  "--context", ctx_path, "--out", rd))
        else:
            # 워크트리를 모르면 경로 박힌 명령을 내지 않는다 — 구 레이아웃이면 run_round 가
            # 실행 시작 시점에 자동 이전해 그 경로가 사라진다(code-2 P1). 대상 워크트리에서
            # 재실행하면 선행 이전 후 새 경로로 재계산된 명령이 나온다.
            print("  워크트리를 못 찾아 실행 명령을 생략한다 — 대상 워크트리로 이동해\n"
                  "  resume.py 를 다시 실행한다(경로가 자동 이전·재계산된다).")
        return
    # 축이 빠진 라운드(--lean 등)의 GO 는 coverage=partial 로 기록된다 — 빠진 축이 못 본
    # 결함이 있을 수 있으므로 top-up 병합으로 커버리지를 채운 뒤의 verdict 만 게이트 판정이다.
    # findings 처리(decisions.md)보다 먼저 안내한다 — top-up 이 findings 를 더할 수 있다.
    if verdict == "GO" and rj.get("coverage") == "partial":
        # 빠진 이유가 둘이다 — skipped(호출 안 됨, --lean 유예) / unparsed(PARSE-FAIL, 실행 사고).
        # 치유 명령은 같지만(그 축만 병합) 사용자가 원인을 알아야 다음 라운드에서 같은 일을 안 겪는다.
        missing = list(rj.get("skipped", [])) + list(rj.get("unparsed", []))
        why = []
        if rj.get("skipped"):
            why.append(f"유예(--lean 등): {','.join(rj['skipped'])}")
        if rj.get("unparsed"):
            why.append(f"결과 파싱 실패(PARSE-FAIL): {','.join(rj['unparsed'])}")
        print(f"\n⚠ 축 {','.join(missing)} 가 빠진 GO 다 (coverage=partial) — 게이트 통과가 아니다.\n"
              f"  원인 — {' · '.join(why)}\n"
              f"  빠진 축을 이 라운드에 병합해 커버리지를 채운 뒤의 verdict 가 판정이다:\n"
              f"    {round_command(repo_cwd, gate, rd, missing)}")
        return
    # 축은 다 돌았는데(coverage=full) 축 스스로는 NO-GO 를 냈고 findings 가 0 이라 GO 로
    # 집계된 라운드다 — partial 과 같은 이유로 게이트 통과가 아니고, 같은 자리에서 막는다
    # (--next 경로도 여기서 함께 걸린다). 치유는 재실행이 아니라 사람이 근거를 읽는 것이다.
    if verdict == "GO" and (dissent := rj.get("verdict_dissent")):
        print(f"\n⚠ 축 {','.join(dissent)} 는 NO-GO 를 냈는데 findings 0 이라 GO 로 집계됐다 — "
              f"게이트 통과가 아니다.\n"
              f"  리뷰어가 라운드 밖 근거(미반영 지적·컨텍스트 지시)로 NO-GO 를 낸 경우다.\n"
              f"  그 축의 raw 에서 근거를 읽고 판단한다: "
              f"{', '.join(str(rd / f'{r}.txt') for r in dissent)}\n"
              f"  근거가 아직 열린 결함이면 이 게이트로 라운드를 더 돈다 — 근거가 이미 닫혔으면\n"
              f"  다음 라운드 context.md 의 `이미 반영된 지적` 에 적어 같은 NO-GO 가 반복되지 않게 한다.")
        return
    if not dec_path.exists():
        print("\n▶ 다음 할 일: 이 라운드의 findings 가 아직 처리되지 않았다.\n"
              f"  {rd/'round.json'} 을 읽어 P1/P2 를 처리하고 {dec_path} 를 쓴다 (형식은 SKILL.md).")
        return
    dec = dec_path.read_text()
    if (p := pending(dec)):
        print(f"\n⚠ decisions.md 의 `보류` 가 비어 있지 않다. 결정 전에는 다음 라운드로 가지 않는다:\n{p}")
        # 보류가 남은 것은 그 자체로 "지금 코드로 닫을 수 없는 것이 있다" 는 신호다 —
        # 그것이 코드 밖 판단 대기면 라운드를 더 도는 것은 교착의 반복이고, 실측에서
        # 사람이 고른 것은 ABORTED 가 아니라 '그냥 나가기' 였다(issue #65). 라운드 수와
        # 무관하게 여기서 제시한다 — 차단 문구만 주면 출구가 '결정' 하나로 보인다.
        print(abort_offer(base))
        return

    if not a.next_gate:
        print(f"\n▶ 먼저 읽을 것: {ctx_path}\n  (의도·스코프·판정 기준이 전부 여기 있다)")
        if verdict == "GO":
            nxt = "code" if gate == "plan" else "완료 — 호출한 워크플로우로 복귀"
            print(f"\n▶ 이 게이트는 통과(GO)했다. 다음: {nxt}")
            if gate == "plan":
                if doc := next((f.name for f in sorted(rd.glob("plan*.md"))), None):
                    print(f"  구현 대상 계획서: {rd/doc}")
                print("  구현 완료 후:  " + shell_command(
                    "python3", Path(__file__).resolve(), "--next", "code",
                    *([keysuf.strip()] if keysuf else [])))
        else:
            print(f"\n▶ verdict={verdict} — 같은 게이트로 라운드를 더 돈다:\n  "
                  + shell_command("python3", Path(__file__).resolve(), "--next", gate,
                                  *([keysuf.strip()] if keysuf else [])))
            # 3라운드부터는 '라운드를 더 돈다' 만 내지 않는다 — 탈출 결함이 나온 5건의
            # 코드 라운드 수는 3·5·5·13·17 이고 전부 마지막 verdict 가 NO-GO 였다
            # (issue #65). 임계는 long-gate.md 진입 조건과 같은 3이다.
            if round_no(rd) >= 3:
                print(abort_offer(base))
        return

    n = 1 + max((int(m.group(1)) for d in base.glob(f"{a.next_gate}-*")
                 if (m := re.fullmatch(rf"{a.next_gate}-(\d+)", d.name))), default=0)
    out = base / f"{a.next_gate}-{n}"
    changed, untracked = worktree_inventory(repo_cwd)
    new_ctx = carry_forward(ctx_path.read_text(), dec, rd.name,
                            inventory_banner(changed, untracked, n))
    if a.dry_run:
        # 읽기는 legacy(무변경 해석)여도 실제 실행은 이전 후 새 키 아래에 만든다 —
        # 표시 경로가 legacy 면 dry-run 확인과 실제 산출물 경로가 갈린다(code-4 P1).
        shown = out
        # 실제 실행이 **선택된 base 를** 이전하게 될 때만 target 을 표시한다 — 새 경로
        # 존재(code-5 P2)·이전 거부(code-7 P2)는 물론, 다른 세대(gen1)가 옮겨지고 선택된
        # gen0 는 남는 상태(code-9 P2)에서도 실제 생성은 base 쪽이다.
        if repo_cwd and (tgt := run_round.target_dir(repo_cwd)) != base \
                and run_round.migration_source(repo_cwd) == base:
            shown = tgt / out.name
        print(f"\n[dry-run] 생성할 경로: {shown}")
        for h, _ in sections(new_ctx):
            if h:
                print("  ", h)
        return
    out.mkdir(parents=True, exist_ok=True)
    if repo_cwd:
        run_round.note_owner(base, repo_cwd)  # 준비만 된 라운드도 승계 증거를 가진다(code-12 P1)
    (out / "context.md").write_text(new_ctx)
    for extra in ("plan-", "impact-"):  # 계획서·아티팩트는 게이트를 넘어가도 참조된다
        for f in rd.glob(f"{extra}*"):
            shutil.copy2(f, out / f.name)
    print(f"\n✅ {out/'context.md'} 생성 — decisions.md 의 반영/후속티켓을 이관했다.")
    todo_secs = [h for h, _ in sections(new_ctx) if h.startswith(PER_ROUND)]
    if todo_secs:
        print(f"   손으로 갱신할 절 {len(todo_secs)}개가 남았다 ({TODO} 표시됨): "
              + ", ".join(f"'{h}'" for h in todo_secs))
    if changed or untracked:
        print(f"   작업 트리 실측: 변경 {changed} · 신규(untracked) {len(untracked)} — "
              f"'{INV}' 절이 주장하는 수와 다르면 그 표가 낡은 것이다")
        if INV_MARK not in new_ctx:
            print(f"   ↳ '{INV}' 절이 컨텍스트에 없어 실측 배너를 넣지 못했다 — 손으로 대조하라")
    if n >= STALE_AFTER:
        print(f"   라운드 {n} 이다 — 이월된 '{INV}' 표는 계획 시점 판이라 서술이 뒤집혔을 수 "
              f"있다. 갱신까지는 아니어도 **한 번은 눈으로** 대조한다")
    if not repo_cwd:
        # 위 준비-라운드 안내와 같은 이유(code-2 P1) — 낡을 수 있는 경로 명령을 내지 않는다
        print("\n▶ 워크트리를 못 찾아 실행 명령을 생략한다 — 대상 워크트리로 이동해\n"
              "  resume.py 를 다시 실행한다(경로가 자동 이전·재계산된다).")
        return
    # --out 을 명시한다. 생략하면 run_round.py 가 자동번호로 다음 디렉토리를 새로 만들어
    # 준비한 컨텍스트와 결과가 다른 라운드로 갈라진다.
    print("\n▶ 갱신 후 실행:\n  " + shell_command(
        "python3", HOME_DIR / "scripts" / "run_round.py", "--cwd", repo_cwd,
        "--gate", a.next_gate, "--context", out / "context.md", "--out", out))


if __name__ == "__main__":
    main()
