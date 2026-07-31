#!/usr/bin/env python3
"""red-team 이어받기 — 지금 어디인지 알려주고, 다음 라운드 컨텍스트를 만들어 준다.

작업 중인 워크트리에서 이것만 실행하면 된다:
    python3 ~/.claude/skills/red-team/scripts/resume.py

게이트를 넘어갈 때(계획 GO → 구현 완료 → 코드 게이트):
    python3 .../resume.py --next code

**포인터 파일을 두지 않는다.** 라운드의 키는 경로 자체이고
(`~/.red-team/runs/<repo>/<branch>/`), 그건 저장소 위치에서 나온다.
별도 포인터를 두면 (a) 어긋날 수 있는 두 번째 진실이 생기고
(b) eval·실험 런이 진짜 작업 포인터를 덮는 사고가 난다(실제로 났다).

`--next` 가 하는 일은 사람이 잊으면 루프가 수렴하지 않는 부분이다 —
직전 라운드 context.md 를 복사하고 decisions.md 의 `반영`을 `이미 반영된 지적` 절에,
`후속 티켓`을 `스코프 밖` 절에 옮겨 붙인다. 이 누적이 재제기를 막는다.
"""
import argparse, json, os, re, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_round import branch_dir, git, slug, GATES  # 경로 파생은 한 곳에서만 한다

HOME_DIR = Path(__file__).resolve().parent.parent  # 스킬 디렉토리
RUNS = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team")) / "runs"
TODO = "<!-- TODO(resume): 이 절을 이번 라운드 기준으로 갱신하라 -->"


def resolve_base(key: str | None, cwd: str):
    """어느 `runs/<repo>/<branch>/` 를 볼지 정한다.

    key 가 없으면 현재 워크트리에서 파생한다. key(티켓·브랜치 조각)가 있으면 runs/ 를 훑어
    그 조각을 담은 브랜치 디렉토리를 찾는다 — 워크트리 밖에서도 진입할 수 있고,
    cwd 와 어긋나면 알려준다(엉뚱한 워크트리에서 구현하는 사고를 막는다).
    """
    derived = branch_dir(cwd)
    if not key:
        return derived, None
    runs = RUNS
    k = key.lower()
    hits = sorted(d for d in runs.glob("*/*") if d.is_dir() and k in d.name.lower())
    if not hits:
        avail = sorted(f"{d.parent.name}/{d.name}" for d in runs.glob("*/*") if d.is_dir())
        sys.exit(f"'{key}' 에 맞는 라운드 디렉토리가 없다.\n  있는 것: "
                 + (", ".join(avail) if avail else "(없음)"))
    if len(hits) > 1:
        sys.exit(f"'{key}' 가 여러 곳에 맞는다. 더 구체적으로 준다:\n  "
                 + "\n  ".join(f"{d.parent.name}/{d.name}" for d in hits))
    return hits[0], (derived if hits[0] != derived else None)


def worktree_for(base: Path, cwd: str, rounds_dir: Path | None):
    """이 브랜치의 워크트리 경로를 찾는다 — run_round.py 의 --cwd 로 쓸 값이다.

    ① 이미 그 워크트리 안이면 cwd ② 라운드 기록(round.json 의 repo_cwd)
    ③ 같은 저장소의 다른 체크아웃에서 `git worktree list` 로 조회.
    """
    if branch_dir(cwd) == base:
        return cwd, "현재 디렉토리"
    if rounds_dir and (rj := rounds_dir / "round.json").exists():
        try:
            if (p := json.loads(rj.read_text()).get("repo_cwd")) and Path(p).is_dir():
                return p, "라운드 기록(round.json)"
        except json.JSONDecodeError:
            pass
    out = git(cwd, "worktree", "list", "--porcelain")
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch ") and path:
            if slug(line[len("branch refs/heads/"):]) == base.name:
                return path, "git worktree list"
    return None, None


def latest_round(base: Path):
    """가장 최근 라운드와 그 상태를 찾는다.

    round.json 이 있으면 '실행됨'(정렬 키 = round.json mtime),
    없으면 '준비만 됨'(= --next 로 만들었고 아직 안 돌린 것, 키 = 디렉토리 mtime).
    번호나 게이트 순서로 정렬하지 않는 이유 — code 게이트를 돌다가 계획으로 되돌아갈 수도 있다.
    """
    rounds = []
    for gate in GATES:
        for d in base.glob(f"{gate}-*"):
            if not (d.is_dir() and re.fullmatch(rf"{gate}-\d+", d.name)):
                continue
            rj = d / "round.json"
            ran = rj.exists()
            rounds.append(((rj if ran else d).stat().st_mtime, d, gate, ran))
    if not rounds:
        return None
    rounds.sort()
    return rounds[-1][1:]


def sections(text: str) -> list[tuple[str, str]]:
    """`## ` 단위로 (heading, body) 로 쪼갠다. `###` 은 body 에 남는다."""
    parts = re.split(r"^(## .+)$", text, flags=re.M)
    out = [("", parts[0])] if parts[0].strip() else []
    for i in range(1, len(parts), 2):
        out.append((parts[i], parts[i + 1]))
    return out


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
    lines = [l for l in b.splitlines()
             if l.strip().startswith(("-", "*")) and "없" not in l and "비운다" not in l]
    return "\n".join(lines)


PER_ROUND = ("## 리뷰 대상", "## 검증 상태")
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


