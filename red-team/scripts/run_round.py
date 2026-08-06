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
프롬프트를 stdin 으로 받는 엔진(codex)은 프롬프트를 흘려보낸 뒤 stdin 을 닫아 EOF 를 준다.
그렇지 않은 엔진은 stdin 을 DEVNULL 로 고정한다 — 열어 두면 EOF 를 기다리며 교착한다.
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PROMPTS = SKILL / "prompts"
HOME_DIR = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team"))
# why 는 --show-assignments 가 "이 축은 어떤 일을 하니 무엇을 추천하나"로 출력한다.
# core: --lean(MoE) 중간 라운드에서도 항상 도는 축. core 가 아닌 축은 "생략"이 아니라
# "유예"다 — coverage 가 partial 인 GO 는 게이트 통과가 아니고, top-up 병합으로 채워야 확정된다.
GATES = {
    "code": {
        "a-code":              {"tier": "deep",  "prefer": "codex", "core": True,
                                "why": "회귀·논리구멍·사실오류 — 변경 목적을 우회하는 경로 탐색, 복합 추론. recall 우선"},
        "b1-state-matrix":     {"tier": "cheap", "prefer": "codex",
                                "why": "surface × 상태 표 전수 — 체크리스트형. 코드는 읽어야 하니 low 가 아닌 medium"},
        "b2-interaction":      {"tier": "deep",  "prefer": "claude",
                                "why": "클릭→핸들러→데이터소스 다단계 추적. deep 을 codex/claude 로 갈라 맹점 분산"},
        "b3-visibility":       {"tier": "cheap", "prefer": "claude",
                                "why": "최종 hex 확정 + 대비비 계산 — 기계적"},
        "b4-null-propagation": {"tier": "mid",   "prefer": "claude",
                                "why": "'판정 불가 vs 판정 결과 불가' 합쳐짐 감지 — 미묘하지만 범위가 좁다"},
    },
    "plan": {
        "a-plan":          {"tier": "deep", "prefer": "codex", "core": True,
                            "why": "계획 결함은 구현 후 발견보다 압도적으로 싸다 — 여기 아끼지 않는다"},
        # 계획 게이트가 1축이던 동안 순서·경합 계열이 전부 코드 게이트로 새어나갔다
        # (B2C-52953: plan 1라운드 GO → code 9라운드, 그중 3라운드가 자체 회귀 검산).
        "b5-plan-ordering": {"tier": "mid", "prefer": "claude",
                            "why": "무효화 타이밍·응답 도착 순서·외부 신호의 보장 범위 — a-plan 과 겹치지 않는 시간축"},
    },
}
# --reviewers 로 GATES 밖 커스텀 축을 주면 이 스펙으로 돈다 — 성격을 모르면 비싼 쪽이 안전하다
DEFAULT_SPEC = {"tier": "deep", "prefer": None, "why": "커스텀 축 — 성격을 모르면 비싼 쪽이 안전하다"}

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
               effort: str | None) -> tuple[list[str], dict, str | None]:
    """엔진별 (argv, env, stdin). 프롬프트 조립·JSON 추출·판정 병합은 엔진과 무관하므로,
    엔진을 바꾸는 일은 여기 한 곳을 갈아끼우는 것으로 끝난다.

    stdin 이 None 이 아니면 그 문자열을 자식 stdin 으로 흘려보낸다(프롬프트가 argv 에 없다)."""
    if engine == "codex":
        # effort 는 CODEX_CONFIG env 로 준다 — acpx 는 codex 의 `-c` 를 노출하지 않지만
        # codex-acp 어댑터가 이 env 의 JSON 을 세션 config 에 병합한다(어댑터 README).
        # model 은 검증된 경로인 acpx --model 로 준다.
        # `--format json`(ACP JSON-RPC 스트림)은 토큰 사용량을 받기 위한 것이다 — parse_output 참고.
        env = dict(os.environ)
        # 설정이 없으면 Codex 기본 홈(~/.codex)을 쓴다. 호출 셸의 CODEX_HOME은
        # 리뷰어 설정으로 쓰지 않는다.
        env.pop("CODEX_HOME", None)
        codex_home = load_cfg().get("codex_home")
        if isinstance(codex_home, str) and codex_home:
            env["CODEX_HOME"] = codex_home
        if effort:
            env["CODEX_CONFIG"] = json.dumps({"model_reasoning_effort": effort})
        # 프롬프트는 argv 가 아니라 stdin 으로 준다. argv 로 주면 acpx/codex 가 SIGKILL 로
        # 죽는다 — 실측: ASCII 660B 통과, 한글 1.8KB·ASCII 5KB 사망(결정적). 리뷰 프롬프트는
        # 컨텍스트+diff 스냅샷까지 붙어 100KB 를 넘으므로 argv 경로는 항상 죽는 경로였다
        # (산출물 0바이트, 4회 재현). `codex exec --file -` 가 stdin 을 읽는다(acpx 0.11.2 확인).
        return [ACPX, "--approve-all", "--non-interactive-permissions", "deny", "--cwd", cwd,
                *(["--model", model] if model else []), "--format", "json",
                "codex", "exec", "--file", "-"], env, prompt
    if engine == "claude":
        # hook·skill 을 끈다 — 리뷰어 세션에 다른 스킬의 프로토콜이 끼면 턴 하나를
        # 그쪽에 다 쓰고 리뷰가 밀린다(hipocampus FIRST RESPONSE RULE 로 실측).
        # `--bare` 를 쓰지 않는다: 그것까지 끄면 인증도 함께 끊겨
        # `Not logged in · Please run /login` 한 줄만 받는다(실측). 로그인은 정상인데도 그렇다.
        return ["claude", "-p", prompt, "--disable-slash-commands",
                "--settings", '{"hooks":{}}', *(["--model", model] if model else []),
                *(["--effort", effort] if effort else []),
                "--output-format", "json",
                "--allowedTools", "Read,Grep,Glob,Bash"], dict(os.environ), None
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


