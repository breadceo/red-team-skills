"""컨텍스트 절 소실·TODO 잔류 검사 (#43).

잘린 컨텍스트로도 라운드가 돌아 GO 까지 났던 경로를 소비 지점에서 막는지 본다.
`--reviewers ""` 로 부르므로 엔진은 호출되지 않는다 — 검사는 그 전에 끝난다.
"""
import json, os, pathlib, subprocess, sys, tempfile

RUNNER = pathlib.Path(__file__).resolve().parent / "run_round.py"
TODO = "<!-- TODO(resume): 이 절을 이번 라운드 기준으로 갱신하라 -->"

FULL = """# Context: sample

## 리뷰 대상
- 구현 diff

## 검증 상태
- 테스트 통과

## 스코프 밖(지적 금지)
- 광고 슬롯

## 이미 반영된 지적(재제기 금지)
- 없음

## 이번 라운드에서 특히 볼 것 (선택)
- 캐시 병합
"""


def run(base, ctx, *args):
    env = {**os.environ, "RED_TEAM_ENGINE": "claude"}
    return subprocess.run(
        [sys.executable, str(RUNNER), "--cwd", str(base), "--context", str(ctx),
         "--out", str(base / "code-2"), "--reviewers", "", *args],
        capture_output=True, text=True, env=env)


def make_prev(base, text=FULL):
    prev = base / "code-1"
    prev.mkdir(parents=True)
    (prev / "context.md").write_text(text)
    (prev / "round.json").write_text(json.dumps({"findings": []}))
    return prev


def main():
    # 1. 직전 라운드 없음 — 비교 대상이 없으니 그냥 통과한다
    base = pathlib.Path(tempfile.mkdtemp())
    ctx = base / "context.md"
    ctx.write_text(FULL)
    r = run(base, ctx)
    assert r.returncode == 0 and "리뷰어 없음" in r.stdout, r.stdout + r.stderr
    print("  ok  첫 라운드는 절 비교를 건너뛴다")

    # 2. TODO 가 남아 있으면 중단하고 그 절 이름을 알린다
    base = pathlib.Path(tempfile.mkdtemp())
    ctx = base / "context.md"
    ctx.write_text(FULL.replace("## 검증 상태\n", f"## 검증 상태\n{TODO}\n"))
    r = run(base, ctx)
    assert r.returncode != 0 and "## 검증 상태" in r.stderr, r.stdout + r.stderr
    print("  ok  TODO 잔류는 중단 사유다")

    # 3. 직전 라운드에 있던 절이 사라지면 중단하고 이름을 출력한다 (실측: 억제 절 2개 소실)
    base = pathlib.Path(tempfile.mkdtemp())
    make_prev(base)
    ctx = base / "context.md"
    cut = FULL[:FULL.index("## 스코프 밖")]
    ctx.write_text(cut)
    r = run(base, ctx)
    assert r.returncode != 0, r.stdout
    assert "## 스코프 밖(지적 금지)" in r.stderr and "## 이미 반영된 지적(재제기 금지)" in r.stderr, r.stderr
    assert "code-1" in r.stderr, r.stderr  # 복구처를 알려준다
    print("  ok  절 소실은 중단 사유이고 사라진 절을 전부 이름으로 알린다")

    # 4. --allow-section-drop 은 경고만 하고 진행한다 (의도적 삭제의 탈출구)
    r = run(base, ctx, "--allow-section-drop")
    assert r.returncode == 0 and "리뷰어 없음" in r.stdout, r.stdout + r.stderr
    assert "⚠" in r.stdout and "## 스코프 밖(지적 금지)" in r.stdout, r.stdout
    print("  ok  --allow-section-drop 은 경고 후 진행한다")

    # 5. 꼬리 괄호만 달라진 절은 소실이 아니다 ('(선택)' 유무로 오탐이 나면 못 쓴다)
    base = pathlib.Path(tempfile.mkdtemp())
    make_prev(base)
    ctx = base / "context.md"
    ctx.write_text(FULL.replace("## 이번 라운드에서 특히 볼 것 (선택)",
                                "## 이번 라운드에서 특히 볼 것"))
    r = run(base, ctx)
    assert r.returncode == 0 and "리뷰어 없음" in r.stdout, r.stdout + r.stderr
    print("  ok  '(선택)' 같은 꼬리 괄호 차이는 소실로 보지 않는다")

    print("PASS — 첫 라운드 예외, TODO 잔류 차단, 절 소실 차단·복구처 안내, "
          "탈출구, 꼬리 괄호 정규화 모두 정상")


if __name__ == "__main__":
    main()
