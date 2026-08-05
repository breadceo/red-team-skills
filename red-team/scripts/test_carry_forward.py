"""resume.carry_forward 2라운드 누적 검증 — 이전 라운드 반영분이 살아남는지."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # 설치 위치를 가정하지 않는다
from resume import carry_forward, pending

CTX = """## 리뷰 대상

git diff master..HEAD

## 이 변경이 하려는 것

X 를 고친다.

## 스코프 밖 (지적 금지)

- 원래부터 있던 항목 Z

## 이미 반영된 지적 (재제기 금지)

없음 — 첫 라운드다.

## 검증 상태

tsc 통과
"""

DEC1 = """# code-1 처리 결과

## 반영 — 다음 컨텍스트의 `이미 반영된 지적` 이 된다
- [b2] 버튼 A 가 죽어 있었다 → invalidate 연결 (커밋 aaa)
  세부: 원인은 서버컴포넌트 캐시와 클라이언트 observer 가 별개였던 것

## 후속 티켓 — 다음 컨텍스트의 `스코프 밖` 이 된다
- [b1] pre-existing 지연 → TICKET-111 신설

## 보류 — 사용자가 아직 결정하지 않음
- (없으면 비운다)
"""

DEC2 = """# code-2 처리 결과

## 반영 — 다음 컨텍스트의 `이미 반영된 지적` 이 된다
- [b3] 대비 미달 → #9ca3af 로 (커밋 bbb)

## 후속 티켓 — 다음 컨텍스트의 `스코프 밖` 이 된다
- [a-code] 디자인시스템 대비 → TICKET-222

## 보류 — 사용자가 아직 결정하지 않음
"""

r2 = carry_forward(CTX, DEC1, "code-1")
r3 = carry_forward(r2, DEC2, "code-2")

# 1) 1라운드 반영분이 3라운드 컨텍스트에도 살아 있어야 한다
assert "버튼 A 가 죽어 있었다" in r3, "code-1 반영분이 유실됐다 (replace 버그 재발)"
assert "대비 미달" in r3, "code-2 반영분이 누락됐다"
# 2) 후속 티켓도 둘 다 누적
assert "TICKET-111" in r3 and "TICKET-222" in r3, "후속 티켓 누적 실패"
assert "원래부터 있던 항목 Z" in r3, "원래 스코프 밖 항목이 유실됐다"
# 3) 라운드 라벨이 붙어 어느 라운드 산물인지 구분된다
assert "### code-1 에서 반영" in r3 and "### code-2 에서 반영" in r3
# 4) '없음 — 첫 라운드다' 플레이스홀더는 실제 항목이 들어오면 제거된다
assert "없음 — 첫 라운드다" not in r3, "플레이스홀더가 남았다"
# 5) 사람이 갱신해야 하는 두 절에 TODO 가 붙는다
assert r3.count("TODO(resume)") == 2, f"TODO 개수 {r3.count('TODO(resume)')}"
# 6) 보류에 실제 항목이 있으면 감지한다
assert pending(DEC1) == "", "빈 보류를 항목으로 오인"
assert "결정 필요" in pending("## 보류 — x\n- 결정 필요 항목\n"), "보류 항목 미검출"
# 수평선은 항목이 아니다 — `---` 로 요약표를 구분하는 문서 관례와 게이트가 충돌했다
assert pending("## 보류 — x\n\n---\n\n| a | b |\n|---|---|\n") == "", "수평선을 보류 항목으로 오인"
assert pending("## 보류 — x\n***\n") == "", "`***` 를 보류 항목으로 오인"
assert "여전히" in pending("## 보류 — x\n  * 여전히 결정 필요\n"), "들여쓴 불릿 미검출"

# 7) 라운드마다 갱신되는 절이 중복되면 마지막 것만 남는다 (철 지난 기재가 리뷰어를 헷갈리게 함)
DUP = """## 리뷰 대상

**계획 게이트** — 구현은 아직 0% 다. 이건 철 지난 기재.

### 코드 게이트로 이어받을 때 — 이 절만 바꾼다

## 리뷰 대상

git diff master..HEAD 가 진짜 리뷰 대상이다.

## 이미 반영된 지적 (재제기 금지)

없음

## 검증 상태

