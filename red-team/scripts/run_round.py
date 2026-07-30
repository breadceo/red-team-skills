#!/usr/bin/env python3
"""red-team 라운드 1회 — 리뷰어를 병렬로 돌려 raw 출력과 findings JSON 을 남긴다.

usage:
  run_round.py --set-engine codex|claude          # 최초 1회 (이후 변경도 같은 명령)
  run_round.py --cwd <repo> --context <file> [--gate code|plan] [--out <dir>]
               [--reviewers a-code,b1-state-matrix,...] [--engine codex|claude]

리뷰 엔진은 `~/.red-team/config.json` 에 저장된다. 우선순위는
`--engine` > `RED_TEAM_ENGINE` 환경변수 > config.json 이고, 셋 다 없으면 최초 설정을 안내한다.

산출물은 기본적으로 저장소 밖 `~/.red-team/runs/<repo>/<branch>/<gate>-<n>/` 에 쌓인다 —
리뷰 대상 저장소를 오염시키지 않고, 라운드 간 컨텍스트가 보존되어 다음 라운드가 이어진다.
`--out` 을 주면 그 경로를 그대로 쓴다(eval 용).

리뷰어 id 는 prompts/<id>.md 의 파일명이다. 기본값은 게이트에 따라 정해진다.
stdin 은 DEVNULL 로 고정한다 — codex 는 stdin 이 TTY 가 아니면 EOF 를 기다리며 교착한다.
"""
import argparse, json, os, re, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PROMPTS = SKILL / "prompts"
HOME_DIR = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team"))
GATES = {
    "code": ["a-code", "b1-state-matrix", "b2-interaction", "b3-visibility", "b4-null-propagation"],
    "plan": ["a-plan"],
}

ACPX = shutil.which("acpx") or os.path.expanduser("~/.local/share/mise/shims/acpx")

CONFIG = HOME_DIR / "config.json"
ENGINES = ("codex", "claude")


def engine_cmd(engine: str, prompt: str, cwd: str, model: str | None) -> list[str]:
    """엔진별 argv. 프롬프트 조립·JSON 추출·판정 병합은 엔진과 무관하므로,
    엔진을 바꾸는 일은 여기 한 곳을 갈아끼우는 것으로 끝난다."""
    if engine == "codex":
        return [ACPX, "--approve-all", "--non-interactive-permissions", "deny", "--cwd", cwd,
                *(["--model", model] if model else []), "codex", "exec", prompt]
    if engine == "claude":
        # hook·skill 을 끈다 — 리뷰어 세션에 다른 스킬의 프로토콜이 끼면 턴 하나를
        # 그쪽에 다 쓰고 리뷰가 밀린다(hipocampus FIRST RESPONSE RULE 로 실측).
        # `--bare` 를 쓰지 않는다: 그것까지 끄면 인증도 함께 끊겨
        # `Not logged in · Please run /login` 한 줄만 받는다(실측). 로그인은 정상인데도 그렇다.
        return ["claude", "-p", prompt, "--disable-slash-commands",
                "--settings", '{"hooks":{}}', *(["--model", model] if model else []),
                "--allowedTools", "Read,Grep,Glob,Bash"]
    sys.exit(f"모르는 엔진: {engine} — {'|'.join(ENGINES)} 중 하나여야 한다.")


def resolve_engine(cli_engine: str | None) -> str:
    """--engine > RED_TEAM_ENGINE > config.json. 아무것도 없으면 최초 설정으로 돌려보낸다."""
    engine = cli_engine or os.environ.get("RED_TEAM_ENGINE")
    if not engine and CONFIG.exists():
        engine = json.loads(CONFIG.read_text()).get("engine")
    if not engine:
        sys.exit("리뷰 엔진이 설정되지 않았다. 최초 1회만 정하면 된다:\n"
                 f"  python3 {__file__} --set-engine codex    # OpenAI codex CLI (acpx 경유)\n"
                 f"  python3 {__file__} --set-engine claude   # Claude Code headless (claude -p)\n"
                 "이후 바꿀 때도 같은 명령이다. SKILL.md 0단계 참고.")
    return engine


def set_engine(engine: str) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    cfg["engine"] = engine
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    binary = "acpx" if engine == "codex" else "claude"
    warn = "" if shutil.which(binary) or (engine == "codex" and Path(ACPX).exists()) \
        else f"\n⚠ `{binary}` 를 PATH 에서 찾지 못했다 — 설치 후 라운드를 돌린다."
    print(f"리뷰 엔진: {engine}  ({CONFIG}){warn}")


