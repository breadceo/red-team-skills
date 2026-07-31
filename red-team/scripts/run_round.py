#!/usr/bin/env python3
"""red-team 라운드 1회 — 리뷰어를 병렬로 돌려 raw 출력과 findings JSON 을 남긴다.

usage:
  run_round.py --set-engine codex,claude          # 최초 1회 (이후 변경도 같은 명령)
  run_round.py --cwd <repo> --context <file> [--gate code|plan] [--out <dir>]
               [--reviewers a-code,b1-state-matrix,...] [--engine codex|claude|codex,claude]
  run_round.py --cwd <repo> --gate plan --from-zax <task>   # zax:task 산출물에서 컨텍스트 초안

가용 엔진 목록은 `~/.red-team/config.json` 에 저장된다. 우선순위는
`--engine` > `RED_TEAM_ENGINE` 환경변수 > config.json 이고, 셋 다 없으면 최초 설정을 안내한다.

리뷰어는 전원 동일 스펙으로 돌지 않는다 — 축 성격별로 GATES 의 tier(deep/mid/cheap)가
모델·reasoning effort 를 정하고, prefer 가 가용 엔진 안에 있으면 그 엔진으로 분산된다
(없으면 첫 엔진으로 폴백). `--model`/`--effort` 는 전 리뷰어 강제 override 다.
배정 결과는 round.json 의 `assignments` 에 남는다.

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
    "code": {
        # 회귀·논리구멍·사실오류 — 변경 목적을 우회하는 경로 탐색, 복합 추론
        "a-code":              {"tier": "deep",  "prefer": "codex"},
        # surface × 상태 표 전수 — 체크리스트형. 코드는 읽어야 하니 low 가 아닌 medium
        "b1-state-matrix":     {"tier": "cheap", "prefer": "codex"},
        # 클릭→핸들러→데이터소스 추적 — 다단계 교차 추론.
        # deep 축을 codex/claude 로 갈라 같은 모델의 맹점이 전 축에 복제되는 것을 막는다
        "b2-interaction":      {"tier": "deep",  "prefer": "claude"},
        # 최종 hex 확정 + 대비비 계산 — 기계적
        "b3-visibility":       {"tier": "cheap", "prefer": "claude"},
        # "판정 불가 vs 판정 결과 불가" 합쳐짐 감지 — 미묘하지만 범위가 좁다
        "b4-null-propagation": {"tier": "mid",   "prefer": "claude"},
    },
    # 계획 결함은 구현 후 발견보다 압도적으로 싸다 — 여기 아끼지 않는다
    "plan": {"a-plan": {"tier": "deep", "prefer": "codex"}},
}
# --reviewers 로 GATES 밖 커스텀 축을 주면 이 스펙으로 돈다 — 성격을 모르면 비싼 쪽이 안전하다
DEFAULT_SPEC = {"tier": "deep", "prefer": None}

# tier → (model, effort). 결함 탐지(deep)는 recall 우선 — CodeRabbit 리뷰 벤치마크에서
# Sol recall 69.7% vs Terra 52.5%, Sol 의 높은 FP 는 P1/P2 분류·사용자 게이트가 거른다.
# mid 는 Terra 의 recall 약점을 effort=high 로 보완. claude 하위 티어는 sonnet/medium 통일(haiku 금지).
TIERS = {
    "codex": {
        "deep":  ("gpt-5.6-sol",   "high"),
        "mid":   ("gpt-5.6-terra", "high"),
        "cheap": ("gpt-5.6-luna",  "medium"),
    },
    "claude": {
        "deep":  ("opus",   "high"),
        "mid":   ("sonnet", "medium"),
        "cheap": ("sonnet", "medium"),
    },
}

ACPX = shutil.which("acpx") or os.path.expanduser("~/.local/share/mise/shims/acpx")

CONFIG = HOME_DIR / "config.json"
ENGINES = ("codex", "claude")


def engine_cmd(engine: str, prompt: str, cwd: str, model: str | None,
               effort: str | None) -> tuple[list[str], dict]:
    """엔진별 (argv, env). 프롬프트 조립·JSON 추출·판정 병합은 엔진과 무관하므로,
    엔진을 바꾸는 일은 여기 한 곳을 갈아끼우는 것으로 끝난다."""
    if engine == "codex":
        # effort 는 CODEX_CONFIG env 로 준다 — acpx 는 codex 의 `-c` 를 노출하지 않지만
        # codex-acp 어댑터가 이 env 의 JSON 을 세션 config 에 병합한다(어댑터 README).
        # model 은 검증된 경로인 acpx --model 로 준다.
        env = dict(os.environ)
        if effort:
            env["CODEX_CONFIG"] = json.dumps({"model_reasoning_effort": effort})
        return [ACPX, "--approve-all", "--non-interactive-permissions", "deny", "--cwd", cwd,
                *(["--model", model] if model else []), "codex", "exec", prompt], env
    if engine == "claude":
        # hook·skill 을 끈다 — 리뷰어 세션에 다른 스킬의 프로토콜이 끼면 턴 하나를
        # 그쪽에 다 쓰고 리뷰가 밀린다(hipocampus FIRST RESPONSE RULE 로 실측).
        # `--bare` 를 쓰지 않는다: 그것까지 끄면 인증도 함께 끊겨
        # `Not logged in · Please run /login` 한 줄만 받는다(실측). 로그인은 정상인데도 그렇다.
        return ["claude", "-p", prompt, "--disable-slash-commands",
                "--settings", '{"hooks":{}}', *(["--model", model] if model else []),
                *(["--effort", effort] if effort else []),
                "--allowedTools", "Read,Grep,Glob,Bash"], dict(os.environ)
    sys.exit(f"모르는 엔진: {engine} — {'|'.join(ENGINES)} 중 하나여야 한다.")


def parse_engines(spec: str) -> list[str]:
    """콤마 목록을 검증한다. 첫 항목이 기본(폴백) 엔진이다."""
    engines = list(dict.fromkeys(e.strip() for e in spec.split(",") if e.strip()))
    bad = [e for e in engines if e not in ENGINES]
    if bad or not engines:
        sys.exit(f"모르는 엔진: {', '.join(bad) or '(빈 값)'} — {'|'.join(ENGINES)} 를 콤마로 잇는다.")
    return engines


def resolve_engines(cli_engine: str | None) -> list[str]:
    """--engine > RED_TEAM_ENGINE > config.json. 아무것도 없으면 최초 설정으로 돌려보낸다.

    반환 리스트가 이번 라운드의 가용 엔진이다 — 리뷰어별 prefer 는 이 안에 있을 때만
    존중되고, 없으면 첫 항목으로 폴백한다. 단일 엔진이면 자연히 전 리뷰어가 통일된다.
    """
    spec = cli_engine or os.environ.get("RED_TEAM_ENGINE")
    if not spec and CONFIG.exists():
        cfg = json.loads(CONFIG.read_text())
        stored = cfg.get("engines") or ([cfg["engine"]] if cfg.get("engine") else [])
        if stored:
            return parse_engines(",".join(stored))
    if not spec:
        sys.exit("리뷰 엔진이 설정되지 않았다. 최초 1회만 정하면 된다:\n"
                 f"  python3 {__file__} --set-engine codex          # 한 엔진만\n"
                 f"  python3 {__file__} --set-engine codex,claude   # 축별 분산 (첫 항목이 기본)\n"
                 "이후 바꿀 때도 같은 명령이다. SKILL.md 0단계 참고.")
    return parse_engines(spec)


def set_engine(spec: str) -> None:
    engines = parse_engines(spec)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    cfg["engines"] = engines
    cfg["engine"] = engines[0]  # 단수 키만 읽는 구버전 리더 호환
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    warn = ""
    for e in engines:
        binary = "acpx" if e == "codex" else "claude"
        if not (shutil.which(binary) or (e == "codex" and Path(ACPX).exists())):
            warn += f"\n⚠ `{binary}` 를 PATH 에서 찾지 못했다 — 설치 후 라운드를 돌린다."
    print(f"리뷰 엔진: {', '.join(engines)}  ({CONFIG}){warn}")


def assign(reviewer: str, gate: str, engines: list[str], model_override: str | None,
           effort_override: str | None) -> tuple[str, str | None, str | None, str]:
    """리뷰어 → (engine, model, effort, tier). CLI override 가 있으면 전 리뷰어에 강제된다."""
    spec = GATES[gate].get(reviewer, DEFAULT_SPEC)
    engine = spec["prefer"] if spec["prefer"] in engines else engines[0]
    model, effort = TIERS[engine][spec["tier"]]
    return engine, model_override or model, effort_override or effort, spec["tier"]


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


ZB_TASK_HOME = Path(os.environ.get("ZB_TASK_HOME", Path.home() / ".zb-task"))


def section(text: str, heading: str) -> str:
    """`## heading` 절의 본문. 없으면 빈 문자열."""
    m = re.search(rf"^##+\s*{re.escape(heading)}.*?\n(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def zax_draft(task: str, gate: str) -> tuple[Path, bool]:
    """zax:task 산출물에서 리뷰 컨텍스트 초안을 만든다.

    반환은 (컨텍스트 경로, 새로 만들었나). **이미 있으면 덮지 않는다** — 판정 기준
    (불변식·스코프 경계·확인 사항)은 사람이 갱신하는 문서이고, 라운드마다 자동 재생성하면
    손으로 좁혀둔 스코프가 조용히 날아간다. 초안은 첫 라운드 전에 한 번만 깔아 준다.

    컨텍스트를 task 디렉토리에 두는 이유: PLAN.md 와 같은 디렉토리에 있어야 라운드 시작
    시점의 신선도 경고(계획서가 컨텍스트보다 새로움)가 작동한다.
    """
    tdir = ZB_TASK_HOME / task
    if not tdir.is_dir():
        sys.exit(f"zax task 디렉토리가 없다: {tdir}\n"
                 f"  `/task plan` 으로 PLAN.md 를 먼저 만든다 (또는 ZB_TASK_HOME 을 지정한다).")
    ctx = tdir / "redteam-context.md"
    if ctx.exists():
        return ctx, False

    plan = next((p for p in tdir.iterdir() if p.name.lower() in ("plan.md", "plan.markdown")), None)
    if plan is None:
        sys.exit(f"{tdir} 에 PLAN.md 가 없다 — `/task plan` 을 먼저 돌린다.")
    ptext = plan.read_text()
    ctext = (tdir / "CONTEXT.md").read_text() if (tdir / "CONTEXT.md").exists() else ""

    intent = "\n\n".join(x for x in (
        section(ptext, "작업 분석"), section(ctext, "PRD 요약"), section(ctext, "Architecture 합의")) if x)
    unknown = "\n".join(l for l in ptext.splitlines() if "미확인" in l)
    # AC·시나리오는 리뷰어의 판정 기준이다 — 안 실으면 코드에서 역추론한다.
    criteria = "\n\n".join(x for x in (
        section(ctext, "Spec AC 매핑"), section(ctext, "Gherkin 시나리오")) if x)
    target = ("`git diff <base>..HEAD` (또는: 커밋되지 않은 작업 트리 변경)\n"
              "저장소: <--cwd 로 준 경로>. 파일 내용은 현재 체크아웃 상태가 맞다."
              if gate == "code" else
              "아래 `## 계획 전문` 의 계획. 코드는 현재 체크아웃 상태를 근거로 확인한다.")

    body = [f"## 리뷰 대상\n{target}",
            f"## 이 변경이 하려는 것\n{intent or '<PLAN.md 작업 분석이 비어 있다 — 직접 채운다>'}"]
    if criteria:
        body.append("## 판정 기준 (Spec AC · Gherkin)\n"
                    "아래 AC·시나리오가 이 변경의 판정 기준이다. "
                    "여기 적힌 동작이 코드에서 실제로 그렇게 되는지 본다.\n\n" + criteria)
    body += ["## 스코프 밖 (지적 금지)\n"
            "<PLAN.md 범위의 '제외되는 것' 과 후속 티켓으로 분리한 것을 여기 옮긴다>"
            + (f"\n\n미확인으로 남은 것(리뷰어가 볼 지점):\n{unknown}" if unknown else ""),
            "## 이미 반영된 지적 (재제기 금지)\n"
            "<2라운드부터 `resume.py` 가 직전 라운드 decisions.md 에서 끌어온다>",
            "## 검증 상태\n<`/task done` 의 tsc·lint·test 결과를 붙인다 — "
            "비워두면 리뷰어가 '테스트 없음' 을 지적하느라 시간을 쓴다>"]
    if gate == "plan":
        body.append(f"## 계획 전문\n{ptext.strip()}")
    ctx.write_text("\n\n".join(body) + "\n")
    return ctx, True


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


def run(reviewer: str, cwd: str, out: Path, context: str, timeout: int,
        assignment: tuple[str, str | None, str | None, str]):
    engine, model, effort, _tier = assignment
    prompt = build(reviewer, context)
    (out / f"{reviewer}.prompt.md").write_text(prompt)
    cmd, env = engine_cmd(engine, prompt, cwd, model, effort)
    try:
        p = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=timeout, env=env)
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
    label = f"[{engine}/{model or 'default'}/{effort or 'default'}]"
    if parsed is None:
        print(f"  {reviewer:24} PARSE-FAIL  {label}{warn}", flush=True)
    else:
        print(f"  {reviewer:24} {len(parsed['findings'])} findings  "
              f"{parsed.get('verdict', '?')}  {label}{warn}", flush=True)
    return reviewer, parsed, lost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd")
    ap.add_argument("--context", help="컨텍스트 md 파일 경로")
    ap.add_argument("--from-zax", metavar="TASK", default=None,
                    help="zax:task 산출물에서 컨텍스트를 잡는다 (~/.zb-task/<TASK>/). "
                         "초안이 없으면 만들고 멈춘다 — 검토 후 다시 실행한다")
    ap.add_argument("--gate", choices=list(GATES), default="code")
    ap.add_argument("--out", default=None, help="생략 시 ~/.red-team/runs/... 로 자동 결정")
    ap.add_argument("--reviewers", default=None, help="생략 시 게이트 기본값")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--model", default=None, help="전 리뷰어 모델 강제 (생략 시 축별 tier 배정)")
    ap.add_argument("--effort", default=None, help="전 리뷰어 effort 강제 (생략 시 축별 tier 배정)")
    ap.add_argument("--engine", default=None,
                    help="이번 라운드만 가용 엔진을 바꾼다 — 단일(codex)이면 전 리뷰어 통일, "
                         "콤마(codex,claude)면 축별 분산")
    ap.add_argument("--set-engine", default=None, metavar="ENGINES",
                    help="가용 엔진을 저장하고 종료 (예: codex 또는 codex,claude — 첫 항목이 기본)")
    a = ap.parse_args()

    if a.set_engine:
        set_engine(a.set_engine)
        return
    if a.from_zax:
        if a.context:
            ap.error("--from-zax 와 --context 는 함께 쓸 수 없다")
        ctx_path, drafted = zax_draft(a.from_zax, a.gate)
        a.context = str(ctx_path)
        if drafted:
            print(f"컨텍스트 초안을 만들었다: {ctx_path}\n"
                  f"  스코프 밖·검증 상태를 채운 뒤 같은 명령을 다시 실행한다.\n"
                  f"  (판정 기준은 사람이 정한다 — 라운드마다 자동 재생성하지 않는다)")
            return
    if not (a.cwd and a.context):
        ap.error("--cwd 와 (--context 또는 --from-zax) 가 필요하다")
    engines = resolve_engines(a.engine)

    out = Path(a.out) if a.out else resolve_out(a.cwd, a.gate)
    out.mkdir(parents=True, exist_ok=True)
    context = Path(a.context).read_text()
    # 컨텍스트를 라운드 디렉토리에 복사한다 — 라운드가 자체로 재현 가능해야 한다
    (out / "context.md").write_text(context)
    # `--reviewers ""` 는 "리뷰어 없이 준비 확인만" 이다 — 기본값 폴백(`or`)으로 처리하면
    # 빈 지정이 조용히 게이트 전체가 되어, 준비 확인용 실행이 실제 엔진 호출로 번진다(실측: 테스트가 opus 를 돌렸다).
    spec = a.reviewers if a.reviewers is not None else ",".join(GATES[a.gate])
    reviewers = [r.strip() for r in spec.split(",") if r.strip()]
    assignments = {r: assign(r, a.gate, engines, a.model, a.effort) for r in reviewers}
    if reviewers:
        print(f"round: gate={a.gate}, engines={'+'.join(engines)}, {len(reviewers)} reviewers, cwd={a.cwd}\n"
              f"  out: {out}", flush=True)

    # 계획서가 컨텍스트보다 새로우면 판정 기준이 낡았을 수 있다.
    # 구현 중 새 사실이 나와 계획을 고쳤는데 context.md 를 안 고치면, 리뷰어는 낡은 기준으로
    # 판정한다 — 조용히 틀린 GO 가 나오는 경로라 라운드 시작 시점에 알린다.
    # 파일명 대소문자를 가리지 않는다 — zax:task 는 `PLAN.md`, 손으로 쓸 때는 `plan-1.md` 다.
    # (Python glob 은 대소문자를 구분하므로 `plan*.md` 만 보면 `PLAN.md` 를 놓친다)
    ctx_src = Path(a.context)
    plans = sorted(p for p in ctx_src.parent.iterdir()
                   if p.is_file() and re.fullmatch(r"plan.*\.md", p.name, re.I))
    for plan in plans:
        if plan.stat().st_mtime > ctx_src.stat().st_mtime + 1:
            print(f"⚠ {plan.name} 이 {ctx_src.name} 보다 새롭다.\n"
                  f"  계획을 고쳤다면 context.md 의 판정 기준(불변식·스코프 밖·확인 사항)도 같이 갱신했는지\n"
                  f"  확인한다. 계획서만 고치면 리뷰어가 낡은 기준으로 판정한다.", flush=True)

    if not reviewers:
        print("리뷰어 없음(--reviewers \"\") — 준비 확인만 했고 라운드는 돌리지 않는다.")
        return

    with ThreadPoolExecutor(max_workers=len(reviewers)) as ex:
        results = list(ex.map(
            lambda r: run(r, a.cwd, out, context, a.timeout, assignments[r]), reviewers))

    # repo_cwd 는 '이 라운드가 어디를 리뷰했나'는 라운드별 불변 기록이다.
    # 전역 가변 포인터와 다르다 — 덮어쓰이지 않고, 티켓 이름으로 진입할 때 워크트리를 찾는 근거가 된다.
    # reviewers 값은 verdict 문자열 규격을 유지한다(summarize_round.py 가 그 형태를 읽는다) —
    # 배정 상세는 assignments 에 따로 남긴다.
    merged = {"repo_cwd": str(Path(a.cwd).resolve()), "gate": a.gate,
              "engine": "+".join(engines),
              "assignments": {r: {"engine": e, "model": m, "effort": ef, "tier": t}
                              for r, (e, m, ef, t) in assignments.items()},
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
              f"  engines={'+'.join(engines)} 이 실제로 돌았는지 확인한다"
              f"({out}/*.txt 첫 줄이 흔히 이유를 말해준다).")
    else:
        # 혼합 라운드에서 한 엔진만 통째로 죽으면(예: claude 로그인 풀림) 나머지 엔진의
        # GO 에 묻혀 조용히 통과한다 — 7/30 `Not logged in` 사고의 재발 경로라 표면화한다.
        by_engine = {}
        for r, parsed, _lost in results:
            by_engine.setdefault(assignments[r][0], []).append(parsed is None)
        for e, fails in by_engine.items():
            if all(fails):
                print(f"⚠ engine={e} 리뷰어 전원({sum(fails)}명)이 결과를 내지 못했다 — "
                      f"그 축들이 빠진 {merged['verdict']} 는 반쪽짜리다. "
                      f"{e} 상태를 확인하고 라운드를 재실행한다.")
    if merged["access_errors"]:
        print(f"⚠ 파일접근오류: {merged['access_errors']} — 이 라운드는 무효로 보고 재실행한다.\n"
              f"  리뷰 대상 디렉토리가 실행 중 사라지지 않는 위치인지 확인한다.")


if __name__ == "__main__":
    main()
