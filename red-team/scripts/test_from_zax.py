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

## 코드 현황 (Code-Hub)
- useUser.tsx:52 에서 error 시 data 를 버린다
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
    for need in ("## 리뷰 대상", "## 이 변경이 하려는 것", "## 스코프 밖 (지적 금지)",
                 "## 이미 반영된 지적 (재제기 금지)", "## 검증 상태", "## 계획 전문"):
        assert need in body, f"절 누락: {need}"
    assert "동의 캐시 동기화를 type 병합으로" in body, "PLAN 작업 분석이 안 실렸다"
    assert "selectUserJudgment" in body, "CONTEXT Architecture 합의가 안 실렸다"
    assert "동시 응답 순서 보장 여부" in body, "미확인 항목이 안 실렸다"
    assert "순차: #1 → #2" in body, "plan 게이트는 계획 전문을 실어야 한다"

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

    print("PASS — 초안 생성/멈춤, 덮어쓰기 금지, 게이트별 내용, 대문자 PLAN.md 신선도 경고, 인자 검증 모두 정상")


if __name__ == "__main__":
    main()
