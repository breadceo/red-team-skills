# 오케스트레이터와 병행 — `zax` 워크플로우의 경우

> SKILL.md 의 「참고 문서」에서 참조된다. **zax(`/task`·`/workflow`) 흐름 안에서 red-team 을
> 게이트로 쓸 때만 읽는다** — zax 를 쓰지 않으면 이 문서는 필요 없다.

이 스킬은 **오케스트레이션을 하지 않는다.** 분해·의존성·진행 추적은 `zax:task` 가 하고,
이 스킬은 그 흐름의 **두 지점에 끼는 게이트**다. 계획을 세우거나 구현하는 것도 아니다.

**게이트를 두 곳으로 제한하는 이유는 zax 에 이미 검증이 있기 때문이다.** 겹치는 자리에
넣으면 축이 안 맞아 `out-of-scope` findings 만 쏟아진다 — 그건 컨텍스트가 새는 신호라
컨텍스트를 계속 손보게 된다.

| zax 단계 | 이미 하는 검증 | 성격 |
|---|---|---|
| `/workflow prd` | PRD readiness gate | 형식·완결성 |
| `spec-crew` Crew E | SSOT 문서 ↔ PRD 트리 교차 검증 | 문서 간 정합 |
| `/workflow validate` | 추적성·커버리지(PRD↔Spec↔Feature), Gherkin 문법 | 추적성 |
| `/workflow pr` | 역할별(PO/FE/BE/QA) 섹션 리뷰 | 사람 리뷰 |
| `/task done` Step 2 | tsc·lint·test | 기계 검증 |

이 다섯이 못 보는 것이 하나 남는다 — **"이 계획대로 짜면 사용자에게 무엇이 보이고, 누르면
무엇이 되나".** 추적성은 AC 가 어딘가 매핑됐는지만 보고 기계 검증은 타입과 테스트만 본다.
`b1`~`b4` 축이 그 공백이고, 그래서 게이트는 아래 두 곳뿐이다.

```
/task plan  →  PLAN.md
                 ↓ ① --gate plan     NO-GO → /task plan 재분석
/task start →  CONTEXT.md · 워크트리/브랜치
/task run   →  구현
/task done  →  Step 2 tsc·lint·test 통과
                 ↓ ② --gate code     NO-GO → done 처리하지 않는다
               완료 처리
```

컨텍스트는 손으로 쓰지 않고 zax 산출물에서 초안을 뽑는다:

```bash
python3 ${CLAUDE_PLUGIN_ROOT:-~/.claude/skills}/red-team/scripts/run_round.py \
  --cwd <저장소 경로> --gate plan --from-zax <task-name>
```

`~/.zb-task/<task-name>/redteam-context.md` 가 없으면 **초안만 만들고 멈춘다.** 실리는 것은:

| red-team 절 | 어디서 오나 |
|---|---|
| `## 이 변경이 하려는 것` | `PLAN.md` 작업 분석 + `CONTEXT.md` PRD 요약·Architecture 합의 |
| `## 판정 기준 (Spec AC · Gherkin)` | `CONTEXT.md` Spec AC 매핑 · Gherkin 시나리오 (있을 때만) |
| `## 스코프 밖` | 비워둔다 + `PLAN.md` 의 `미확인` 항목을 "리뷰어가 볼 지점"으로 첨부 |
| `## 검증 상태` | 비워둔다 — `/task done` Step 2 결과를 붙인다 |
| `## 계획 전문` | `PLAN.md` 전문 (계획 게이트만) |

`## 스코프 밖` 과 `## 검증 상태` 를 채운 뒤 같은 명령을 다시 실행한다 —
**이미 있으면 덮지 않는다**(위 표의 두 번째 행이 그 이유다).

컨텍스트를 task 디렉토리에 두는 것이 핵심이다. `PLAN.md` 와 같은 디렉토리에 있어야
신선도 경고(SKILL.md 「구현 중 계획이 바뀌면」 절)가 작동한다 — `/task run` 중에 계획이
바뀌면 그때 알려준다.

## NO-GO 가 어디로 올라가는지가 두 도구의 경계다

무엇을 잡았느냐에 따라 되돌아갈 지점이 다르다. 안 가르면 계획만 계속 고치고 산출물에는
drift 가 남는다.

| 잡힌 것 | 되돌아갈 곳 |
|---|---|
| 계획의 논리구멍·순서 오류 | `/task plan` 재분석 |
| "이 계획이 Spec AC 와 다르다" | **`/workflow spec`** — 산출물 역반영. task 에서 고치면 Spec drift 가 남는다 |
| 계약(FE/BE)·정책·범위 변경 | `/workflow` sub-PR (`fix/{KEY}-{topic}`) — 리뷰어가 오너인 결정이다 |
| `pre-existing` | 반영하지 않는다. 후속 티켓 후보로 보고 → 다음 컨텍스트의 `스코프 밖` |

**3라운드를 넘겨 반복이 계속되면 반복의 종류를 가른다.** findings 가 Spec AC 와 코드의
어긋남을 가리키는 **Spec 불일치 증거가 있는 반복**이면 계획이 아니라 Spec 이 틀렸다는
신호다 — 표면을 하나씩 막지 말고 `/workflow spec` 으로 올라간다. 반면 **same-origin P1
반복이거나 생존성 7·8행(수명·통증 위치)이 흔들리는 반복**이면 Spec 수정이 아니라 루프 절의
세 번째 escape 다 — 어댑터 격리로 계획을 바꾸거나, 전제를 재심해 사용자에게 중단(ABORTED)/
피벗을 올린다.

## 하지 말 것

- **PRD·Architecture·Spec 단계에 계획 게이트를 걸지 않는다.** `a-plan` 은 *구현 계획* 리뷰어다.
  예외는 Architecture 가 **계약 전환**(대상 레포 변경, FE/BE 책임 이동)을 담을 때 한 번 —
  그건 sub-PR 리뷰 대상이라 값이 있다.
- **`/task run` 서브태스크마다 돌리지 않는다.** 라운드 단위는 브랜치 상태다. 여러 서브태스크가
  한 브랜치에 쌓이므로 수합 시점이나 `/task done` 에서 한 번이다. 레포가 여러 개면(워크트리
  여러 개) 레포별로 따로 돈다 — diff 가 레포별이라 그게 맞다.
- **`--skip-check` 로 기계 검증을 건너뛴 상태에서 코드 게이트를 돌리지 않는다.** `## 검증 상태` 가
  비면 리뷰어 5명이 동시에 "테스트 없음"을 지적하느라 턴을 쓴다.
- **`/task pr` 이후에 걸지 않는다.** 그 시점부터는 `pr-triage` 담당이고, 코드 게이트는
  `pr-triage` 가 이미 호출한다.
