"""--from-zax 어댑터 + 계획서 신선도 감지(대소문자 무시) 검증."""
import os, pathlib, subprocess, sys, tempfile, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 설치 위치를 가정하지 않는다
RUNNER = pathlib.Path(__file__).resolve().parent / "run_round.py"

PLAN = """# 작업 계획: sample

## 작업 분석

**목표:** 동의 캐시 동기화를 type 병합으로 바꾼다
**범위:** 포함 — useUser 판정 소스 / 제외 — 광고 슬롯 표시 로직
**제약:** 기존 호출부 4곳 유지

## 서브태스크 분해

1. [code] 캐시 병합 (미확인: 동시 응답 순서 보장 여부)
2. [code] 호출부 연결

## 실행 계획

순차: #1 → #2
"""
CONTEXT = """# Context: sample

## PRD 요약
- 목표: 동의 상태가 조회 실패에 덮이지 않게 한다

## Architecture 합의
- 판정은 selectUserJudgment 로 파생한다

## Spec AC 매핑
- AC-1: 조회 실패 시 '미보유' 로 보이지 않는다 → 서브태스크 #1 매핑
- AC-2: 재시도 버튼이 실제로 재조회한다 → 서브태스크 #2 매핑

## Gherkin 시나리오
- Scenario 1 (feature_consent.feature): 조회 실패 후 재시도 → 서브태스크 #2 매핑

## 코드 현황 (Code-Hub)
- useUser.tsx:52 에서 error 시 data 를 버린다
"""

NO_CRITERIA_CONTEXT = """# Context: bare

## PRD 요약
- 목표: 문구만 고친다
"""


def run(task_home, *args):
    """러너를 서브프로세스로 돌린다.

    `--reviewers ""` 로 부르는 케이스는 리뷰어 0명이라 라운드 자체는 실패한다(엔진을 부르지
    않는다) — 검증 대상인 초안 처리와 신선도 경고는 그 전에 stdout 으로 나오므로 종료코드는
    보지 않는다. 엔진은 환경변수로 고정해 config.json 유무에 흔들리지 않게 한다.
    """
    env = {**os.environ, "ZB_TASK_HOME": str(task_home), "RED_TEAM_ENGINE": "claude"}
    return subprocess.run([sys.executable, str(RUNNER), *args],
                          capture_output=True, text=True, env=env)