def carry_forward(prev_ctx: str, decisions: str, prev_round: str) -> str:
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
        elif h.startswith(("## 리뷰 대상", "## 검증 상태")) and TODO not in b:
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

    cwd = a.cwd or os.getcwd()
    base, mismatch = resolve_base(a.key, cwd)
    found = latest_round(base)
    if found is None:
        sys.exit(f"{base} 에 라운드가 없다.\n"
                 f"  여기가 맞는 저장소인지 확인하고(현재: {cwd}), 티켓 이름으로 찾으려면\n"
                 f"  `resume.py <티켓키>` 로 준다. 첫 라운드는 SKILL.md 의 '라운드 실행'부터 시작한다.")
    rd, gate, ran = found
    repo_cwd, how = worktree_for(base, cwd, rd)
    if mismatch is not None:
        print(f"⚠ 현재 디렉토리는 {mismatch.name} 인데 '{a.key}' 는 {base.name} 이다.")
        print(f"  {'구현·리뷰는 다음 워크트리에서 해야 한다: ' + repo_cwd if repo_cwd else '해당 워크트리를 못 찾았다 — 그 워크트리로 이동해 다시 실행한다.'}\n")
    keysuf = f" {a.key}" if a.key else ""
    dec_path, ctx_path = rd / "decisions.md", rd / "context.md"
    verdict, counts = "(미실행)", None
    if ran:
        rj = json.loads((rd / "round.json").read_text())
        verdict, counts = rj.get("verdict", "?"), rj.get("counts")
        if rj.get("access_errors"):
            print(f"⚠ 이 라운드에 파일접근오류가 있다 {rj['access_errors']} — 무효로 보고 재실행한다.")

    print(f"저장소   : {repo_cwd or '(못 찾음)'}" + (f"  [{how}]" if how else ""))
    print(f"직전 라운드: {rd.name}  (gate={gate}, verdict={verdict})")
    print(f"경로     : {rd}")
    if counts:
        print(f"counts   : {counts}")
    if not ctx_path.exists():
        print("⚠ context.md 가 없다 — 이 라운드는 이어받을 수 없다.")
    if not ran:
        print(f"\n▶ 이 라운드는 준비만 됐고 아직 실행되지 않았다.\n"
              f"  {ctx_path} 의 '{TODO}' 표시된 절을 채운 뒤 실행한다:\n"
              f"  python3 {HOME_DIR/'scripts'/'run_round.py'} \\\n"
              f"    --cwd {repo_cwd or '<워크트리 경로>'} --gate {gate} --context {ctx_path} --out {rd}")
        return
    if not dec_path.exists():
        print("\n▶ 다음 할 일: 이 라운드의 findings 가 아직 처리되지 않았다.\n"
              f"  {rd/'round.json'} 을 읽어 P1/P2 를 처리하고 {dec_path} 를 쓴다 (형식은 SKILL.md).")
        return
    dec = dec_path.read_text()
    if (p := pending(dec)):
        print(f"\n⚠ decisions.md 의 `보류` 가 비어 있지 않다. 결정 전에는 다음 라운드로 가지 않는다:\n{p}")
        return

    if not a.next_gate:
        print(f"\n▶ 먼저 읽을 것: {ctx_path}\n  (의도·스코프·판정 기준이 전부 여기 있다)")
        if verdict == "GO":
            nxt = "code" if gate == "plan" else "완료 — 호출한 워크플로우로 복귀"
            print(f"\n▶ 이 게이트는 통과(GO)했다. 다음: {nxt}")
            if gate == "plan":
                if doc := next((f.name for f in sorted(rd.glob("plan*.md"))), None):
                    print(f"  구현 대상 계획서: {rd/doc}")
                print(f"  구현 완료 후:  python3 {Path(__file__)} --next code{keysuf}")
        else:
            print(f"\n▶ verdict={verdict} — 같은 게이트로 라운드를 더 돈다:"
                  f"\n  python3 {Path(__file__)} --next {gate}{keysuf}")
        return

    n = 1 + max((int(m.group(1)) for d in base.glob(f"{a.next_gate}-*")
                 if (m := re.fullmatch(rf"{a.next_gate}-(\d+)", d.name))), default=0)
    out = base / f"{a.next_gate}-{n}"
    new_ctx = carry_forward(ctx_path.read_text(), dec, rd.name)
    if a.dry_run:
        print(f"\n[dry-run] 생성할 경로: {out}")
        for h, _ in sections(new_ctx):
            if h:
                print("  ", h)
        return
    out.mkdir(parents=True, exist_ok=True)
    (out / "context.md").write_text(new_ctx)
    for extra in ("plan-", "impact-"):  # 계획서·아티팩트는 게이트를 넘어가도 참조된다
        for f in rd.glob(f"{extra}*"):
            shutil.copy2(f, out / f.name)
    print(f"\n✅ {out/'context.md'} 생성 — decisions.md 의 반영/후속티켓을 이관했다.")
    print(f"   손으로 갱신할 절 2개만 남았다 ({TODO} 표시됨): '## 리뷰 대상', '## 검증 상태'")
    # --out 을 명시한다. 생략하면 run_round.py 가 자동번호로 다음 디렉토리를 새로 만들어
    # 준비한 컨텍스트와 결과가 다른 라운드로 갈라진다.
    print(f"\n▶ 갱신 후 실행:\n  python3 {HOME_DIR/'scripts'/'run_round.py'} \\\n"
          f"    --cwd {repo_cwd or '<워크트리 경로>'} --gate {a.next_gate} \\\n"
          f"    --context {out/'context.md'} --out {out}")


if __name__ == "__main__":
    main()