def load_cfg() -> dict:
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def cfg_engines(cfg: dict) -> list[str]:
    return cfg.get("engines") or ([cfg["engine"]] if cfg.get("engine") else [])


def assign(reviewer: str, gate: str, engines: list[str], model_override: str | None,
           effort_override: str | None,
           overrides: dict | None = None) -> tuple[str, str | None, str | None, str]:
    """리뷰어 → (engine, model, effort, tier).

    우선순위: CLI --model/--effort(전 리뷰어 강제) > config assignments[축] > tier/prefer 기본.
    오버라이드의 engine 이 가용 목록 밖이면 **오버라이드 전체를 무시한다** — 한도 소진 시
    `--set-engine codex` 한 방 전환이 축별 설정에 발목 잡히면 안 된다 (모델·effort 는
    엔진에 종속이라 엔진이 무시되면 함께 무시하는 것이 안전하다).
    """
    spec = GATES[gate].get(reviewer, DEFAULT_SPEC)
    o = (overrides or {}).get(reviewer) or {}
    if o.get("engine") and o["engine"] not in engines:
        o = {}
    engine = o.get("engine") or (spec["prefer"] if spec["prefer"] in engines else engines[0])
    model, effort = TIERS[engine][spec["tier"]]
    return (engine, model_override or o.get("model") or model,
            effort_override or o.get("effort") or effort, spec["tier"])


def set_assignment(spec_str: str) -> None:
    """`축=engine/model/effort` 를 config 에 저장한다. 값을 비우면 기본(추천)으로 복귀."""
    axis, _, val = spec_str.partition("=")
    axis = axis.strip()
    if not axis:
        sys.exit("형식: --set-assignment '축=engine/model/effort' (값을 비우면 기본 복귀)")
    cfg = load_cfg()
    asg = cfg.setdefault("assignments", {})
    if not val.strip():
        print(f"{axis}: " + ("오버라이드 제거 — tier 기본(추천)으로 복귀"
                             if asg.pop(axis, None) else "오버라이드가 없다"))
    else:
        parts = [p.strip() for p in val.split("/")]
        if len(parts) != 3 or parts[0] not in ENGINES or not all(parts):
            sys.exit(f"형식: --set-assignment '{axis}=<{'|'.join(ENGINES)}>/<model>/<effort>'\n"
                     "  model·effort 는 그 엔진 CLI 가 아는 값이어야 한다 (틀리면 라운드에서 에러로 표면화).")
        engine, model, effort = parts
        asg[axis] = {"engine": engine, "model": model, "effort": effort}
        enabled = cfg_engines(cfg)
        warn = (f"\n⚠ {engine} 는 가용 엔진({', '.join(enabled)}) 밖이다 — "
                f"목록에 들어올 때까지 이 오버라이드는 무시된다."
                if enabled and engine not in enabled else "")
        print(f"{axis}: {engine}/{model}/{effort} 저장{warn}")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))


def show_assignments() -> None:
    """유효 배정표 + 각 축의 성격(왜 이 tier 인가) + 오버라이드 표시 — '추천과 이유'의 원천."""
    cfg = load_cfg()
    engines = cfg_engines(cfg)
    if not engines:
        sys.exit("엔진이 설정되지 않았다 — 먼저 --set-engine 을 실행한다.")
    overrides = cfg.get("assignments", {})
    print(f"가용 엔진: {', '.join(engines)}  (전환: --set-engine, 예: 한도 소진 시 --set-engine codex)")
    for gate, axes in GATES.items():
        print(f"\n[{gate} 게이트]")
        for axis, spec in axes.items():
            e, m, ef, t = assign(axis, gate, engines, None, None, overrides)
            de, dm, def_, _ = assign(axis, gate, engines, None, None, {})
            mark = f"  ✏ 오버라이드 (추천: {de}/{dm}/{def_})" if (e, m, ef) != (de, dm, def_) else ""
            print(f"  {axis:22} [{t:5}] {e}/{m}/{ef}{mark}")
            print(f"      └ {spec['why']}")
    ignored = [a for a, o in overrides.items()
               if o.get("engine") and o["engine"] not in engines]
    if ignored:
        print(f"\n⚠ 무시 중인 오버라이드(가용 밖 엔진): {', '.join(ignored)}")
    print("\n변경: --set-assignment '축=engine/model/effort' · 추천 복귀: --set-assignment '축='")


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