def main():
    tmp = pathlib.Path(tempfile.mkdtemp())
    tdir = tmp / "sample"
    tdir.mkdir()
    (tdir / "PLAN.md").write_text(PLAN)
    (tdir / "CONTEXT.md").write_text(CONTEXT)

    # 1. 초안 생성 — 만들고 멈춘다 (리뷰어를 돌리지 않는다)
    r = run(tmp, "--cwd", str(tmp), "--from-zax", "sample", "--gate", "plan")
    ctx = tdir / "redteam-context.md"
    assert r.returncode == 0, r.stderr
    assert ctx.exists(), "초안이 생성되지 않았다"
    assert "초안을 만들었다" in r.stdout, r.stdout
    assert "round:" not in r.stdout, "초안만 만들고 멈춰야 하는데 라운드가 돌았다"

    body = ctx.read_text()
    for need in ("## 리뷰 대상", "## 이 변경이 하려는 것", "## 판정 기준 (Spec AC · Gherkin)",
                 "## 스코프 밖 (지적 금지)", "## 이미 반영된 지적 (재제기 금지)",
                 "## 검증 상태", "## 계획 전문"):
        assert need in body, f"절 누락: {need}"
    assert "동의 캐시 동기화를 type 병합으로" in body, "PLAN 작업 분석이 안 실렸다"
    assert "selectUserJudgment" in body, "CONTEXT Architecture 합의가 안 실렸다"
    assert "동시 응답 순서 보장 여부" in body, "미확인 항목이 안 실렸다"
    assert "순차: #1 → #2" in body, "plan 게이트는 계획 전문을 실어야 한다"
    assert "AC-1:" in body and "AC-2:" in body, "Spec AC 매핑이 판정 기준으로 안 실렸다"
    assert "feature_consent.feature" in body, "Gherkin 시나리오가 안 실렸다"
    # 판정 기준은 '하려는 것' 뒤에, 스코프 밖 앞에 온다 — 리뷰어가 기준으로 읽어야 한다
    assert body.index("## 이 변경이 하려는 것") < body.index("## 판정 기준") < body.index("## 스코프 밖"), \
        "판정 기준 절 위치가 어긋났다"
    # 계획 게이트 초안에는 문서 6-구조 커버리지 표가 빈 채로 깔린다 (채우는 것은 clarify 단계)
    assert "## 판정 기준 — 문서 6-구조 커버리지" in body, "6-구조 표가 계획 초안에 없다"
    assert body.count("<반영/해당없음/누락>") == 6, "6-구조 행이 6개가 아니다"
    assert "신뢰 경계" in body and "과도기 규정" in body, "6-구조 행 내용이 빠졌다"

    # 2. 이미 있으면 덮지 않는다 — 사람이 좁혀둔 스코프가 날아가면 안 된다
    ctx.write_text(body.replace("<PLAN.md 범위의 '제외되는 것'", "광고 슬롯 표시 로직(별도 티켓)"))
    edited = ctx.read_text()
    r2 = run(tmp, "--cwd", str(tmp), "--from-zax", "sample", "--gate", "plan", "--reviewers", "")
    assert ctx.read_text() == edited, "기존 컨텍스트를 덮어썼다"
    assert "초안을 만들었다" not in r2.stdout, "두 번째 실행에서 초안을 다시 만들었다"

    # 3. code 게이트는 계획 전문을 싣지 않는다 (diff 가 리뷰 대상이다)
    (tdir / "redteam-context.md").unlink()
    run(tmp, "--cwd", str(tmp), "--from-zax", "sample", "--gate", "code")
    code_body = ctx.read_text()
    assert "## 계획 전문" not in code_body, "code 게이트에 계획 전문이 실렸다"
    assert "git diff" in code_body, "code 게이트 리뷰 대상이 diff 가 아니다"
    assert "문서 6-구조" not in code_body, "6-구조 표는 계획 게이트 전용이다 (코드 게이트로는 이관으로 온다)"

    # 4. PLAN.md 가 컨텍스트보다 새로우면 경고한다 (대문자 파일명도 잡아야 한다)
    time.sleep(1.1)
    (tdir / "PLAN.md").write_text(PLAN + "\n5. [code] 추가 발견 사항\n")
    r4 = run(tmp, "--cwd", str(tmp), "--from-zax", "sample", "--gate", "code", "--reviewers", "")
    assert "PLAN.md 이" in r4.stdout and "새롭다" in r4.stdout, \
        f"대문자 PLAN.md 신선도 경고가 안 떴다:\n{r4.stdout}"

    # 5. --from-zax 와 --context 동시 사용은 막는다
    r5 = run(tmp, "--cwd", str(tmp), "--from-zax", "sample", "--context", str(ctx))
    assert r5.returncode != 0 and "함께 쓸 수 없다" in r5.stderr, r5.stderr

    # 6. task 디렉토리가 없으면 안내하고 죽는다
    r6 = run(tmp, "--cwd", str(tmp), "--from-zax", "no-such-task")
    assert r6.returncode != 0 and "task 디렉토리가 없다" in r6.stdout + r6.stderr

    # 7. AC·시나리오가 없는 CONTEXT 면 판정 기준 절을 만들지 않는다 (빈 절로 리뷰어를 흔들지 않는다)
    bare = tmp / "bare"
    bare.mkdir()
    (bare / "PLAN.md").write_text(PLAN)
    (bare / "CONTEXT.md").write_text(NO_CRITERIA_CONTEXT)
    run(tmp, "--cwd", str(tmp), "--from-zax", "bare", "--gate", "code")
    bare_body = (bare / "redteam-context.md").read_text()
    assert "## 판정 기준" not in bare_body, "AC 가 없는데 빈 판정 기준 절을 만들었다"
    assert "문구만 고친다" in bare_body, "PRD 요약이 안 실렸다"

    print("PASS — 초안 생성/멈춤, 덮어쓰기 금지, 게이트별 내용, 판정 기준 절(유/무), "
          "대문자 PLAN.md 신선도 경고, 인자 검증 모두 정상")


if __name__ == "__main__":
    main()
