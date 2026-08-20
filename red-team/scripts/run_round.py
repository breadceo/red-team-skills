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

산출물은 기본적으로 저장소 밖 `~/.red-team/runs2/<owner>__<repo>/<branch키>/<gate>-<n>/` 에 쌓인다 —
리뷰 대상 저장소를 오염시키지 않고, 라운드 간 컨텍스트가 보존되어 다음 라운드가 이어진다.
`--out` 을 주면 그 경로를 그대로 쓴다(eval 용).

리뷰어 id 는 prompts/<id>.md 의 파일명이다. 기본값은 게이트에 따라 정해진다.
프롬프트를 stdin 으로 받는 엔진(codex)은 프롬프트를 흘려보낸 뒤 stdin 을 닫아 EOF 를 준다.
그렇지 않은 엔진은 stdin 을 DEVNULL 로 고정한다 — 열어 두면 EOF 를 기다리며 교착한다.
"""
import argparse, errno, fcntl, hashlib, json, os, re, shlex, shutil, stat, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PROMPTS = SKILL / "prompts"
HOME_DIR = Path(os.environ.get("RED_TEAM_HOME", Path.home() / ".red-team"))


def shell_command(*args) -> str:
    return shlex.join(str(arg) for arg in args)


def lock_round(out: Path):
    path = out / "round.json"
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock = os.fdopen(os.open(path, flags), "r+b")
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise OSError("regular file이 아니다")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if "lock" in locals():
            lock.close()
        if not isinstance(e, BlockingIOError):
            sys.exit(f"라운드 lock을 안전하게 열 수 없다: {path}: {e}")
        sys.exit(f"라운드가 이미 아카이브 중이거나 round.json을 안전하게 잠글 수 없다: {path}")
    return lock


def lock_migrations(home: Path = HOME_DIR, *, exclusive: bool):
    if not home.is_dir() or home.is_symlink():
        return None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(home, flags)
    fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    return fd


def auto_archive(archive_fn=None):
    if os.environ.get("RED_TEAM_DISABLE_AUTO_ARCHIVE") == "1":
        return
    try:
        if archive_fn is None:
            from archive_runs import archive as archive_fn
        result = archive_fn(HOME_DIR, older_than=30, apply=True, include_legacy=False)
    except Exception as e:
        print(f"⚠ 자동 아카이브 실패(리뷰 판정은 유지): {e}", flush=True)
        return
    if any(result.values()):
        print(f"auto-archive: files={result['files']} "
              f"saved={result['original'] - result['compressed']} "
              f"busy_rounds={result['busy']} conflicts={result['conflicts']}")
# v2 라운드 루트. 구 루트(runs/)와 **경로 공간을 통째로 분리**한 이유(issue #8 code-7):
# 구 레이아웃의 키는 slug 산출물 전체라, 새 키를 아무리 단사로 설계해도 이미 디스크에 있는
# 구 디렉토리가 새 키 자리를 선점하는 전환기 충돌(스쿼팅)을 막을 수 없었다. 루트가 다르면
# runs2/ 아래는 v2 코드가 만든 것뿐이라 그 클래스가 통째로 사라진다.
RUNS_DIR = HOME_DIR / "runs2"
LEGACY_RUNS = HOME_DIR / "runs"
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
                 f"  {shell_command('python3', Path(__file__).resolve(), '--set-engine', 'codex')}          # 한 엔진만\n"
                 f"  {shell_command('python3', Path(__file__).resolve(), '--set-engine', 'codex,claude')}   # 축별 분산 (첫 항목이 기본)\n"
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


# 해시 접미 자리 — 이 패턴은 무접미 키로 쓰지 않는다. re.I: APFS 같은 대소문자 비구분
# 파일시스템에서 `--87171AD4` 브랜치가 소문자 접미 키와 같은 inode 를 얻는다(code-4 P2).
_RESERVED_SUF = re.compile(r"--[0-9a-f]{8}$", re.I)


def _suffixed(rendered: str, raw: str) -> str:
    # rendered 는 slug 산출물(ASCII)이라 문자수 == 바이트수다. NAME_MAX(255) 안에 접미
    # 10자 자리를 남겨 245자로 자른다 — 해시는 절단 전 원문 전체로 계산하므로 단사성은
    # 유지된다(code-2 P1: 246자 브랜치가 변경 전엔 되다가 접미 10자로 mkdir 이 죽었다).
    return f"{rendered[:245]}--{hashlib.sha1(raw.encode()).hexdigest()[:8]}"


def branch_key(branch: str) -> str:
    """브랜치 → 디렉토리명. 필요한 성질은 가역이 아니라 **단사**다 — 디렉토리명을
    브랜치로 되돌리는 소비처는 없고, resume.py 의 워크트리 매칭도 브랜치→키 방향으로
    이 함수를 적용해 비교한다. slug 가 뭉갠 브랜치(`feature/foo`)에만 원문 해시를
    접미해 `feature-foo` 브랜치와의 충돌을 없앤다. 무손실 브랜치는 키가 그대로라
    마이그레이션 대상도 아니다.

    접미 패턴 `--<hex8>` 은 **예약**이다 — 그 패턴으로 끝나는 무손실 브랜치도 강제로
    해시를 받아, lossy 키가 문자 그대로의 브랜치명과 겹칠 수 없다(code-1 P1).
    잔여 충돌은 sha1 32bit 접두 충돌뿐이다.
    """
    s = slug(branch)
    if s == branch and not _RESERVED_SUF.search(s) and len(s) <= 255:
        return s
    return _suffixed(s, branch)


def _single_key(name: str) -> str:
    """owner 가 없는 repo 키(로컬 경로 origin·origin 부재 폴백).

    pair 키(`owner__repo`)와 출력 공간이 겹치면 안 된다 — basename 이 `team__app` 인
    저장소가 `team/app` 의 pair 키와 같은 디렉토리를 얻는다(code-3 P1). 그래서
    `__` 를 담은 이름은 branch_key 규칙에 더해 해시로 가른다.
    """
    s = slug(name)
    if s == name and "__" not in s and not _RESERVED_SUF.search(s) and len(s) <= 255:
        return s
    return _suffixed(s, name)


def repo_key(cwd: str) -> str:
    """origin 의 `owner/repo` → `owner__repo`. basename 만 쓰면 `team-a/app` 과
    `team-b/app` 이 같은 `runs/app/` 을 공유한다(issue #8).

    URL(https·ssh·scp형)에서 host 뒤 마지막 두 path segment 를 owner/repo 로 본다.
    owner 가 안 나오는 origin(로컬 경로, host 직결 단일 segment)은 basename,
    origin 이 없으면 toplevel 디렉토리명 — 구 키와 같다.

    구분자 `__` 는 owner·repo 안에도 올 수 있어 경계가 모호해진다(`a__b/c` 와
    `a/b__c` — code-1 P1). 렌더링을 다시 split 해 원문 쌍이 유일 복원될 때만
    무접미로 쓰고, 아니면(slug 손실 포함) 원문 `owner/repo` 해시를 접미한다.
    """
    origin = git(cwd, "remote", "get-url", "origin")
    if not origin:
        return _single_key(Path(git(cwd, "rev-parse", "--show-toplevel") or cwd).name)
    if origin.startswith("file://") or re.match(r"^[A-Za-z]:[\\/]", origin) \
            or (not re.match(r"^\w+://", origin) and not re.match(r"^[^/]+:", origin)):
        # 로컬 경로 origin(절대·상대·file://·Windows 드라이브) — URL 로 오인하면
        # `../x/team/app.git` 은 team__app, `/abs/x/team/app.git` 은 app 이 되어 같은
        # 저장소가 표기별로 갈린다(code-2 P2). file://(code-8 P2)·`C:/`(code-10 P2, SCP
        # 오인)도 같은 로컬 디스크다. 원격 scheme·SCP형만 URL 이고 나머지는 basename 폴백.
        tail = re.sub(r"\.git[\\/]?$", "", origin.rstrip("/\\"))
        return _single_key(re.split(r"[\\/]", tail)[-1])
    url = origin
    if re.match(r"^\w+://", url):
        # scheme 형의 명시 포트는 키가 아니다 — `ssh://host:22/app.git` 의 22 가 경로
        # 세그먼트로 새면 `22__app` 이 된다(code-9 P2). SCP 형은 건드리지 않는다 —
        # 숫자만으로 된 owner(`host:123/repo`)가 포트로 오인되면 안 된다.
        url = re.sub(r"^(\w+://[^/]+?):\d+(/)", r"\1\2", url)
    # host 는 bracketed IPv6 도 된다 — `[2001:db8::1]` 내부 콜론을 경로 구분자로 읽으면
    # host 직결 저장소에 가짜 owner 가 생긴다(code-10 P2).
    m = re.match(r"^(?:\w+://)?(?:[^/@]+@)?(?:\[[^\]]+\]|[^/:]+)[:/](.+)$", url)
    if m:
        parts = re.sub(r"\.git/?$", "", m.group(1)).strip("/").split("/")
        if len(parts) >= 2:
            owner, repo = parts[-2], parts[-1]
            rendered = f"{slug(owner)}__{slug(repo)}"
            if rendered.split("__") == [owner, repo] and not _RESERVED_SUF.search(rendered) \
                    and len(rendered) <= 255:  # 무접미 초과분은 절단+해시 경로로(code-3 P2)
                return rendered
            return _suffixed(rendered, f"{owner}/{repo}")
    return _single_key(re.sub(r"\.git/?$", "", origin.rstrip("/")).rsplit("/", 1)[-1])


def _legacy_branch_dir(cwd: str, branch: str) -> Path:
    """issue #8 이전(gen0)의 키 규칙 — 마이그레이션·무변경 해석 판정에만 쓴다."""
    origin = git(cwd, "remote", "get-url", "origin")
    repo = slug(re.sub(r"\.git$", "", origin.rsplit("/", 1)[-1])) if origin else \
        slug(Path(git(cwd, "rev-parse", "--show-toplevel") or cwd).name)
    return LEGACY_RUNS / repo / slug(branch)


def _legacy_candidates(cwd: str, branch: str):
    """구 세대 경로들 — 최신 세대 우선.

    구 루트(runs/) 세대만 낸다 — v2 안의 이전 키(폴백→pair 승격·origin 변경)는 전부
    `_v2_predecessor` 의 **양성 증거 규칙**으로만 승계한다. v2 디렉토리에 구 세대의
    "판정 불가면 이전" 규칙을 적용하면 디렉토리명이 같은 다른 저장소의 준비 라운드를
    가져간다(code-12 P1). gen1 은 이 변경의 미출시 중간 레이아웃(구 루트 아래
    owner__repo 키 — issue #8 개발 라운드에서만 생성됨), gen0 은 출시본(basename/slug)이다.
    """
    yield LEGACY_RUNS / repo_key(cwd) / branch_key(branch)  # gen1
    yield _legacy_branch_dir(cwd, branch)                   # gen0


def _git_toplevel_state(p: str):
    """git 소유 확인의 3상태 — ('ok', toplevel) | ('not_repo', None) | ('error', None).

    확인 **실패**(권한·I/O·타임아웃)를 '비워크트리(판정 불가)'와 같은 빈 값으로 뭉개면
    외부 저장소 기록을 이전할 수 있다(code-10 P1) — 실패는 이전 거부 사유다.
    """
    try:
        r = subprocess.run(["git", "-C", p, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return "error", None
    if r.returncode == 0:
        return "ok", r.stdout.strip()
    if "not a git repository" in (r.stderr or "").lower():
        return "not_repo", None
    return "error", None


def _record_owners(d: Path):
    """d 아래 라운드 기록들의 (확인 상태, repo_cwd) — 'ok'(워크트리 확인) 또는 'error'.

    판정 불가는 내지 않는다 — 손상(잘린 UTF-8)·비 dict 유효 JSON(null·[])·비문자열
    repo_cwd(code-2 P2), 경로 소실·초장문 OSError(code-4 P2), 비워크트리(code-3 P2).
    """
    try:
        names = sorted(os.listdir(d))
    except OSError:
        # 디렉토리 열거 실패(권한·마운트)는 '기록 없음'이 아니다(code-15 P2) — 빈 소유자
        # 목록으로 뭉개면 남의 구 라운드를 판정 불가로 오인해 이전한다.
        yield "error", str(d)
        return
    sources = []
    for n in names:
        rj = d / n / "round.json"
        try:
            if rj.is_file():
                sources.append(rj)
        except OSError:
            yield "error", str(rj)
    if "owner.json" in names:
        sources.append(d / "owner.json")  # 커서 전용 디렉토리도 증거를 가진다(code-14 P2)
    for rj in sources:
        st, p = _read_repo_cwd(rj)
        if st == "none":
            continue
        if st == "error":  # 존재·읽기 확인 실패 — 판정 불가가 아니라 거부 사유(code-13·14)
            yield "error", p
            continue
        state, _top = _git_toplevel_state(p)
        if state != "not_repo":
            yield state, p


def _pick_legacy(cwd: str, branch: str, parent_name: str):
    """(소유 가능한 첫 구 후보, 못 쓴 첫 외부 워크트리) — 부수효과 없는 선택.

    구 디렉토리가 **다른 저장소의 기록**일 수 있다(그 충돌이 issue #8 이다) — 후보의
    기록 전부를 대조해 하나라도 불일치면 그 후보는 건너뛰고 **다음 세대를 계속 본다**.
    외부 후보에서 멈추면 뒤 세대에 남은 자기 기록을 두고 새 라운드를 시작한다(code-8 P2).
    첫 일치에서 멈추지 않는 이유는 code-1 P2(혼합 기록), 판정 불가(기록 없음·워크트리
    소실·손상)면 이전 — 단일 저장소 사용이 압도적이다.
    """
    first_blocked = None
    for legacy in _legacy_candidates(cwd, branch):
        if not legacy.is_dir():
            continue
        blocked = next((("unverified" if state == "error" else "foreign", p)
                        for state, p in _record_owners(legacy)
                        if state == "error" or repo_key(p) != parent_name), None)
        if blocked:
            first_blocked = first_blocked or blocked
            continue
        return legacy, None
    return None, first_blocked


def _read_repo_cwd(f: Path):
    """기록 파일(round.json·owner.json)의 repo_cwd — ('ok', p) | ('none', None) | ('error', p).

    'none' 은 판정 불가 — 손상(잘린 UTF-8)·비 dict 유효 JSON(null·[])·비문자열·경로
    소실·초장문 ENAMETOOLONG(code-2·4 P2). 'error' 는 존재 확인 자체가 실패한 것
    (권한·마운트 등) — 소유자 없음으로 오인해 이전하면 오귀속이다(code-13 P2).
    """
    try:
        data = json.loads(f.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "none", None
    except OSError as e:
        # 기록 파일 자체를 읽지 못한 것(권한·마운트)은 '증거 없음'이 아니다(code-14 P1) —
        # glob 직후 삭제된 ENOENT 경합만 판정 불가로 남긴다.
        return ("none", None) if e.errno == errno.ENOENT else ("error", str(f))
    p = data.get("repo_cwd") if isinstance(data, dict) else None
    if not (isinstance(p, str) and p):
        return "none", None
    try:
        return ("ok", p) if Path(p).is_dir() else ("none", None)
    except OSError as e:
        return ("none", None) if e.errno == errno.ENAMETOOLONG else ("error", p)


def note_owner(bdir: Path, cwd: str) -> None:
    """브랜치 디렉토리에 소유 증거(owner.json)를 남긴다 — 쓰기 지점에서 호출한다.

    경로 파생의 두 번째 진실이 **아니다**(파생은 여전히 워크트리에서만 나온다) —
    round.json 이 없는 디렉토리(준비만 된 라운드, pr-triage 커서 전용)도 origin 변경
    승계(_v2_predecessor)의 양성 증거를 가질 수 있게 하는 판정 재료다(code-12 P1).
    실패해도 라운드·상태 저장을 막지 않는다.
    """
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "owner.json").write_text(
            json.dumps({"repo_cwd": str(Path(cwd).resolve())}, ensure_ascii=False))
    except OSError:
        pass


def _v2_predecessor(cwd: str, new: Path):
    """origin 변경으로 키가 바뀐 같은 저장소의 이전 v2 디렉토리 — **양성 소유 증거** 필수.

    구 세대(gen0·gen1)와 달리 판정 불가를 이전 근거로 삼지 않는다 — v2 아래에는 여러
    저장소가 같은 브랜치명(main 등)으로 공존하는 것이 정상이라, 증거 없는 이전은 남의
    기록을 가져간다(code-11·12 P1). **모든 기록**(round.json 전부 + owner.json)이 판정
    가능하고 전부 현재 키로 파생 확인될 때만 승계한다 — 판정 불가가 하나라도 섞이면
    미검증 기록까지 새 이력으로 승계된다(code-12 P2).
    """
    blocked = None
    for d in sorted(RUNS_DIR.glob(f"*/{new.name}")):
        if d == new or not d.is_dir():
            continue
        try:
            names = sorted(os.listdir(d))
        except OSError:
            # 열거 실패한 디렉토리는 '전신 없음'이 아니다(code-15 P2) — 새 이력이 선점하면
            # 접근 복구 후에도 승계가 막힌다.
            blocked = blocked or ("unverified", str(d))
            continue
        sources = []
        errors = 0
        for n in names:
            rj = d / n / "round.json"
            try:
                if rj.is_file():
                    sources.append(rj)
            except OSError:
                errors += 1
        if "owner.json" in names:
            sources.append(d / "owner.json")
        if not sources and not errors:
            continue
        # 전 소스를 끝까지 훑어 집계한다 — 순서에 따라 판정이 갈리면 안 된다(code-15 P1).
        pos = mismatch = undecidable = 0
        for f in sources:
            st, p = _read_repo_cwd(f)
            if st == "error":
                errors += 1
            elif st == "none":
                undecidable += 1
            else:
                g = _git_toplevel_state(p)[0]
                if g == "error":
                    errors += 1
                elif g == "ok" and repo_key(p) == new.parent.name:
                    pos += 1
                else:
                    mismatch += 1
        if mismatch:
            continue  # 다른 저장소의 v2 디렉토리 — 정상 공존, 보류 사유 아님
        if errors or (undecidable and pos):
            # 확인 실패, 또는 양성 증거가 있는데 손상 기록이 섞임 — '없음'으로 처리해
            # 새 키를 선점하면 복구 가능한 이력이 영구 고아가 된다(code-15 P1·P2).
            blocked = blocked or ("unverified", str(d))
            continue
        if pos:
            return d, None
    return None, blocked


def _migration_state(cwd: str, branch: str, new: Path):
    """(이전할 구 디렉토리, 거부 사유 (상태, 워크트리)) — 부수효과 없는 판정.

    거부 상태는 'foreign'(origin 불일치)과 'unverified'(소유 확인 실패)를 가른다 —
    합치면 확인 실패를 다른 저장소 기록이라고 단정하는 안내가 나간다(code-11 P1).
    _migrate_legacy 와 dry-run 표시, 무변경 해석(branch_dir)이 같은 선택을 공유한다 —
    갈라지면 읽는 곳과 옮기는 곳이 서로 다른 디렉토리를 가리킨다(code-7·8).
    """
    if not new.exists():
        # 양성 검증된 v2 전신이 구 세대 선택보다 먼저다 — 외부 legacy 하나가 정당한
        # 승계를 차단하면 owner 변경 후 기존 라운드가 고아가 된다(code-12 P1).
        pred, pred_blocked = _v2_predecessor(cwd, new)
        if pred:
            return pred, None
        src, blocked = _pick_legacy(cwd, branch, new.parent.name)
        if src:
            return src, None
        # 전신 후보의 확인 실패도 보류로 전파한다 — 새 이력 선점을 막아야 복구 후
        # 승계가 산다(code-14 P1, resolve_out 의 unverified 차단과 한 벌).
        # unverified 가 foreign 보다 먼저다 — foreign 은 새 이력 진행을 허용하는 상태라
        # 그것이 unverified 를 가리면 보류가 무력화된다(code-16 P2).
        for b in (pred_blocked, blocked):
            if b and b[0] == "unverified":
                return None, b
        return None, blocked or pred_blocked
    return None, None


def _migrate_legacy(cwd: str, branch: str, new: Path) -> None:
    """구 레이아웃 라운드 기록을 v2 루트로 1회 rename — 새 경로가 아직 없을 때만.

    옮기지 않으면 라운드 번호·컨텍스트 이관·latest_round 가 조용히 리셋된다.
    v2 루트는 구 루트와 경로 공간이 분리돼 있어 new 가 있으면 항상 v2 코드가 만든
    정당한 기록이다 — 스쿼팅 검사(code-3·4·7 의 경고들)가 필요 없어졌다.
    """
    migration_fd = lock_migrations(HOME_DIR, exclusive=True)
    try:
        src, blocked = _migration_state(cwd, branch, new)
        if blocked:
            state, p = blocked
            why = (f"기록의 워크트리 {p} 의 origin 이 현재와 다르다 — 필요하면 수동 이전"
                   if state == "foreign" else
                   f"기록의 워크트리 {p} 의 소유 확인에 실패했다(권한·I/O 등) — "
                   f"불일치로 단정하지 않는다. 확인 가능해지면 다시 시도된다")
            print(f"⚠ 구 라운드 디렉토리를 두고 간다\n  ({why})", flush=True)
            return
        if src is None:
            return
        new.parent.mkdir(parents=True, exist_ok=True)
        try:
            src.rename(new)
        except OSError:
            # 동시 branch_dir() 경합 — 같은 목적지끼리는 new 가 생겨 있고, 서로 다른 저장소가
            # 판정 불가 legacy 를 각자의 키로 다투면 src 만 사라진다(code-5 P2). 어느 쪽이든
            # "다른 소비처가 먼저 이전했다"이므로 정상 반환한다.
            if new.exists() or not src.is_dir():
                return
            raise
        print(f"runs2/ 키 이전(issue #8): {src} → {new}", flush=True)
    finally:
        if migration_fd is not None:
            os.close(migration_fd)


def migration_blocked(cwd: str):
    """이전이 거부된 상태면 ('foreign'|'unverified', 워크트리 경로) — 아니면 None.

    resume 가 '라운드가 없다'와 '거부된 구 기록이 있다'를 갈라 안내하는 데 쓴다
    (code-10 P1). 상태를 보존해 확인 실패를 불일치로 단정하지 않는다(code-11 P1).
    부수효과 없음.
    """
    branch = _current_branch(cwd)
    new = RUNS_DIR / repo_key(cwd) / branch_key(branch)
    return _migration_state(cwd, branch, new)[1]


def migration_source(cwd: str):
    """실제 실행(migrate=True)이 이전하게 될 구 디렉토리 — 없으면 None. 부수효과 없음.

    dry-run 표시 전용: 존재 여부(bool)만 돌려주면 "이전은 일어나는데 **선택된 base 가
    아닌 다른 세대**가 옮겨지는" 상태를 못 가른다(code-9 P2) — 호출자가 자기 base 와
    동일한지 비교해야 표시 == 실제가 된다. 거부 상태(다른 저장소 기록 혼재)·새 경로
    존재 시 None(code-7 P2).
    """
    branch = _current_branch(cwd)
    new = RUNS_DIR / repo_key(cwd) / branch_key(branch)
    return _migration_state(cwd, branch, new)[0]


MIGRATE = True  # resume --dry-run 이 끈다 — dry-run 은 무변경으로 구 경로를 그대로 읽는다(code-3 P1)


def _current_branch(cwd: str) -> str:
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if branch in ("", "HEAD"):  # detached
        branch = git(cwd, "rev-parse", "--short", "HEAD") or "detached"
    return branch


def target_dir(cwd: str) -> Path:
    """이전이 끝난 뒤 실제로 쓰일 새 키 경로 — 순수 계산, 부수효과 없음.

    dry-run 이 '읽기는 legacy, 표시는 target' 을 갈라야 할 때 쓴다(code-4 P1) —
    무변경 해석(branch_dir migrate=False)은 legacy 를 돌려주는데, 실제 실행은 이전 후
    이 경로 아래에 만들기 때문이다.
    """
    return RUNS_DIR / repo_key(cwd) / branch_key(_current_branch(cwd))


def branch_dir(cwd: str, migrate: bool = False) -> Path:
    """~/.red-team/runs2/<owner>__<repo>/<branch키> — 저장소 위치에서 결정된다.

    **이것이 라운드의 키다.** 별도 포인터 파일을 두지 않는 이유가 여기 있다 —
    작업 중인 워크트리만 있으면 경로가 나오고, 어긋날 수 있는 두 번째 진실이 생기지 않는다.
    resume.py·pr-triage 도 이 함수를 쓴다(각자 계산하면 조용히 다른 디렉토리를 가리킬 수 있다).

    **기본은 무변경 해석이다** — 이전하지 않고, 새 경로가 없으면 구 경로를 그대로 읽는다.
    구 레이아웃의 1회 이전(rename)은 migrate=True 를 명시한 **쓰기 지점 두 곳**에서만
    일어난다: run_round 의 resolve_out(정상 라운드 생성)과 resume 의 대상 확정 후 선행
    이전. 기본값이 이전이면 조회·감시 소비처가 하나 늘 때마다 부수효과 누수가 재발한다 —
    pr-triage 의 상태 조회가 정확히 그 사례였다(code-6 P1).
    """
    branch = _current_branch(cwd)
    new = RUNS_DIR / repo_key(cwd) / branch_key(branch)
    if MIGRATE and migrate:
        _migrate_legacy(cwd, branch, new)
    elif not new.exists():
        # 무변경 해석 — 기록이 현재 사는 곳을 가리킨다. 마이그레이션과 같은 판정
        # (_migration_state: 구 세대 + v2 전신)을 공유해 외부 소유 gen1 오독(code-8 P2)과
        # origin 변경 후 v2 전신 미인식(code-11 P1)을 막는다. 이 소유 검사 비용은
        # 전환기(new 부재)에만 발생한다.
        src, _ = _migration_state(cwd, branch, new)
        if src:
            return src
    return new


def resolve_out(cwd: str, gate: str) -> Path:
    """다음 라운드 디렉토리 — `<gate>-<n>` 의 n 은 자동 증가."""
    base = branch_dir(cwd, migrate=True)  # 쓰기 지점 ① — 구 레이아웃 이전은 여기서 일어난다
    if not base.exists() and (bk := migration_blocked(cwd)) and bk[0] == "unverified":
        # 확인 실패로 이전이 **보류**된 상태에서 새 이력을 시작하면 new.exists() 가 복구
        # 후의 승계까지 영구히 막는다(code-13 P1) — 여기서 멈추는 것이 유일한 가역 선택이다.
        # foreign(불일치 확정)은 새 이력이 맞으므로 막지 않는다.
        sys.exit(f"구 라운드 기록의 소유 확인에 실패해 이전이 보류됐다 — 지금 새 라운드를 만들면\n"
                 f"  확인이 복구돼도 기존 기록을 승계할 수 없게 된다(새 경로가 선점됨).\n"
                 f"  확인 실패 지점: {bk[1]}\n"
                 f"  접근 가능해진 뒤 다시 실행하고, 남의 기록이 확실하면 그 구 디렉토리를 수동 정리한다.")
    note_owner(base, cwd)
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
    # 축 선택은 순수 읽기다(기본값) — moe 가 켜진 명시적 --out 실행에서 여기가
    # 이전을 일으키면 마커 검사와 같은 경로 파괴가 난다(code-4 P1 과 동일 계열).
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
            # 빈 절만 깔아 둔다 — 키 형태 텍스트를 넣지 않는다(플레이스홀더가 식별자로 오인된다).
            "## 티켓\n<이 작업의 Jira 티켓·GitHub 이슈 식별자를 적는다 — 형식은 SKILL.md"
            " 「게이트 GO 후 — 외부 산출물 최신화」 절. 없으면 비워 둔다 (기능이 조용히 꺼진다)>",
            f"## 이 변경이 하려는 것\n{intent or '<PLAN.md 작업 분석이 비어 있다 — 직접 채운다>'}"]
    if criteria:
        body.append("## 판정 기준 (Spec AC · Gherkin)\n"
                    "아래 AC·시나리오가 이 변경의 판정 기준이다. "
                    "여기 적힌 동작이 코드에서 실제로 그렇게 되는지 본다.\n\n" + criteria)
    if gate == "plan":
        # 문서 6-구조 — B2C-52504 회고: 리뷰 32건 중 25건(78%)이 문서에서 예방 가능했고,
        # 그 빈칸이 이 여섯이다. 채우는 절차(누락 행의 사용자 clarify)는 SKILL.md 에 있다.
        body.append("## 판정 기준 — 문서 6-구조 + 계획 생존성 커버리지\n"
                    "각 행을 PRD·계획서와 대조해 채운다 — `반영(어디 — 인용)` / `해당 없음(이유)` / `누락`.\n"
                    "인용 없는 `반영` 은 무효다. `누락` 은 라운드 전에 사용자에게 물어(clarify) 해소한다 (SKILL.md).\n\n"
                    "| 구조 | 판정 | 근거·인용 |\n|---|---|---|\n"
                    "| 1. 화면별 상태 매트릭스 (조회중·무효접근·조회실패·전제불일치·정상·만료) | <반영/해당없음/누락> | |\n"
                    "| 2. 개념 사전 + 소비처 목록 (핵심 판정의 SSOT 와 쓰는 화면 전수) | <반영/해당없음/누락> | |\n"
                    "| 3. 신뢰 경계 (딥링크·params 중 믿을 값 vs 서버 권위값으로 덮을 값) | <반영/해당없음/누락> | |\n"
                    "| 4. 과도기 규정 (서버 반영 전 구간에 보여줄 것과 막을 것) | <반영/해당없음/누락> | |\n"
                    "| 5. 집계·이벤트 시점 (어느 상태 전이에서, FE/BE 중 누가 세나) | <반영/해당없음/누락> | |\n"
                    "| 6. BE 계약 위반 시 FE 기대 (규격 밖 값을 막나 통과시키나) | <반영/해당없음/누락> | |\n"
                    "| 7. 설계 수명·로드맵 충돌 (변경·신설 코드가 딛는 토대에 삭제·대체 예정이 있나 — 있다면 이 계획은 그 이후에도 유효한가) | <반영/해당없음/누락> | |\n"
                    "| 8. 통증 위치 대조 (티켓·사용자가 보고한 실제 통증 경로가 `스코프 밖` 과 겹치지 않나 — 겹치면 사용자가 승인했나) | <반영/해당없음/누락> | |")
    # 인벤토리는 초안이 만들 수 없다(호출부 grep 은 변경 대상을 알아야 한다) — 자리만 남긴다.
    body.append("## 변경 대상 인벤토리 (전수 주장)\n"
                "`<이 목록을 뽑은 명령 — 예: grep -rn \"getUsersMe\" apps/>` 기준. "
                "리뷰어가 이 명령을 다시 돌려 누락을 반증한다.\n\n"
                "| 호출부·전송지점 | 이 변경이 무엇을 바꾸나 | 의도된 동작 |\n|---|---|---|\n"
                "| <파일:줄> | <바뀌는 것> | <사용자가 무엇을 보나> |\n\n"
                "의존하는 외부 신호(SDK 콜백·이벤트·응답 필드)는 보장하지 않는 것을 함께 적는다. "
                "근거 규칙(구현 경계 기준): 호출부 최근접 설치 사본이 그 동작을 구현하면 그 소스가 "
                "최종 권위, 사본이 래퍼·바이너리 위임이면 위임 인용 + 버전 명시 공식 문서, "
                "로컬 사본이 없는 외부 시스템이면 버전/날짜 명시 공식 문서(confidence 하향):\n"
                "- `<신호>` = <보장하는 사건> (근거: <패키지명>@<설치버전> — <구현 사본 파일:줄 / "
                "래퍼면 위임 인용 + 문서 URL>). <보장하지 않는 사건>은 보장하지 않는다.")
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
        # RecursionError — 극단 중첩 JSON 이 여기서 죽으면 PARSE-FAIL 기록 전에
        # 라운드가 죽는다(extract_json 의 생존 보장과 같은 계열)
        except (ValueError, KeyError, TypeError, RecursionError):
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
            # 거대 정수는 ValueError, 극단 중첩은 RecursionError — 한 줄이
            # 스트림 전체 파싱을 죽이면 안 된다
            except (ValueError, RecursionError):
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


def rel_to_root(v: str, root: str) -> str:
    """절대경로 finding 을 저장소 루트 기준 상대경로로 정규화한다(`:줄` 접미 보존).

    병합 라운드는 기존 repo_root 를 유지하므로(기존 findings 의 정규화 기준), 워크트리가
    다른 경로에 재생성된 뒤 재실행 리뷰어가 계약을 어겨 절대경로를 내면 그 기준으로 풀 수
    없다 — 저장 시점에 현재 루트로 미리 상대화한다. 루트 밖 경로는 그대로 둔다.
    """
    m = re.fullmatch(r"(.*?)((?::\d+(?:[-:]\d+)?)?)", v)
    p, suf = m.group(1), m.group(2)
    if os.path.isabs(p) and root:
        try:
            p = str(Path(p).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            return v
    return p + suf


def extract_json(raw: str):
    """서술에서 판정 객체를 읽는다. 없으면 None.

    모델 출력 형식은 매 실행 흔들린다 — json 태그 펜스를 1순위로 두되(기존 동작),
    태그 없는 펜스·bare JSON 폴백을 둔다(issue #25: 판정을 낸 리뷰어가 펜스 없이
    출력해 PARSE-FAIL 로 버려지고, 재실행이 같은 결과를 다시 사 왔다).
    세 경로 모두 같은 수락 조건을 통과해야 한다 — dict 이고 "findings" 가 list 이고
    "verdict" 가 str 이어야 한다("findings" 키 존재만 보면 findings:null 이 뒤의
    len() 을 죽이고, verdict 없는/null 객체가 verdict None 인 성공 축으로 집계된다).
    각 경로 안에서는 마지막 후보가 이긴다 — 진짜 판정은 문서 끝에 있고 서술 중간
    예시 JSON 은 그보다 앞이다.
    """
    def accept(c):
        if isinstance(c, str):
            try:
                c = json.loads(c)
            # JSONDecodeError 가 아니라 상위형 ValueError — 4300자리 초과 정수는 int
            # 변환 제한으로 ValueError 를, 극단 중첩은 RecursionError 를 낸다(파서가
            # 여기서 죽으면 라운드째 죽는다 — PARSE-FAIL 로 남는 것이 계약이다)
            except (ValueError, RecursionError):
                return None
        # findings 원소는 dict 만 — 집계부(recompute 병합)가 원소에 setdefault 를
        # 호출하므로 비-dict 원소가 섞인 후보는 PARSE-FAIL 로 돌린다
        if isinstance(c, dict) and isinstance(c.get("findings"), list) \
                and all(isinstance(f, dict) for f in c["findings"]) \
                and isinstance(c.get("verdict"), str) and depth_of(c) <= 64 \
                and storable(c):
            return c
        return None

    def storable(c):
        """수락의 계약은 '기록 가능한 판정' 이다 — reviewer.json/round.json 이 쓰는
        직렬화 연산 그 자체를 프로브한다. lone surrogate(\\ud800 escape 복원)는
        UTF-8 인코딩이 불가능해, 수락하면 기록 단계에서 라운드째 죽는다(code-14 —
        UnicodeEncodeError 는 ValueError 하위형)."""
        try:
            json.dumps(c, ensure_ascii=False).encode("utf-8")
            return True
        except (ValueError, RecursionError):
            return False

    def depth_of(o):
        """반복문 중첩 깊이 — 유효하지만 극단적으로 깊은 객체(실측 62k 중첩)를
        수락하면 뒤의 json.dumps(indent=2) 저장이 제곱 크기로 증폭되거나
        RecursionError 로 라운드째 죽는다(code-9). 실제 판정 구조는 depth ~6 —
        상한 64 는 10배 여유이면서 직렬화 재귀·크기 증폭을 차단한다."""
        d, stack = 0, [(o, 1)]
        while stack:
            v, k = stack.pop()
            # 컨테이너 방문 시에만 깊이를 갱신한다 — scalar leaf 까지 세면 정확히
            # 64단 컨테이너 판정(main 이 수락)이 65로 계산돼 오거부된다(code-13)
            if isinstance(v, dict):
                d = max(d, k)
                stack.extend((x, k + 1) for x in v.values())
            elif isinstance(v, list):
                d = max(d, k)
                stack.extend((x, k + 1) for x in v)
        return d

    def last(candidates):
        found = None
        for c in candidates:
            got = accept(c)
            if got is not None:
                found = got
        return found

    def json_fenced():
        """1순위 경로 — main 파리티의 json 펜스 단일 패스.

        opener 는 줄 위치와 무관한 ```json 토큰이다 — 실제 리뷰어가 인라인 opener
        (`…하겠습니다.```json`)로 출력한 실측이 있고(code-9 b4, main 은 회수),
        ```json 은 closer 일 수 없어 줄 경계를 요구할 이유가 없다. closer 는 main
        의 정규식(`\\n``` `)과 같은 줄 시작 ``` — `\\n``` 토큰으로 잡으면 blank line
        뒤 opener 의 선행 개행을 closer 가 먼저 소비한다(줄 시작 앵커는 무소비).
        알터네이션 finditer 한 번이라 닫는 펜스 없는 opener 가 많아도 O(n) 이다
        (lazy 정규식의 O(n²) 재탐색 방지, code-6 P1)."""
        bodies, start = [], None
        # opener 는 3개 이상 백틱 + json — 4백틱 opener(````json)를 3백틱으로 쓰면
        # closer 대안이 offset 0 에서 먼저 소비해 main 이 회수하던 판정을 잃는다
        # (code-11). lookbehind 는 백틱 run 의 첫 위치에서만 시도하게 한다 — 없으면
        # 긴 단일 run("`"*N)에서 위치마다 재시도해 O(n²) 다(code-12, 64k 실측 3.7초)
        for m in re.finditer(r"(?<!`)`{3,}json[^\S\n]*\n|^```", raw, re.M):
            if start is not None:
                # 블록 안에서는 줄 시작의 ``` 가 태그 반복 여부와 무관하게 closer 다
                # — main 의 closer(\n```)는 "```json" 닫는 줄의 앞 세 백틱에도
                # 매치했다(code-10). 닫은 줄은 재개방하지 않고(main 의 소비 동작과
                # 동일), 인라인 ```json 은 블록을 닫지 않는다(main 도 안 닫았다).
                if m.start() == 0 or raw[m.start() - 1] == "\n":
                    bodies.append(raw[start:m.start()])
                    start = None
            elif m.group(0) != "```":
                start = m.end()
        return bodies

    def untagged_fenced():
        """2순위 경로 — 태그 없는 펜스, 줄 단위 전역 짝짓기.

        모든 펜스를 여닫아 짝짓고 태그 없는 펜스의 본문만 낸다 — 태그별로 따로
        스캔하면 언어 태그 펜스의 닫는 ``` 가 태그 없는 opener 로 오인된다
        (code-3 실측). 이 경로 전체가 이 브랜치에서 추가된 것이라 main 대비
        회수 손실이 없다. closer 는 opener 길이 이상의 백틱만으로 된 줄
        (Markdown 규칙 — 4백틱 closer, code-7)."""
        bodies, body, info, opener = [], None, None, 0
        for line in raw.split("\n"):
            stripped = line.strip()
            ticks = len(stripped) - len(stripped.lstrip("`"))
            if body is None:
                if ticks >= 3:
                    opener, info, body = ticks, stripped[ticks:].strip(), []
            elif ticks >= opener and stripped == "`" * len(stripped):
                if info == "":
                    bodies.append("\n".join(body))
                body = None
            else:
                body.append(line)
        return bodies

    obj = last(json_fenced())
    if obj is None:
        obj = last(untagged_fenced())
    if obj is None:
        # bare JSON — 정방향 span-skip: `{` 마다 raw_decode 를 시도하고 성공하면
        # 그 객체의 끝으로 점프한다. 내부 중첩 객체를 따로 보지 않으므로 바깥 판정
        # 안의 findings 원소가 판정으로 오인되지 않고(역방향 스캔의 실측 결함),
        # 정규식 중괄호 균형 카운팅의 문자열-리터럴 문제도 없다.
        # 단 **문서를 끝내는 객체만** 수락한다(뒤가 공백뿐) — 산문 속에 파묻힌
        # 형식 예시를 미완주 리뷰어의 판정으로 오인하지 않기 위해서다(code-2 P1).
        # 실측 사고(#25)의 판정은 서술 마지막에 있었고, 판정 뒤에 서술이 더 붙는
        # 출력은 변경 전과 같은 PARSE-FAIL 로 남는다 — 악화가 아니다.
        dec = json.JSONDecoder()

        # 문서끝 인덱스는 1회만 계산한다 — 후보마다 raw[end:] 를 slice+strip 하면
        # 연속 객체 입력에서 복사량이 제곱으로 는다(code-6 P2)
        doc_last = len(raw.rstrip())

        def doc_end():
            # 통합 정책(code-13): **모든 opener(인라인 포함)가 유효 파스 연쇄에
            # 포함**되고, **후보 자격은 줄 시작(선행 공백 허용) opener 뿐**이다.
            # - 인라인 opener 도 raw_decode 로 소비한다 — 무시하면 인라인에서 열린
            #   미종결 컨테이너 안의 줄 시작 조각이 판정으로 오인된다(code-13 P1).
            # - 인라인이 유효해도 후보는 아니다 — 산문 구문(미종결 인용 등)의
            #   일부일 수 있어 독립 JSON 블록이 아니다(code-12 P1).
            # 실측 사고(#25)의 판정은 제 줄에서 시작했고 서술에 중괄호가 없다.
            pos = 0
            for m in re.finditer(r"^[ \t]*([{\[])|[{\[]", raw, re.M):
                p = m.start(1) if m.group(1) else m.start()
                # 이미 소비한 span 내부(예: pretty-print 된 판정의 내부 줄)는 후보가
                # 아니다 — 유효 파스의 연쇄 위에서만 다음 후보를 본다
                if p < pos:
                    continue
                # `[` 컨테이너도 raw_decode 로 통째로 소비한다 — 닫히지 않은 배열
                # 안의 판정 조각이 독립 후보로 오인되지 않게(code-6 P2). 판정은
                # dict 뿐이라 배열 자체는 후보가 아니다.
                try:
                    cand, end = dec.raw_decode(raw, p)
                except (ValueError, RecursionError):
                    # 파싱이 실패하면 이후는 아무것도 후보가 아니다. 실패한 span 의
                    # 문법(문자열·escape·주석·인용 규약)은 알 수 없으므로 어떤
                    # 휴리스틱 경계도 그 안의 closer 에 속아 내부 조각을 판정으로
                    # 오인시킬 수 있다 — double quote 추적(code-4)→종류 stack(code-5)
                    # →인용부호 의심(code-7)→escape·주석(code-8) 4연속 실측이 증명.
                    # bare 수락은 "유효 JSON 파스의 연쇄" 위에만 놓는다. 실패 이후의
                    # 판정 유실은 main 과 같은 PARSE-FAIL 이라 악화가 아니고, 실측
                    # 사고(#25) 원본(서술 484자 + bare 판정, 앞선 opener 없음)은
                    # 이 정책에서도 회수됨을 검증했다.
                    return
                if m.group(1) and raw[p] == "{" and end >= doc_last:
                    yield cand
                pos = end
        obj = last(doc_end())
    return obj


def count_access_errors(raw: str, cwd: str) -> int:
    """리뷰 대상 루트(cwd) 자체에 닿지 못한 줄만 센다.

    리뷰어가 없는 하위 파일을 추측해 열거나 재현 스크립트를 돌리다 내는
    FileNotFoundError 는 정상 행동이다 — 그런 줄은 cwd 뒤에 하위 경로가 이어지므로
    (`{cwd}/...`), cwd 가 경로의 끝으로 등장하는 줄만 접근 실패로 본다(issue #11).
    ACP JSON 은 최종 non-zero terminal 출력만 본다 — prompt·명령·성공한 로그 검색에
    같은 문자열이 있어도 실행 실패가 아니다.
    """
    root = re.escape(cwd.rstrip("/")) + r"/?(?![\w.\-/])"
    outputs = []

    def add_output(value):
        # 명시적 스택 반복문 — json.loads 가 성공한 깊은 중첩 이벤트를 재귀로
        # 순회하면 RecursionError 로 extract_json 전에 라운드가 죽는다(code-11)
        stack = [value]
        while stack:
            v = stack.pop()
            if isinstance(v, str):
                outputs.extend(v.splitlines())
            elif isinstance(v, list):
                stack.extend(reversed(v))
            elif isinstance(v, dict):
                stack.extend(v[key] for key in reversed(
                    ("formatted_output", "error", "text", "message",
                     "stdout", "stderr", "content", "output")) if key in v)

    terminal_requests = {}
    terminal_outputs = {}
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        # JSONDecodeError 만 잡으면 거대 정수(ValueError)·극단 중첩(RecursionError)
        # 한 줄이 라운드째 죽인다 — 이 함수는 extract_json 보다 먼저 불리므로
        # 여기가 뚫리면 파서의 생존 보장에 도달하지 못한다(code-4 지적)
        except (ValueError, RecursionError):
            outputs.append(line)
            continue
        if not isinstance(event, dict) or event.get("jsonrpc") != "2.0":
            result = event.get("result") if isinstance(event, dict) else None
            outputs.append(result if isinstance(result, str) else line)
            continue
        event_id = event.get("id")
        method = event.get("method")
        if method in ("terminal/output", "terminal/wait_for_exit") \
                and isinstance(event_id, (str, int)):
            params = event.get("params", {})
            terminal_id = params.get("terminalId") if isinstance(params, dict) else None
            terminal_requests[event_id] = (method, terminal_id)
        request = terminal_requests.get(event_id) if isinstance(event_id, (str, int)) else None
        result = event.get("result")
        if request and isinstance(result, dict):
            request_method, terminal_id = request
            status = result.get("exitStatus", result)
            failed = isinstance(status, dict) and \
                (status.get("exitCode") not in (None, 0) or status.get("signal"))
            if request_method == "terminal/output":
                output = result.get("output")
                if failed and isinstance(output, str):
                    for previous in terminal_outputs.get(terminal_id, []):
                        outputs.extend(previous.splitlines())
                    outputs.extend(output.splitlines())
                elif isinstance(output, str) and terminal_id is not None:
                    terminal_outputs.setdefault(terminal_id, []).append(output)
            elif failed and terminal_id in terminal_outputs:
                for output in terminal_outputs[terminal_id]:
                    outputs.extend(output.splitlines())
            continue
        if method in ("terminal/output", "terminal/wait_for_exit"):
            continue
        if event.get("method") != "session/update":
            continue
        params = event.get("params")
        if not isinstance(params, dict):
            continue
        update = params.get("update", {})
        if not isinstance(update, dict):
            continue
        meta = update.get("_meta", {}) if isinstance(update, dict) else {}
        result = update.get("rawOutput", {})
        terminal_exit = meta.get("terminal_exit", {}) if isinstance(meta, dict) else {}
        exit_code = result.get("exit_code") if isinstance(result, dict) else None
        if exit_code is None and isinstance(terminal_exit, dict):
            exit_code = terminal_exit.get("exit_code")
        signal = terminal_exit.get("signal") if isinstance(terminal_exit, dict) else None
        if exit_code in (None, 0) and not signal and update.get("status") != "failed":
            continue
        if isinstance(result, dict):
            for key in ("formatted_output", "error", "text", "message",
                        "stdout", "stderr", "content", "output"):
                if key in result:
                    add_output(result[key])
            content = result.get("result", {}).get("content", []) \
                if isinstance(result.get("result"), dict) else []
            for item in content if isinstance(content, list) else []:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    outputs.extend(item["text"].splitlines())
        elif isinstance(result, str):
            add_output(result)
        content = update.get("content", [])
        for item in content if isinstance(content, list) else []:
            nested = item.get("content", {}) if isinstance(item, dict) else {}
            if isinstance(nested, dict) and isinstance(nested.get("text"), str):
                outputs.extend(nested["text"].splitlines())
    return sum(1 for line in outputs
               if ("No such file or directory" in line or "not a git repository" in line)
               and re.search(root, line))


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
    lost = count_access_errors(raw, cwd)
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
        # raw 크기·엔진 에러 수로 실측 3유형이 로그 한 줄에서 갈린다(issue #25) —
        # 2k 내외+에러 = 모델 용량/실행 환경(산출물 없음, 재실행), 정상 크기+에러 0 =
        # 파서 미스 의심(raw 끝에 판정이 있을 수 있다).
        errs = 0
        for line in raw.splitlines():
            try:
                ev = json.loads(line)
            # ValueError(거대 정수)·RecursionError(극단 중첩) — 진단 루프가
            # 라운드를 죽이면 extract_json 의 생존 보장이 무효가 된다(code-2·3 P2)
            except (ValueError, RecursionError):
                continue
            # claude 는 에러를 최상위 error 키가 아니라 is_error 로 표시한다
            if isinstance(ev, dict) and (ev.get("error") or ev.get("is_error")):
                errs += 1
        print(f"  {reviewer:24} PARSE-FAIL  {label}"
              f"  raw {len(raw)/1000:.1f}k, error {errs}건{warn}", flush=True)
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

    같은 계열의 세 번째 입구가 `verdict_dissent` 다 — 축이 findings 없이 `NO-GO` 를 내는
    경우(라운드 밖 근거: 미반영 지적, context.md 의 지시)가 findings 수만 보는 계산에
    흡수돼 `GO` 로 집계된다(실측: b2b PR #915 code-10, 5축 전원 NO-GO → 라운드 GO).
    verdict 자체는 여전히 findings 에서만 도출한다 — 근거 없는 NO-GO 가 게이트를 세우는
    것을 막는 방어라서 풀지 않는다. 대신 불일치를 조용히 버리지 않고 라운드에 남긴다.
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
    # 축이 GO 라고 안 한 GO — coverage 와 같은 성질이라 같은 자리에 남긴다. GO 일 때만
    # 채운다(NO-GO·INVALID 라운드는 이미 통과가 아니라 불일치가 의미를 갖지 않는다).
    # 병합(top-up)으로 그 축이 GO 를 내면 여기서 자동으로 비워진다.
    merged["verdict_dissent"] = (sorted(r for r, v in merged["reviewers"].items() if v == "NO-GO")
                                 if merged["verdict"] == "GO" else [])
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
            plain = target / f"{r}{suf}"
            compressed = target / f"{r}{suf}.gz"
            if plain.exists():
                plain.rename(target / f"{r}.superseded-{stamp}{suf}")
            if compressed.exists():
                compressed.rename(target / f"{r}.superseded-{stamp}{suf}.gz")
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
    ap.add_argument("--out", default=None, help="생략 시 ~/.red-team/runs2/... 로 자동 결정")
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
    # 중단 선언된 티켓에 직접 실행으로 라운드·초안을 쌓는 실수를 조기에 알린다 — zax_draft
    # 의 조기 return 과 출력 디렉토리 생성보다 앞이어야 어떤 직접 실행 경로에서도 경고가
    # 우회되지 않는다(code-3 지적). 차단은 하지 않는다 — --out 실험·eval 흐름과 의도적
    # 재실행을 막지 않는다(차단은 resume 의 몫).
    def warn_if_aborted(marker: Path):
        if marker.exists():
            print(f"⚠ 이 티켓은 중단 선언돼 있다: {marker}\n"
                  f"  재개 의사가 아니면 이 라운드는 낭비다 — 사유는 그 파일에 있고, "
                  f"재개는 그 파일 삭제로만 한다.", flush=True)

    # 경고가 검사하는 상태와 사용자가 실제로 바꾸는 라운드 상태가 같아야 한다 —
    # 병합이면 병합 대상, --out 이면 그 부모(둘 다 브랜치 디렉토리)가 실제 대상이다
    # (code-5·6 지적: cwd 만 보면 다른 브랜치를 가리키는 --merge-into/--out 이 경고를 우회한다).
    markers = []
    if a.merge_into:
        markers.append(Path(a.merge_into).resolve().parent / "ABORTED")
    else:
        if a.cwd:
            # 경고용 파생은 순수 읽기다(기본값) — 여기서 이전이 일어나면 명시적
            # --context/--out 이 구 레이아웃을 가리키는 직접 실행이 자기 입력 경로를
            # 잃는다(code-4 P1). 실제 이전은 resolve_out 경유 정상 경로가 한다.
            markers.append(branch_dir(a.cwd) / "ABORTED")
        if a.out:
            markers.append(Path(a.out).resolve().parent / "ABORTED")
    for m in dict.fromkeys(markers):  # 같은 마커면 한 번만
        warn_if_aborted(m)

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

    # 컨텍스트는 resolve_out **전에** 읽는다 — resolve_out 의 이전(rename)이 --context 가
    # 가리키는 구 경로를 옮기면, 뒤에서 읽을 때 FileNotFoundError 로 죽는다(code-7 P2).
    # 신선도 경고의 입력(계획서 목록·mtime)도 같은 이유로 여기서 캡처한다.
    ctx_src = Path(a.context)
    context = ctx_src.read_text()
    ctx_mtime = ctx_src.stat().st_mtime
    plan_mtimes = sorted((p.name, p.stat().st_mtime) for p in ctx_src.parent.iterdir()
                         if p.is_file() and re.fullmatch(r"plan.*\.md", p.name, re.I))
    out = Path(a.merge_into) if a.merge_into else (Path(a.out) if a.out else resolve_out(a.cwd, a.gate))
    out.mkdir(parents=True, exist_ok=True)
    round_lock = lock_round(out) if (out / "round.json").exists() else None
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
    for plan_name, plan_mtime in plan_mtimes:  # 입력은 resolve_out 전에 캡처됨(code-7 P2)
        if plan_mtime > ctx_mtime + 1:
            print(f"⚠ {plan_name} 이 {ctx_src.name} 보다 새롭다.\n"
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
        # repo_root: 절대경로 findings 를 저장소 상대 경로로 정규화할 때의 기준.
        # repo_cwd 는 --cwd 그대로라 저장소 하위 디렉토리일 수 있다 — 그걸 기준으로 삼으면
        # 같은 파일이 라운드마다 다른 경로로 남는다(resume.py same-origin 감지의 미탐 원인).
        "repo_root": git(a.cwd, "rev-parse", "--show-toplevel") or str(Path(a.cwd).resolve()),
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
            if prepared is not None:
                # 병합 라운드는 기존 repo_root 를 유지한다 — 새 findings 의 절대경로는
                # 현재 워크트리 루트로 미리 상대화해야 resume 의 same-origin 정규화가 풀린다.
                cur_root = git(a.cwd, "rev-parse", "--show-toplevel") if a.cwd else ""
                for k in ("origin_file", "file"):
                    if isinstance(f.get(k), str) and f[k].strip():
                        f[k] = rel_to_root(f[k].strip(), cur_root)
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
    def heal(reviewers):
        return shell_command("python3", Path(__file__).resolve(), "--cwd", a.cwd,
                             "--gate", a.gate, "--merge-into", out,
                             "--reviewers", ",".join(reviewers))
    if merged["verdict"] == "INVALID":
        print(f"⚠ 리뷰어 전원이 결과를 내지 못했다 — 이 라운드는 판정이 아니다.\n"
              f"  engines={'+'.join(engines)} 이 실제로 돌았는지 확인한다"
              f"({out}/*.txt 첫 줄이 흔히 이유를 말해준다).")
    else:
        # 혼합 라운드에서 한 엔진만 통째로 죽으면(예: claude 로그인 풀림) 나머지 엔진의
        # GO 에 묻혀 조용히 통과한다 — 7/30 `Not logged in` 사고의 재발 경로라 표면화한다.
        by_engine = {}
        for r, parsed, _lost, _tokens in results:
            by_engine.setdefault(assignments[r][0], []).append(parsed is None)
        dead_engines = [e for e, fails in by_engine.items() if all(fails)]
        for e in dead_engines:
            print(f"⚠ engine={e} 리뷰어 전원({len(by_engine[e])}명)이 결과를 내지 못했다 — "
                  f"그 축들이 빠진 {merged['verdict']} 는 반쪽짜리다.\n"
                  f"  {e} 상태를 확인한 뒤 그 축만 다시 돌려 이 라운드에 병합한다:\n"
                  + "    " + heal(r for r in reviewers if assignments[r][0] == e))
        # 엔진이 통째로 죽은 게 아니라 일부만 PARSE-FAIL 이면 라운드를 버리지 않는다 —
        # SKILL.md 가 "그 리뷰어만 1회 재실행" 을 지시하는데, 되돌릴 수단이 병합이다.
        fail = [r for r, v in merged["reviewers"].items() if v == "PARSE-FAIL"]
        if fail and not dead_engines:
            print(f"⚠ PARSE-FAIL: {','.join(fail)} — 그 축이 빠진 {merged['verdict']} 는 반쪽짜리다.\n"
                  f"  그 리뷰어만 1회 재실행해 이 라운드에 병합한다:\n    " + heal(fail))
    if merged["access_errors"]:
        # 이 리뷰어 결과만 신뢰할 수 없다 — 라운드 전체를 버릴 필요는 없고, 손으로 round.json 을
        # 고칠 필요도 없다. 병합 경로가 verdict·counts·access_errors 를 다시 계산한다.
        print(f"⚠ 파일접근오류: {merged['access_errors']} — 그 리뷰어 결과는 신뢰할 수 없다.\n"
              f"  리뷰 대상 디렉토리가 실행 중 사라지지 않는 위치인지 확인한 뒤,\n"
              f"  그 리뷰어만 다시 돌려 이 라운드에 병합한다:\n"
              f"    {heal(merged['access_errors'])}\n"
              f"  (round.json 을 손으로 고치지 않는다 — 병합이 verdict·counts·access_errors 를 다시 계산한다)")
    if merged["verdict"] == "GO" and merged.get("coverage") == "partial":
        # 축을 빼는 것은 "생략"이 아니라 "유예"다 — 빠진 축이 못 본 결함은 GO 로 결론나면 안 된다.
        # 이 top-up 병합이 채워진 뒤의 verdict 만 게이트 판정이다 (resume.py 도 같은 규칙을 본다).
        if merged["skipped"]:
            print(f"⚠ 축 {','.join(merged['skipped'])} 가 빠진 GO 다 (coverage=partial) — 게이트 통과가 아니다.\n"
                  f"  빠진 축을 이 라운드에 병합해 커버리지를 채운 뒤의 verdict 가 판정이다:\n"
                  f"    {heal(merged['skipped'])}")
        if merged["unparsed"]:
            # 재실행 명령은 위 PARSE-FAIL 경고가 이미 안내했다 — 여기서는 그 GO 의 성격을 못 박는다.
            # 경고문을 읽었는지에 의존하지 않으려고 coverage 로 남기는 것이 이 분기의 목적이다.
            print(f"⚠ 축 {','.join(merged['unparsed'])} 가 결과를 내지 못한 GO 다 (coverage=partial) — "
                  f"게이트 통과가 아니다.\n"
                  f"  위 안내대로 그 축만 재실행해 이 라운드에 병합한 뒤의 verdict 가 판정이다.")
    if merged["verdict_dissent"]:
        # 축은 다 돌았고(coverage=full) findings 도 0 인데 축 스스로는 NO-GO 를 냈다 —
        # 라운드 밖 근거를 본 것이다. 치유 명령이 없는 유일한 경고다(재실행이 아니라
        # 사람이 그 근거를 읽고 판단하는 자리다).
        print(f"⚠ 축 {','.join(merged['verdict_dissent'])} 는 NO-GO 를 냈는데 findings 0 이라 GO 로 집계됐다.\n"
              f"  리뷰어가 라운드 밖 근거(미반영 지적·컨텍스트 지시)로 NO-GO 를 낸 경우다 —\n"
              f"  그 근거를 확인하기 전에는 게이트 통과가 아니다.\n"
              f"  근거는 그 축의 raw 에 있다: {out}/<축>.txt")

    if round_lock is not None:
        round_lock.close()
    auto_archive()


if __name__ == "__main__":
    main()