def git(cwd: str, *args) -> str:
    try:
        return subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-") or "unknown"


def branch_dir(cwd: str) -> Path:
    """~/.red-team/runs/<repo>/<branch> — 저장소 위치에서 결정된다.

    **이것이 라운드의 키다.** 별도 포인터 파일을 두지 않는 이유가 여기 있다 —
    작업 중인 워크트리만 있으면 경로가 나오고, 어긋날 수 있는 두 번째 진실이 생기지 않는다.
    resume.py 도 이 함수를 쓴다(각자 계산하면 조용히 다른 디렉토리를 가리킬 수 있다).
    """
    origin = git(cwd, "remote", "get-url", "origin")
    repo = slug(re.sub(r"\.git$", "", origin.rsplit("/", 1)[-1])) if origin else \
        slug(Path(git(cwd, "rev-parse", "--show-toplevel") or cwd).name)
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if branch in ("", "HEAD"):  # detached
        branch = git(cwd, "rev-parse", "--short", "HEAD") or "detached"
    return HOME_DIR / "runs" / repo / slug(branch)


def resolve_out(cwd: str, gate: str) -> Path:
    """다음 라운드 디렉토리 — `<gate>-<n>` 의 n 은 자동 증가."""
    base = branch_dir(cwd)
    n = 1 + max((int(m.group(1)) for d in base.glob(f"{gate}-*")
                 if (m := re.fullmatch(rf"{gate}-(\d+)", d.name))), default=0)
    return base / f"{gate}-{n}"


def build(reviewer: str, context: str) -> str:
    tpl = (PROMPTS / "_common.md").read_text()
    axis = (PROMPTS / f"{reviewer}.md").read_text()
    return tpl.replace("{{CONTEXT}}", context).replace("{{AXIS}}", axis)


def extract_json(raw: str):
    """마지막 fenced json 블록을 findings 로 읽는다. 없으면 None."""
    blocks = re.findall(r"```json\s*\n(.*?)\n```", raw, re.S)
    for b in reversed(blocks):
        try:
            obj = json.loads(b)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "findings" in obj:
            return obj
    return None