def lean_reviewers(gate: str, cwd: str) -> tuple[list[str], str]:
    """MoE 중간 라운드의 축 = core + 직전 라운드에서 regression 을 낸 축.

    "이번엔 필요 없어 보여서" 축을 빼는 판단은 이 스킬이 만들어진 사고 그 자체라 하지 않는다.
    대신 결정적 규칙으로 **유예**한다 — 직전 라운드가 없으면 전체 축(베이스라인)이고,
    결함을 낸 축은 그 수정을 재검증해야 하므로 자동으로 다시 켜지며, 축이 빠진 라운드의
    GO 는 coverage=partial 로 기록되어 top-up 병합 전에는 게이트 통과가 아니다.
    """
    axes = GATES[gate]
    rounds = [(rj.stat().st_mtime, str(rj), rj) for d in branch_dir(cwd).glob(f"{gate}-*")
              if re.fullmatch(rf"{gate}-\d+", d.name) and (rj := d / "round.json").exists()]
    if not rounds:
        return list(axes), "직전 라운드 없음 — 전체 축(베이스라인)"
    prev = max(rounds)  # round.json mtime 기준 — 번호 순이 아니다 (resume.py 와 같은 이유)
    hot = {f.get("reviewer") for f in json.loads(prev[2].read_text()).get("findings", [])
           if f.get("classification") == "regression"}
    sel = [a for a, spec in axes.items() if spec.get("core") or a in hot]
    return sel, f"core + 직전({prev[2].parent.name})에서 regression 을 낸 축"


DIFF_CAP = 200_000  # chars — 넘으면 프롬프트에 첨부하지 않는다 (프롬프트가 터진다)


def diff_snapshot(cwd: str, base_ref: str | None) -> str:
    """라운드 시작 시점의 diff 를 마크다운 절로 만든다. 빈 diff 면 빈 문자열.

    리뷰어 N 명이 각자 `git diff` 를 다시 뜨는 탐색 턴이 실측 턴당 ~120K 토큰이었다 —
    러너가 한 번 떠서 전원에게 같은 스냅샷을 준다. 리뷰어는 이걸로 시작하되
    findings 의 근거는 여전히 파일에서 재확인한다(스냅샷 헤더가 그렇게 지시한다).
    """
    body = git(cwd, "diff", f"{base_ref}...HEAD") if base_ref else git(cwd, "diff", "HEAD")
    if not body.strip():
        return ""
    status = git(cwd, "status", "--short")
    label = f"`git diff {base_ref}...HEAD`" if base_ref else "`git diff HEAD` (작업 트리)"
    return (f"\n\n## Diff 스냅샷 (자동 생성 — 라운드 시작 시점, {label})\n\n"
            "리뷰어마다 같은 diff 를 다시 뜨는 탐색을 없애기 위해 러너가 첨부했다. "
            "탐색은 이걸로 시작하되, findings 의 근거는 파일을 직접 열어 재확인한다.\n\n"
            f"```diff\n{body}\n```\n"
            + (f"\n### git status --short\n\n```\n{status}\n```\n" if status else ""))


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
    if gate == "plan":
        # 문서 6-구조 — B2C-52504 회고: 리뷰 32건 중 25건(78%)이 문서에서 예방 가능했고,
        # 그 빈칸이 이 여섯이다. 채우는 절차(누락 행의 AskUserQuestion clarify)는 SKILL.md 에 있다.
        body.append("## 판정 기준 — 문서 6-구조 커버리지\n"
                    "각 행을 PRD·계획서와 대조해 채운다 — `반영(어디 — 인용)` / `해당 없음(이유)` / `누락`.\n"
                    "인용 없는 `반영` 은 무효다. `누락` 은 라운드 전에 사용자에게 물어(clarify) 해소한다 (SKILL.md).\n\n"
                    "| 구조 | 판정 | 근거·인용 |\n|---|---|---|\n"
                    "| 1. 화면별 상태 매트릭스 (조회중·무효접근·조회실패·전제불일치·정상·만료) | <반영/해당없음/누락> | |\n"
                    "| 2. 개념 사전 + 소비처 목록 (핵심 판정의 SSOT 와 쓰는 화면 전수) | <반영/해당없음/누락> | |\n"
                    "| 3. 신뢰 경계 (딥링크·params 중 믿을 값 vs 서버 권위값으로 덮을 값) | <반영/해당없음/누락> | |\n"
                    "| 4. 과도기 규정 (서버 반영 전 구간에 보여줄 것과 막을 것) | <반영/해당없음/누락> | |\n"
                    "| 5. 집계·이벤트 시점 (어느 상태 전이에서, FE/BE 중 누가 세나) | <반영/해당없음/누락> | |\n"
                    "| 6. BE 계약 위반 시 FE 기대 (규격 밖 값을 막나 통과시키나) | <반영/해당없음/누락> | |")
    # 인벤토리는 초안이 만들 수 없다(호출부 grep 은 변경 대상을 알아야 한다) — 자리만 남긴다.
    body.append("## 변경 대상 인벤토리 (전수 주장)\n"
                "`<이 목록을 뽑은 명령 — 예: grep -rn \"getUsersMe\" apps/>` 기준. "
                "리뷰어가 이 명령을 다시 돌려 누락을 반증한다.\n\n"
                "| 호출부·전송지점 | 이 변경이 무엇을 바꾸나 | 의도된 동작 |\n|---|---|---|\n"
                "| <파일:줄> | <바뀌는 것> | <사용자가 무엇을 보나> |\n\n"
                "의존하는 외부 신호(SDK 콜백·이벤트·응답 필드)는 보장하지 않는 것을 함께 적는다:\n"
                "- `<신호>` = <보장하는 사건>. <보장하지 않는 사건>은 보장하지 않는다.")
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