tsc 통과
"""
d = carry_forward(DUP, DEC1, "plan-1")
assert d.count("## 리뷰 대상") == 1, f"중복 절이 남았다: {d.count('## 리뷰 대상')}개"
assert "git diff master..HEAD 가 진짜" in d, "마지막(실제) 절이 유실됐다"
assert "구현은 아직 0%" not in d, "철 지난 기재가 남았다"
assert d.count("TODO(resume)") == 2, f"TODO 개수 {d.count('TODO(resume)')}"

# 8) 반영 블록이 KEEP_FULL(2)개를 넘으면 오래된 블록은 1줄 인덱스로 접힌다 (diet)
DEC3 = DEC2.replace("code-2", "code-3").replace("대비 미달", "포커스 트랩 누락") \
           .replace("TICKET-222", "TICKET-333")
r4 = carry_forward(r3, DEC3, "code-3")
assert "### code-1 에서 반영 (요약" in r4, "오래된 블록이 접히지 않았다"
assert "버튼 A 가 죽어 있었다" in r4, "접힌 항목의 식별자 첫 줄이 유실됐다 — 재제기 방지가 깨진다"
assert "원인은 서버컴포넌트 캐시" not in r4, "접힌 상세가 남아 있다 (decisions.md 에만 있어야 함)"
# 최근 2개(code-2, code-3)는 전문 유지
assert "### code-2 에서 반영\n" in r4 and "대비 미달" in r4, "최근 블록이 접혔다"
assert "포커스 트랩 누락" in r4
# 후속 티켓·스코프 밖은 diet 대상이 아니다
assert "TICKET-111" in r4 and "TICKET-333" in r4

# 9) 한 라운드 더 가도 접힌 블록이 이중으로 접히지 않는다 (멱등)
DEC4 = DEC2.replace("code-2", "code-4").replace("대비 미달", "빈 목록 오안내") \
           .replace("TICKET-222", "TICKET-444")
r5 = carry_forward(r4, DEC4, "code-4")
assert r5.count("### code-1 에서 반영 (요약") == 1, "요약 마커가 중복됐다"
assert "### code-2 에서 반영 (요약" in r5, "code-2 가 오래된 블록이 됐는데 접히지 않았다"
assert "버튼 A 가 죽어 있었다" in r5 and "대비 미달" in r5, "접힌 식별자가 유실됐다"

# --- resume 게이트: coverage=partial 인 GO 는 다음 라운드로 넘기지 않는다 ---
# unparsed(PARSE-FAIL) 만으로 partial 이 된 라운드도 skipped 와 똑같이 막혀야 한다.
import json, os, subprocess, tempfile

with tempfile.TemporaryDirectory() as home:
    # resume 는 cwd 의 git remote·브랜치로 runs/<repo>/<branch> 를 찾는다 — 그 조건을 만들어 준다
    repo = pathlib.Path(home) / "wt"
    repo.mkdir()
    for cmd in (["init", "-q", "-b", "branch"],
                ["remote", "add", "origin", "https://example.com/org/repo.git"],
                ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                 "--allow-empty", "-m", "seed"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rd = pathlib.Path(home) / "runs" / "repo" / "branch" / "code-1"
    rd.mkdir(parents=True)
    (rd / "context.md").write_text(CTX)
    (rd / "decisions.md").write_text("## 반영\n\n- 없음\n\n## 보류\n\n")
    (rd / "round.json").write_text(json.dumps({
        "gate": "code", "verdict": "GO", "coverage": "partial",
        "skipped": [], "unparsed": ["b3-visibility"],
        "reviewers": {"a-code": "GO", "b1-state-matrix": "GO", "b2-interaction": "GO",
                      "b4-null-propagation": "GO", "b3-visibility": "PARSE-FAIL"},
        "findings": [], "counts": {"regression_P1": 0, "regression_P2": 0, "non_regression": 0},
        "repo_cwd": str(repo),
    }, ensure_ascii=False))

    env = dict(os.environ, RED_TEAM_HOME=home)
    r = subprocess.run([sys.executable, str(pathlib.Path(__file__).parent / "resume.py")],
                       cwd=repo, env=env, capture_output=True, text=True)
    out = r.stdout
    assert "게이트 통과가 아니다" in out, out
    assert "b3-visibility" in out, out
    assert "PARSE-FAIL" in out, "원인이 유예인지 파싱 실패인지 구분해서 알려야 한다\n" + out
    # 막혔으므로 findings 처리·다음 게이트 안내로 넘어가지 않는다
    assert "다음 할 일: 이 라운드의 findings" not in out, out
    assert "--merge-into" in out and "--reviewers b3-visibility" in out, out

print("PASS — 2라운드 누적 유지, 라운드 라벨, TODO 표시, 보류 감지, 중복 절 정리, "
      "오래된 반영 블록 접기(diet)·멱등, PARSE-FAIL partial 차단 모두 정상")
