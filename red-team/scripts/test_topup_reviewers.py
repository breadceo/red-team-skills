"""partial GO 의 top-up 명령에 core 축 재샘플을 넣는 조건 — core 가 codex 로 돌았을 때만."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from run_round import topup_reviewers

CODEX = {"a-code": {"engine": "codex"}}
CLAUDE = {"a-code": {"engine": "claude"}}
assert topup_reviewers("code", ["b1-state-matrix", "b3-visibility"], CODEX) == ["b1-state-matrix", "b3-visibility", "a-code"]
assert topup_reviewers("code", ["b1-state-matrix"], CLAUDE) == ["b1-state-matrix"]  # claude: prompt 캐시 안 됨 → 종전대로 뒤에 따로
assert topup_reviewers("code", ["b1-state-matrix"], None) == ["b1-state-matrix"]    # 엔진 모름 → 보수적으로 제외
assert topup_reviewers("code", ["a-code", "b2-interaction"], CODEX) == ["a-code", "b2-interaction"]  # core 가 이미 빠진 경우 중복 없음
assert topup_reviewers("plan", ["b5-plan-ordering"], {"a-plan": {"engine": "codex"}}) == ["b5-plan-ordering", "a-plan"]
print("ok")