# codex 는 subscription 이라 CLI 가 비용을 안 준다 — 환산 단가($/1M input, output).
# OpenAI 표준대로 cached input 은 input 단가의 10% 로 계산한다.
CODEX_PRICES = {"gpt-5.6-sol": (5.0, 30.0), "gpt-5.6-terra": (2.5, 15.0), "gpt-5.6-luna": (1.0, 6.0)}


def parse_output(engine: str, stdout: str, model: str | None):
    """JSON 래핑 출력에서 (리뷰 텍스트, tokens dict|None) 를 꺼낸다.

    래핑 파싱이 실패하면 stdout 을 그대로 텍스트로 돌려준다 — CLI 출력 형식이 바뀌어도
    라운드가 죽는 대신 토큰 집계만 빠진다. tokens 는 {"input","output","total","cost_usd"}.
    """
    if engine == "claude":
        # `--output-format json`: 한 개의 JSON 객체. 리뷰 본문은 .result, 사용량은 .usage.
        try:
            d = json.loads(stdout[stdout.index("{"):stdout.rindex("}") + 1])
            u = d["usage"]
            inp = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) \
                + u.get("cache_read_input_tokens", 0)
            out = u.get("output_tokens", 0)
            return d.get("result") or "", {"input": inp, "output": out,
                                           "total": inp + out, "cost_usd": d.get("total_cost_usd")}
        except (ValueError, KeyError, TypeError):
            return stdout, None
    if engine == "codex":
        # acpx `--format json`: ACP JSON-RPC 스트림. 본문은 agent_message_chunk 조각의 연결,
        # 사용량은 session/prompt 응답의 result.usage 다.
        text, usage = [], None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            upd = (d.get("params") or {}).get("update") or {}
            if upd.get("sessionUpdate") == "agent_message_chunk":
                text.append((upd.get("content") or {}).get("text") or "")
            if isinstance(d.get("result"), dict) and "usage" in d["result"]:
                usage = d["result"]["usage"]
        if not text and usage is None:
            return stdout, None
        tokens = None
        if usage:
            # inputTokens 는 캐시를 **제외한** 입력이다 — 실측 검산:
            # totalTokens(23406) = inputTokens(14441) + cachedReadTokens(8960) + output(5).
            fresh, cached = usage.get("inputTokens", 0), usage.get("cachedReadTokens", 0)
            out = usage.get("outputTokens", 0) + usage.get("thoughtTokens", 0)
            cost = None
            if model in CODEX_PRICES:
                in_rate, out_rate = CODEX_PRICES[model]
                cost = (fresh * in_rate + cached * in_rate * 0.1 + out * out_rate) / 1e6
            tokens = {"input": fresh + cached, "output": out,
                      "total": usage.get("totalTokens", fresh + cached + out),
                      "cost_usd": round(cost, 4) if cost is not None else None}
        return "".join(text), tokens
    return stdout, None


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
    cmd, env, stdin_text = engine_cmd(engine, prompt, cwd, model, effort)
    # 프롬프트를 stdin 으로 받는 엔진은 input= 으로 넘긴다 — 다 쓰면 파이프가 닫혀 EOF 가 된다.
    # 아니면 DEVNULL 로 막는다(열어 두면 EOF 를 기다리며 교착한다).
    stdio = {"input": stdin_text} if stdin_text is not None else {"stdin": subprocess.DEVNULL}
    stdout = ""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True,
                           text=True, timeout=timeout, env=env, **stdio)
        stdout, raw = p.stdout, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        raw = f"[TIMEOUT after {timeout}s]\n" + (e.stdout or "") + (e.stderr or "")
    (out / f"{reviewer}.txt").write_text(raw)
    # 리뷰어가 대상 코드를 못 읽으면 findings 가 조용히 비어 GO 로 보인다.
    # 그 라운드를 정상 결과로 채점하면 틀린 결론이 나오므로 반드시 표면화한다.
    # 리뷰어가 없는 파일 경로를 추측하는 것은 정상이므로, cwd 자체에 닿지 못한 경우만 센다.
    lost = sum(1 for line in raw.splitlines()
               if cwd in line and ("No such file or directory" in line or "not a git repository" in line))
    text, tokens = parse_output(engine, stdout, model)
    parsed = extract_json(text)
    (out / f"{reviewer}.json").write_text(json.dumps(parsed, ensure_ascii=False, indent=2)
                                          if parsed else "null")
    warn = f"  ⚠ 파일접근오류 {lost}건 — 이 리뷰어 결과는 신뢰할 수 없다" if lost else ""
    label = f"[{engine}/{model or 'default'}/{effort or 'default'}]"
    if tokens:
        label += f" {tokens['total']/1000:.1f}k tok" \
                 + (f" ${tokens['cost_usd']:.2f}" if tokens.get("cost_usd") is not None else "")
    if parsed is None:
        print(f"  {reviewer:24} PARSE-FAIL  {label}{warn}", flush=True)
    else:
        print(f"  {reviewer:24} {len(parsed['findings'])} findings  "
              f"{parsed.get('verdict', '?')}  {label}{warn}", flush=True)
    return reviewer, parsed, lost, tokens