def run(reviewer: str, cwd: str, out: Path, context: str, timeout: int, model: str | None,
        engine: str):
    prompt = build(reviewer, context)
    (out / f"{reviewer}.prompt.md").write_text(prompt)
    cmd = engine_cmd(engine, prompt, cwd, model)
    try:
        p = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=timeout)
        raw = p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        raw = f"[TIMEOUT after {timeout}s]\n" + (e.stdout or "") + (e.stderr or "")
    (out / f"{reviewer}.txt").write_text(raw)
    # 리뷰어가 대상 코드를 못 읽으면 findings 가 조용히 비어 GO 로 보인다.
    # 그 라운드를 정상 결과로 채점하면 틀린 결론이 나오므로 반드시 표면화한다.
    # 리뷰어가 없는 파일 경로를 추측하는 것은 정상이므로, cwd 자체에 닿지 못한 경우만 센다.
    lost = sum(1 for line in raw.splitlines()
               if cwd in line and ("No such file or directory" in line or "not a git repository" in line))
    parsed = extract_json(raw)
    (out / f"{reviewer}.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2)
                                          if parsed else "null")
    warn = f"  ⚠ 파일접근오류 {lost}건 — 이 리뷰어 결과는 신뢰할 수 없다" if lost else ""
    if parsed is None:
        print(f"  {reviewer:24} PARSE-FAIL{warn}", flush=True)
    else:
        print(f"  {reviewer:24} {len(parsed['findings'])} findings  "
              f"{parsed.get('verdict', '?')}{warn}", flush=True)
    return reviewer, parsed, lost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd")
    ap.add_argument("--context", help="컨텍스트 md 파일 경로")
    ap.add_argument("--gate", choices=list(GATES), default="code")
    ap.add_argument("--out", default=None, help="생략 시 ~/.red-team/runs/... 로 자동 결정")
    ap.add_argument("--reviewers", default=None, help="생략 시 게이트 기본값")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--model", default=None)
    ap.add_argument("--engine", choices=ENGINES, default=None, help="이번 라운드만 다른 엔진")
    ap.add_argument("--set-engine", choices=ENGINES, default=None,
                    help="기본 리뷰 엔진을 저장하고 종료 (최초 1회 / 변경 시)")
    a = ap.parse_args()

    if a.set_engine:
        set_engine(a.set_engine)
        return
    if not (a.cwd and a.context):
        ap.error("--cwd 와 --context 가 필요하다")
    engine = resolve_engine(a.engine)

    out = Path(a.out) if a.out else resolve_out(a.cwd, a.gate)
    out.mkdir(parents=True, exist_ok=True)
    context = Path(a.context).read_text()
    # 컨텍스트를 라운드 디렉토리에 복사한다 — 라운드가 자체로 재현 가능해야 한다
    (out / "context.md").write_text(context)
    reviewers = [r.strip() for r in (a.reviewers or ",".join(GATES[a.gate])).split(",") if r.strip()]
    print(f"round: gate={a.gate}, engine={engine}, {len(reviewers)} reviewers, cwd={a.cwd}\n"
          f"  out: {out}", flush=True)

    # 계획서가 컨텍스트보다 새로우면 판정 기준이 낡았을 수 있다.
    # 구현 중 새 사실이 나와 계획을 고쳤는데 context.md 를 안 고치면, 리뷰어는 낡은 기준으로
    # 판정한다 — 조용히 틀린 GO 가 나오는 경로라 라운드 시작 시점에 알린다.
    ctx_src = Path(a.context)
    for plan in sorted(ctx_src.parent.glob("plan*.md")):
        if plan.stat().st_mtime > ctx_src.stat().st_mtime + 1:
            print(f"⚠ {plan.name} 이 {ctx_src.name} 보다 새롭다.\n"
                  f"  계획을 고쳤다면 context.md 의 판정 기준(불변식·스코프 밖·확인 사항)도 같이 갱신했는지\n"
                  f"  확인한다. 계획서만 고치면 리뷰어가 낡은 기준으로 판정한다.", flush=True)

    with ThreadPoolExecutor(max_workers=len(reviewers)) as ex:
        results = list(ex.map(
            lambda r: run(r, a.cwd, out, context, a.timeout, a.model, engine), reviewers))

    # repo_cwd 는 '이 라운드가 어디를 리뷰했나'는 라운드별 불변 기록이다.
    # 전역 가변 포인터와 다르다 — 덮어쓰이지 않고, 티켓 이름으로 진입할 때 워크트리를 찾는 근거가 된다.
    merged = {"repo_cwd": str(Path(a.cwd).resolve()), "gate": a.gate, "engine": engine,
              "reviewers": {}, "findings": [], "access_errors": {}}
    for r, parsed, lost in results:
        merged["reviewers"][r] = parsed.get("verdict") if parsed else "PARSE-FAIL"
        if lost:
            merged["access_errors"][r] = lost
        for f in (parsed or {}).get("findings", []):
            f.setdefault("axis", r)
            f["reviewer"] = r
            merged["findings"].append(f)
    reg = [f for f in merged["findings"] if f.get("classification") == "regression"]
    # 아무도 결과를 못 낸 라운드는 GO 가 아니라 무효다. findings 0 == 결함 없음 처럼 보이지만
    # 엔진이 로그인 안 됐거나 세션이 끊긴 경우와 구별되지 않는다 — claude 엔진 첫 실측에서
    # `Not logged in` 한 줄만 받고도 GO 가 찍혔다. 그 GO 를 믿으면 리뷰 없이 커밋한다.
    if all(v == "PARSE-FAIL" for v in merged["reviewers"].values()):
        merged["verdict"] = "INVALID"
    else:
        merged["verdict"] = "NO-GO" if reg else "GO"
    merged["counts"] = {
        "regression_P1": sum(1 for f in reg if f.get("severity") == "P1"),
        "regression_P2": sum(1 for f in reg if f.get("severity") == "P2"),
        "non_regression": len(merged["findings"]) - len(reg),
    }
    (out / "round.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2))

    print(f"\n{merged['verdict']}  {merged['counts']}")
    if merged["verdict"] == "INVALID":
        print(f"⚠ 리뷰어 전원이 결과를 내지 못했다 — 이 라운드는 판정이 아니다.\n"
              f"  engine={engine} 이 실제로 돌았는지 확인한다"
              f"({out}/*.txt 첫 줄이 흔히 이유를 말해준다).")
    if merged["access_errors"]:
        print(f"⚠ 파일접근오류: {merged['access_errors']} — 이 라운드는 무효로 보고 재실행한다.\n"
              f"  리뷰 대상 디렉토리가 실행 중 사라지지 않는 위치인지 확인한다.")


if __name__ == "__main__":
    main()
