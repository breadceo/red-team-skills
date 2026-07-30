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

print("PASS — 2라운드 누적 유지, 라운드 라벨, TODO 표시, 보류 감지, 중복 절 정리 모두 정상")