def recompute(merged: dict) -> dict:
    """`reviewers`·`findings` 에서 verdict·counts 를 다시 계산한다 — 새 라운드와 병합이 공용으로 쓴다.

    아무도 결과를 못 낸 라운드는 GO 가 아니라 무효다. findings 0 == 결함 없음 처럼 보이지만
    엔진이 로그인 안 됐거나 세션이 끊긴 경우와 구별되지 않는다 — claude 엔진 첫 실측에서
    `Not logged in` 한 줄만 받고도 GO 가 찍혔다. 그 GO 를 믿으면 리뷰 없이 커밋한다.
    병합 경로에서 findings 만 보고 재계산하면 이 규칙이 조용히 풀리므로 여기 한 곳에 둔다.
    """
    reg = [f for f in merged["findings"] if f.get("classification") == "regression"]
    merged["verdict"] = ("INVALID" if merged["reviewers"]
                         and all(v == "PARSE-FAIL" for v in merged["reviewers"].values())
                         else "NO-GO" if reg else "GO")
    merged["counts"] = {
        "regression_P1": sum(1 for f in reg if f.get("severity") == "P1"),
        "regression_P2": sum(1 for f in reg if f.get("severity") == "P2"),
        "non_regression": len(merged["findings"]) - len(reg),
    }
    # 축이 빠진 라운드의 GO 는 게이트 통과가 아니다 — 어떤 축이 빠졌는지를 라운드 자체에
    # 남겨야 resume.py 와 사람이 top-up 전에 GO 로 읽는 사고를 막는다. 병합(top-up)이
    # 리뷰어를 채우면 여기서 자동으로 full 로 돌아온다.
    #
    # 축이 빠지는 경로는 둘이고 **원인이 다르다** — 그래서 따로 남긴다.
    #   skipped : 호출조차 안 된 축 (--lean/MoE 의 의도적 유예, --reviewers 로 좁힌 경우)
    #   unparsed: 호출됐지만 결과를 파싱하지 못한 축 (PARSE-FAIL — 실행 사고)
    # 둘 다 "그 축이 못 본 상태" 라 coverage 는 같이 partial 이지만, 치유 명령이 다르다
    # (전자는 빠진 축 top-up, 후자는 그 축만 재실행해 병합). 안내 문구도 아래에서 갈린다.
    skipped = sorted(set(GATES.get(merged.get("gate"), {})) - set(merged["reviewers"]))
    unparsed = sorted(r for r, v in merged["reviewers"].items() if v == "PARSE-FAIL")
    merged["coverage"] = "partial" if (skipped or unparsed) else "full"
    merged["skipped"] = skipped
    merged["unparsed"] = unparsed
    return merged


def merge_prepare(target: Path, reviewers: list[str]) -> dict:
    """부분 재실행을 원 라운드에 되돌릴 준비 — 이전 산출물을 보존하고 round.json 을 돌려준다.

    이 경로가 없던 동안 유일한 수단은 `--out` 으로 별 디렉토리에 돌린 뒤 round.json 을 손으로
    고치는 것이었다(실측: code-9). 그러면 무엇이 왜 바뀌었는지가 감사 기록에서 사라진다.
    그래서 덮되 지우지 않는다 — 이전 파일은 `<reviewer>.superseded-<stamp>.*` 로 남고
    교체 사실은 `reruns` 에 누적된다.
    """
    rj = target / "round.json"
    if not rj.exists():
        sys.exit(f"--merge-into: {rj} 가 없다 — 아직 돌지 않은 라운드에는 병합할 것이 없다.")
    merged = json.loads(rj.read_text())
    stamp = time.strftime("%Y%m%dT%H%M%S")
    for r in reviewers:
        for suf in (".txt", ".json", ".prompt.md"):
            if (old := target / f"{r}{suf}").exists():
                old.rename(target / f"{r}.superseded-{stamp}{suf}")
        merged.setdefault("reruns", []).append(
            {"reviewer": r, "at": stamp, "was": (merged.get("reviewers") or {}).get(r),
             "was_access_errors": (merged.get("access_errors") or {}).get(r),
             "superseded": f"{r}.superseded-{stamp}.*"})
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd")
    ap.add_argument("--context", help="컨텍스트 md 파일 경로")
    ap.add_argument("--from-zax", metavar="TASK", default=None,
                    help="zax:task 산출물에서 컨텍스트를 잡는다 (~/.zb-task/<TASK>/). "
                         "초안이 없으면 만들고 멈춘다 — 검토 후 다시 실행한다")
    ap.add_argument("--gate", choices=list(GATES), default="code")
    ap.add_argument("--out", default=None, help="생략 시 ~/.red-team/runs/... 로 자동 결정")
    ap.add_argument("--merge-into", default=None, metavar="ROUND_DIR",
                    help="부분 재실행 결과를 그 라운드에 병합한다 (PARSE-FAIL·파일접근오류 치유). "
                         "--reviewers 필수. 이전 산출물은 *.superseded-* 로 남고 reruns 에 기록된다")
    ap.add_argument("--reviewers", default=None, help="생략 시 게이트 기본값")
    ap.add_argument("--lean", action="store_true",
                    help="MoE 중간 라운드: core + 직전 라운드에서 regression 을 낸 축만 돈다. "
                         "축이 빠진 GO 는 coverage=partial — top-up 병합 전에는 게이트 통과가 아니다")
    ap.add_argument("--full", action="store_true",
                    help="config 의 moe:true 를 이번 라운드만 해제하고 전체 축을 돈다")
    ap.add_argument("--diff-base", default=None, metavar="REF",
                    help="코드 게이트 diff 스냅샷의 기준 (git diff REF...HEAD 로 뜬다). "
                         "생략 시 작업 트리(git diff HEAD)")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--model", default=None, help="전 리뷰어 모델 강제 (생략 시 축별 tier 배정)")
    ap.add_argument("--effort", default=None, help="전 리뷰어 effort 강제 (생략 시 축별 tier 배정)")
    ap.add_argument("--engine", default=None,
                    help="이번 라운드만 가용 엔진을 바꾼다 — 단일(codex)이면 전 리뷰어 통일, "
                         "콤마(codex,claude)면 축별 분산")
    ap.add_argument("--set-engine", default=None, metavar="ENGINES",
                    help="가용 엔진을 저장하고 종료 (예: codex 또는 codex,claude — 첫 항목이 기본)")
    ap.add_argument("--set-assignment", action="append", default=None, metavar="AXIS=E/M/EF",
                    help="축별 배정을 config 에 저장하고 종료 (값을 비우면 추천 복귀). 반복 가능")
    ap.add_argument("--show-assignments", action="store_true",
                    help="유효 배정표 + 축 성격(추천 이유) 출력하고 종료")
    a = ap.parse_args()

    if a.set_engine:
        set_engine(a.set_engine)
        return
    if a.set_assignment:
        for s in a.set_assignment:
            set_assignment(s)
        return
    if a.show_assignments:
        show_assignments()
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
    if a.merge_into:
        tgt = Path(a.merge_into)
        if a.out:
            ap.error("--merge-into 와 --out 은 함께 쓸 수 없다 — 병합 대상이 곧 출력 위치다")
        if not a.reviewers:
            ap.error("--merge-into 는 --reviewers 가 필요하다 (전원 재실행이면 새 라운드를 돈다)")
        if not (tgt / "round.json").exists():
            ap.error(f"--merge-into: {tgt/'round.json'} 가 없다")
        # 라운드의 입력은 그 라운드의 context.md 다. 다른 컨텍스트로 돈 결과를 섞으면
        # round.json 과 context.md 가 어긋나 라운드가 자체 재현성을 잃는다.
        if a.context and Path(a.context).resolve() != (tgt / "context.md").resolve():
            ap.error("--merge-into 는 그 라운드의 context.md 로만 돈다 — --context 를 생략한다")
        a.context = str(tgt / "context.md")
        a.cwd = a.cwd or json.loads((tgt / "round.json").read_text()).get("repo_cwd")
    if not (a.cwd and a.context):
        ap.error("--cwd 와 (--context 또는 --from-zax) 가 필요하다")
    engines = resolve_engines(a.engine)

    out = Path(a.merge_into) if a.merge_into else (Path(a.out) if a.out else resolve_out(a.cwd, a.gate))
    out.mkdir(parents=True, exist_ok=True)
    context = Path(a.context).read_text()
    if not a.merge_into:
        # 컨텍스트를 라운드 디렉토리에 복사한다 — 라운드가 자체로 재현 가능해야 한다
        (out / "context.md").write_text(context)
    cfg = load_cfg()
    if a.lean and a.full:
        ap.error("--lean 과 --full 은 함께 쓸 수 없다")
    if a.lean and (a.reviewers is not None or a.merge_into):
        ap.error("--lean 은 --reviewers/--merge-into 와 함께 쓸 수 없다 — 축은 lean 이 계산한다")
    # `--reviewers ""` 는 "리뷰어 없이 준비 확인만" 이다 — 기본값 폴백(`or`)으로 처리하면
    # 빈 지정이 조용히 게이트 전체가 되어, 준비 확인용 실행이 실제 엔진 호출로 번진다(실측: 테스트가 opus 를 돌렸다).
    if a.reviewers is None and not a.merge_into and (a.lean or (cfg.get("moe") and not a.full)):
        reviewers, lean_why = lean_reviewers(a.gate, a.cwd)
        print(f"lean: {lean_why} → {','.join(reviewers)}")
    else:
        spec = a.reviewers if a.reviewers is not None else ",".join(GATES[a.gate])
        reviewers = [r.strip() for r in spec.split(",") if r.strip()]
    overrides = cfg.get("assignments", {})
    assignments = {r: assign(r, a.gate, engines, a.model, a.effort, overrides) for r in reviewers}
    if reviewers:
        print(f"round: gate={a.gate}, engines={'+'.join(engines)}, {len(reviewers)} reviewers, cwd={a.cwd}\n"
              f"  {'merge-into' if a.merge_into else 'out'}: {out}", flush=True)

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

    # diff 스냅샷 — 리뷰어 N 명이 각자 git diff 를 다시 뜨는 탐색 턴(실측 턴당 ~120K 토큰)을
    # 러너가 한 번 뜨는 것으로 대체한다. context.md 에는 넣지 않는다 — 그건 라운드 간
    # 이관되는 사람 문서라 diff 가 낡은 채 승계된다. 스냅샷은 diff.md 로 라운드에 보존된다.
    if a.diff_base and a.gate != "code":
        ap.error("--diff-base 는 코드 게이트에서만 쓴다 — 계획 게이트의 리뷰 대상은 계획 문서다")
    prompt_context = context
    if a.gate == "code":
        if a.merge_into:
            # top-up·재실행 리뷰어도 원 라운드와 같은 스냅샷을 봐야 한다 — 다시 뜨지 않는다
            if (dmd := out / "diff.md").exists():
                prompt_context += dmd.read_text()
        else:
            snap = diff_snapshot(a.cwd, a.diff_base)
            if not snap:
                print("ℹ diff 가 비어 있어 스냅샷을 첨부하지 않는다 — 브랜치 diff 를 리뷰한다면 "
                      "--diff-base <base> 를 준다.", flush=True)
            elif len(snap) > DIFF_CAP:
                print(f"ℹ diff 스냅샷이 {len(snap)//1000}K자로 상한({DIFF_CAP//1000}K)을 넘는다 — "
                      f"첨부를 생략한다(리뷰어가 직접 뜬다).", flush=True)
            else:
                (out / "diff.md").write_text(snap)
                prompt_context += snap

    # 병합 준비는 리뷰어를 돌리기 **전에** 한다 — 뒤로 밀면 방금 쓴 새 산출물을
    # superseded 로 밀어내고 새 결과 파일이 사라진다(테스트로 잡힌 실패다).
    prepared = merge_prepare(out, reviewers) if a.merge_into else None

    with ThreadPoolExecutor(max_workers=len(reviewers)) as ex:
        results = list(ex.map(
            lambda r: run(r, a.cwd, out, prompt_context, a.timeout, assignments[r]), reviewers))

    # repo_cwd 는 '이 라운드가 어디를 리뷰했나'는 라운드별 불변 기록이다.
    # 전역 가변 포인터와 다르다 — 덮어쓰이지 않고, 티켓 이름으로 진입할 때 워크트리를 찾는 근거가 된다.
    # reviewers 값은 verdict 문자열 규격을 유지한다(summarize_round.py 가 그 형태를 읽는다) —
    # 배정 상세는 assignments 에 따로 남긴다.
    merged = prepared if prepared is not None else {
        "repo_cwd": str(Path(a.cwd).resolve()), "gate": a.gate,
        "engine": "+".join(engines),
        "assignments": {}, "reviewers": {}, "findings": [], "access_errors": {}}
    for r, parsed, lost, tokens in results:
        e, m, ef, t = assignments[r]
        merged["reviewers"][r] = parsed.get("verdict") if parsed else "PARSE-FAIL"
        merged["assignments"][r] = {"engine": e, "model": m, "effort": ef, "tier": t,
                                   "tokens": tokens}
        # 재실행한 리뷰어의 이전 findings 는 걷어낸다 — 남기면 처리가 끝난 지적이 되살아난다.
        # 새 라운드에서는 no-op 다.
        merged["findings"] = [f for f in merged["findings"] if f.get("reviewer") != r]
        if lost:
            merged["access_errors"][r] = lost
        else:
            merged["access_errors"].pop(r, None)  # 병합으로 치유되면 사라져야 한다
        for f in (parsed or {}).get("findings", []):
            f.setdefault("axis", r)
            f["reviewer"] = r
            merged["findings"].append(f)
    recompute(merged)
    (out / "round.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2))

    print(f"\n{merged['verdict']}  {merged['counts']}")
    toks = [a["tokens"] for a in merged["assignments"].values() if a.get("tokens")]
    if toks:
        total = sum(t["total"] for t in toks)
        costs = [t["cost_usd"] for t in toks if t.get("cost_usd") is not None]
        print(f"tokens: {total/1000:.1f}k 합계"
              + (f", ${sum(costs):.2f} (API 환산가, {len(costs)}/{len(merged['assignments'])} 리뷰어 집계)"
                 if costs else ""))
    if merged["verdict"] == "INVALID":
        print(f"⚠ 리뷰어 전원이 결과를 내지 못했다 — 이 라운드는 판정이 아니다.\n"
              f"  engines={'+'.join(engines)} 이 실제로 돌았는지 확인한다"
              f"({out}/*.txt 첫 줄이 흔히 이유를 말해준다).")
    else:
        # 혼합 라운드에서 한 엔진만 통째로 죽으면(예: claude 로그인 풀림) 나머지 엔진의
        # GO 에 묻혀 조용히 통과한다 — 7/30 `Not logged in` 사고의 재발 경로라 표면화한다.
        heal = (f"    python3 {Path(__file__)} --cwd {a.cwd} --gate {a.gate} \\\n"
                f"      --merge-into {out} --reviewers ")
        by_engine = {}
        for r, parsed, _lost, _tokens in results:
            by_engine.setdefault(assignments[r][0], []).append(parsed is None)
        dead_engines = [e for e, fails in by_engine.items() if all(fails)]
        for e in dead_engines:
            print(f"⚠ engine={e} 리뷰어 전원({len(by_engine[e])}명)이 결과를 내지 못했다 — "
                  f"그 축들이 빠진 {merged['verdict']} 는 반쪽짜리다.\n"
                  f"  {e} 상태를 확인한 뒤 그 축만 다시 돌려 이 라운드에 병합한다:\n"
                  + heal + ",".join(r for r in reviewers if assignments[r][0] == e))
        # 엔진이 통째로 죽은 게 아니라 일부만 PARSE-FAIL 이면 라운드를 버리지 않는다 —
        # SKILL.md 가 "그 리뷰어만 1회 재실행" 을 지시하는데, 되돌릴 수단이 병합이다.
        fail = [r for r, v in merged["reviewers"].items() if v == "PARSE-FAIL"]
        if fail and not dead_engines:
            print(f"⚠ PARSE-FAIL: {','.join(fail)} — 그 축이 빠진 {merged['verdict']} 는 반쪽짜리다.\n"
                  f"  그 리뷰어만 1회 재실행해 이 라운드에 병합한다:\n" + heal + ",".join(fail))
    if merged["access_errors"]:
        # 이 리뷰어 결과만 신뢰할 수 없다 — 라운드 전체를 버릴 필요는 없고, 손으로 round.json 을
        # 고칠 필요도 없다. 병합 경로가 verdict·counts·access_errors 를 다시 계산한다.
        print(f"⚠ 파일접근오류: {merged['access_errors']} — 그 리뷰어 결과는 신뢰할 수 없다.\n"
              f"  리뷰 대상 디렉토리가 실행 중 사라지지 않는 위치인지 확인한 뒤,\n"
              f"  그 리뷰어만 다시 돌려 이 라운드에 병합한다:\n"
              f"    python3 {Path(__file__)} --cwd {a.cwd} --gate {a.gate} \\\n"
              f"      --merge-into {out} --reviewers {','.join(merged['access_errors'])}\n"
              f"  (round.json 을 손으로 고치지 않는다 — 병합이 verdict·counts·access_errors 를 다시 계산한다)")
    if merged["verdict"] == "GO" and merged.get("coverage") == "partial":
        # 축을 빼는 것은 "생략"이 아니라 "유예"다 — 빠진 축이 못 본 결함은 GO 로 결론나면 안 된다.
        # 이 top-up 병합이 채워진 뒤의 verdict 만 게이트 판정이다 (resume.py 도 같은 규칙을 본다).
        if merged["skipped"]:
            print(f"⚠ 축 {','.join(merged['skipped'])} 가 빠진 GO 다 (coverage=partial) — 게이트 통과가 아니다.\n"
                  f"  빠진 축을 이 라운드에 병합해 커버리지를 채운 뒤의 verdict 가 판정이다:\n"
                  f"    python3 {Path(__file__)} --cwd {a.cwd} --gate {a.gate} \\\n"
                  f"      --merge-into {out} --reviewers {','.join(merged['skipped'])}")
        if merged["unparsed"]:
            # 재실행 명령은 위 PARSE-FAIL 경고가 이미 안내했다 — 여기서는 그 GO 의 성격을 못 박는다.
            # 경고문을 읽었는지에 의존하지 않으려고 coverage 로 남기는 것이 이 분기의 목적이다.
            print(f"⚠ 축 {','.join(merged['unparsed'])} 가 결과를 내지 못한 GO 다 (coverage=partial) — "
                  f"게이트 통과가 아니다.\n"
                  f"  위 안내대로 그 축만 재실행해 이 라운드에 병합한 뒤의 verdict 가 판정이다.")


if __name__ == "__main__":
    main()
