# red-team 스킬 설계 — 인계 문서

> 작성: 2026-07-29 · 출처 세션: 사내 웹 프론트엔드 저장소 1건 / PR 1건
> **갱신 2026-07-29: 스킬 작성 + eval 2회 완료.** 트랙 B 검증됨 — recall 5/5(2런 재현),
> baseline 0/5, 날조 0. 결과는 `~/.red-team/eval/iteration-{1,2}/REPORT.md`.
> 아래 "다음 작업 — eval 먼저" 절은 이력으로 남기고, 현재 상태는 이 박스와 문서 끝의
> "eval 결과 요약" 을 본다. 남은 작업은 **fix-flow 게이트 연결** 하나다.
>
> ⚠️ **골든 워크트리를 `~/.claude/` 아래에 두지 말 것.** 관리·동기화 대상이라 실행 중 삭제된다.
> iteration 2 첫 실행이 이것 때문에 통째로 무효였고, recall 하락을 프롬프트 탓으로 오진할 뻔했다.
> eval 자산은 전부 `~/.red-team/eval/` 에 둔다 — 스킬 폴더에도, 배포판에도 넣지 않는다
> (골든 워크트리·리포트가 로컬 자산이라 남의 환경에서 재현되지 않는다).

## 왜 만드는가

현재 사용자의 흐름은 이렇다.

```
계획 문서화 → codex red-team 반복(GO까지) → 구현 → codex red-team 반복(GO까지) → 검증 → 커밋/PR
```

문제는 **오케스트레이션 부담이 전부 사용자에게 있다**는 것. 매번 "red-team 리뷰 받아"를 말해야 하고
라운드를 직접 관리한다. 여기에 축을 더하면(아래) 부담이 더 커진다. 그래서 스킬화한다.

기존 관련 지식: `red-team-plan-before-implementation-cheaper`, `redteam-scattered-gaps-need-invariant-redesign`

## 이번 세션에서 드러난 공백 — 트랙 B 가 필요한 이유

그 티켓은 codex red-team 을 **8라운드**(계획 5 + 코드 3) 돌려 GO 를 받았다.
그런데 PR 을 올린 뒤 PR 자동 리뷰 봇이 **6건**을 잡았고 **전부 실제 결함**이었다.

| # | 발견 | 축 | 성격 |
|---|---|---|---|
| 1 | 재시도 버튼이 클라이언트 쿼리를 재조회하지 않음 (`router.refresh()` 는 RSC payload 만 갱신) | 인터랙션 실효성 | 내가 만든 결함 |
| 2 | 어두운 패널(#292929)에서 안내 문구 대비 1.3:1 로 읽을 수 없음 | 가시성 | 내가 만든 결함 |
| 3 | 광고 검증 실패를 `undefined` 로 뭉개 등록 버튼이 안내 없이 disabled (`!undefined === true`) | null 전파 → 기능 차단 | 내가 만든 결함 |
| 4 | 로그인 사용자가 조회 로딩 중 "로그인해주세요"를 봄 (retry backoff 로 구간이 길어짐) | 상태 매트릭스 | pre-existing 이나 내 변경이 악화 |
| 5 | critical 실패인데 optional 타임아웃(20s)까지 대기 | 지연 | **회귀 아님** — 변경 전에도 동일(당시 resolve null 이라 short-circuit 없었음) |
| 6 | 403/404 Sentry 중복 캡처 (인터셉터 + queryFn) | 관측 중복 | 서버 403/404 는 내가 만든 회귀 |

**왜 red-team 이 놓쳤나**: 내 NO-GO 기준이 (1) 새 회귀 (2) 논리구멍 (3) 사실오류 —
전부 *코드 정합성* 축이었다. 빠진 축은 **"사용자에게 무엇이 보이고, 누르면 무엇이 되나"**.
또 프롬프트에서 내가 지목한 지점만 파고들었다(지목한 곳만 본다).

## 설계

### 두 트랙

**트랙 A — 코드 정합성** (현행. 이번 세션 프롬프트 재사용)
NO-GO 기준: 새 회귀 / 대상 결함 못 막는 논리구멍 / 코드 사실오류.
스코프 규칙: '스코프 밖' 명시 항목 지적 금지, pre-existing 은 별도티켓 분류만,
스타일·추상화 취향 배제(ponytail), 이미 반영된 지적 재제기 금지.

**트랙 B — UI 계약** (신규. **미검증**)
4개 축:

1. **상태 매트릭스 전수** — 각 surface × (loading / error / empty / success) × (인증 in/out)
   표를 채우고 **틀린 정보를 보여주는 칸**을 찾는다. (#4 가 여기)
2. **인터랙션 실효성** — 새로 추가한 모든 컨트롤에 대해 "클릭 → 어떤 함수 → 그게 실패 상태를
   해소하는가"를 끝까지 추적. 해소하지 못하면 죽은 컨트롤. (#1 이 여기)
3. **가시성** — 새 UI 가 놓이는 **모든 컨테이너의 배경색**을 찾아 대비 계산. (#2 가 여기)
4. **`undefined`/`null` 전파** — nullable 값이 하위 `!x && y` 같은 논리식에 들어가 기능을
   조용히 막는지. **이전엔 실행조차 안 되던 경로**를 특히 본다. (#3 이 여기)

### 독립 실행 · 병렬

한 프롬프트에 축을 더하면 (a) 컨텍스트가 길어져 각 축이 얕아지고 (b) NO-GO 기준이 섞여
우선순위가 흐려진다. 이번에 라운드마다 P2 가 하나씩만 나온 것도 스코프를 좁게 준 결과.
→ **서로 다른 프롬프트로 동시 실행, 결과를 합쳐 반영.**

### 사용자 결정사항 (2026-07-29 확인)

| 항목 | 결정 |
|---|---|
| 자율성 | **P1 지적은 사용자 확인, P2 는 자동 반영** |
| 트랙 B 적용 | **항상 둘 다** (UI 파일 유무와 무관) |
| 스킬 경계 | **red-team 스킬 신규 + fix-flow 에 게이트 2개 연결** — 단 **eval 이후에** |

`fix-flow` 연결 지점: 4단계(코드 검증) 뒤에 계획 문서화 + red-team(계획) 게이트,
6단계(검증) 앞에 red-team(코드) 게이트. 루프 로직은 red-team 스킬에만 두고
fix-flow 는 호출만 한다(복붙하면 갈라진다).

## 다음 작업 — eval 먼저

### 왜 eval 이 필요한가

**트랙 B 의 4개 축은 자동 리뷰 봇이 찾은 결과를 보고 사후적으로 역산한 것이다.** 표본 1개 오버피팅이고
한 번도 시험되지 않았다. 여기에 "P2 자동 반영"을 얹으면 **검증 안 된 분류기에 쓰기 권한**을 주는 셈.
특히 P1 을 P2 로 낮춰 부르면 검토 없이 자동 반영된다 — 안전 지표다.

### 골든셋 — 라벨된 데이터가 이미 있다

그 저장소의 티켓 브랜치에 각 결함의 **수정 직전 상태**가 커밋으로 남아 있다.
base 는 `3554e550`(작업 시작 전).

| 시나리오 | 리뷰 대상 diff | 찾아야 할 것 | 축 |
|---|---|---|---|
| A | `3554e550..f2b0c037` | #1 재시도 버튼, #2 대비 | 인터랙션 / 가시성 |
| B | `3554e550..3e55f54c` | #3 광고 검증 조용한 disabled | null 전파 |
| C | `3554e550..fe3bf8c6` | #4 로딩 오안내 | 상태 매트릭스 |
| D | `3554e550..a26b36ce` | #6 Sentry 중복 | 관측 중복 |
| E (노이즈) | `3554e550..HEAD` (전부 수정 후) | **없음** | 오탐 측정 |

#5 는 회귀가 아니므로 골든셋에서 제외하되, **"pre-existing 을 회귀로 오판하지 않는가"**를 보는
음성 사례로 쓸 수 있다(트랙 A·B 모두 스코프 규칙 준수 테스트).

### 측정 지표

- **recall** — 알려진 결함을 몇 개 재발견하나
- **대조군** — baseline 은 트랙 A(현행 프롬프트). "트랙 B 가 실제로 새 커버리지를 주는가"를 직접 비교.
  이 5개 시나리오는 **트랙 A 8라운드가 전부 놓친 것**이라 대조가 선명하다.
- **P1/P2 정확도** — 오분류율. 자동 반영 정책의 안전 근거.
- **노이즈** — 시나리오 E 에서 P1 을 몇 개 만들어내나.
- **분산** — LLM 리뷰는 확률적. 같은 시나리오를 2~3회 돌려 재현성 확인.

### 실행 순서

```
1. SKILL.md 초안 (트랙 A/B 프롬프트 + 루프 규칙 + P1/P2 기준)
2. 시나리오 A~E 를 skill-creator test case 로 작성
3. eval 실행 — with-skill vs baseline(트랙 A)
4. recall / P1·P2 정확도 / 노이즈 확인 → 프롬프트 수정 → 재실행
5. 수치 납득되면 description 트리거 최적화 (skill-creator 의 improve_description)
6. 그 다음에 fix-flow 게이트 연결
```

**4번에서 recall 이 낮으면 축 자체를 다시 설계해야 한다.** 그 결과 전에 fix-flow 에 연결하는 것은
검증 안 된 게이트를 워크플로우에 박는 것.

비용 추정: 시나리오 5개 × (with-skill + baseline) = 10회가 1 iteration. 2~3회 반복 시 20~30회.

## 워크스페이스 주의

- 스킬 파일은 `~/.claude/skills/red-team/` — 어떤 repo 밖이므로 PR 오염 없음
- **eval 은 별도 워크트리에서** — 골든 커밋을 리뷰 에이전트가 탐색해야 하는데,
  PR 브랜치가 체크아웃된 워크트리에서 하면 리뷰 중에 상태가 흔들린다
- `git worktree add <path> <sha>` 또는 orca child worktree 사용

## skill-creator 자산

`~/.claude/plugins/cache/claude-plugins-official/skill-creator/unknown/skills/skill-creator/`

- `SKILL.md` — Test Cases(141), Running and evaluating(163), Improving(292), Description Optimization(333) 절
- `scripts/run_eval.py` — 트리거 eval
- `scripts/aggregate_benchmark.py` — 분산 포함 벤치마크
- `scripts/improve_description.py` — description 최적화 루프
- `agents/grader.md`, `agents/comparator.md` — 채점·비교 에이전트
- `eval-viewer/` — 결과 뷰어

## 참고 — 엔진 호출 방식 (설계 당시 기록)

이 문서를 쓸 때는 codex 가 유일한 엔진이었다. **지금은 엔진이 설정 항목**이고
`run_round.py` 의 `engine_cmd()` 한 곳에서 갈린다(`--set-engine codex|claude`, SKILL.md 0단계).
아래는 그 설정화 이전의 원본 기록이다.

```bash
# 래퍼가 --ask-for-approval never --sandbox danger-full-access 를 이미 붙인다 (중복 지정 금지)
codex exec "$(cat prompt.txt)" < /dev/null > out.txt 2>&1
```

**`< /dev/null` 필수** — 백그라운드에서 stdin 이 TTY 가 아니면 EOF 를 기다리며 무한 대기한다
(이번 세션에서 46분 낭비). 출력 파일 크기가 늘지 않으면 진행 중이 아니라 교착이다.
관련 지식: `codex-exec-background-needs-stdin-devnull`

claude 엔진 쪽 함정은 다르다 — `--bare` 를 붙이면 인증 로딩까지 건너뛰어
`Not logged in · Please run /login` 한 줄만 남는다(로그인은 정상인데도). hook·skill 만
끄려면 `--disable-slash-commands` + `--settings '{"hooks":{}}'` 를 쓴다.
두 엔진의 findings 스키마는 실측상 동일해서 파싱·판정 계층은 손대지 않았다.

## 이 세션의 PR 상태 (참고)

PR #872 · 커밋 11개 · `tsc` 통과 · 53 suites / 313 tests · eslint 에러 0
자동 리뷰 지적 6건 중 5건 수정, 1건(#5 지연)은 후속 티켓으로 분류하고 PR 에 근거 기재.
미결: assignee 미지정(템플릿은 최소 1명 요구), 리뷰 요청은 팀 3개.

---

## eval 결과 요약 (2026-07-29 · iteration 1~2)

전문: `~/.red-team/eval/iteration-1/REPORT.md`, `~/.red-team/eval/iteration-2/REPORT.md`
골든 워크트리: `~/.red-team/eval/golden/{scenario-a,scenario-e,parent-head}`

| 지표 | iteration 1 | iteration 2 (A×2) |
|---|---|---|
| 골든 recall (시나리오 A) | 4/5 | **5/5 · 5/5** |
| baseline(트랙 A 단독) recall | **0/5** | 미실행(확립됨) |
| 날조 findings | 0/18 | **0/32** |
| 취향·스타일 지적 | 0 | 0 |
| 이미 수정된 결함 재제기 (E) | 0/5 | 0/5 · 0/5 |
| N5(음성 사례) 오판 | 0 | 0 |
| P2 정확도 | 1건 중 1오류 | **2건 중 0오류** |

**iteration 2 에서 바꾼 것 3개와 효과**
1. `a-code` 에 관측 중복·유실 축 추가 → iteration 1 이 유일하게 놓친 D6 를 2/2 런에서 회수
2. `_common.md` 에 "변경 전 동작을 실제로 확인" 요구 → claim 이 `"실패하면"` → `"4xx 로 실패하면"` 으로 정확해짐
3. P1/P2 재정의(자동 반영 폐지) → P2 정확도 2/2

**정책 변경 — P2 자동 반영 폐지 (사용자 결정)**
P2 는 32 findings 중 2건(6%)으로 희소하고, iteration 1 의 1건은 severity 오분류였다.
자동화가 아끼는 수고가 거의 없고 잘못 들어가면 조용히 남는다. 둘 다 사람이 보되
P2 는 묶어서 일괄 승인하는 제시 방식만 유지한다.

**축별 안정성** — `b2-interaction` 이 가장 안정(present/absent 양쪽 정확, E 에서 2회 모두 빈 손 GO).
`b3-visibility`·`b4-null-propagation` 안정. `a-code` 가 가장 가변(매 런 다른 지점을 지목).

### `a-code` 축 분리는 하지 않는다 (2026-07-29 판단)

관측 축을 `a-code` 에서 떼어 6번째 리뷰어로 만드는 안을 검토했고 **기각했다. 개선할 지표가 없다.**

- D6(관측 중복) recall — 관측 축 추가 후 **2/2 런**. 더 올릴 곳이 없다
- 날조 — **0/32**. 줄일 곳이 없다
- `a-code` 의 "분산"을 다시 보면 매 런 *서로 다른 실제 결함*을 지목한 것이고 전부 코드로 확인됐다.
  불안정이 아니라 탐색 폭이다

측정 근거 없이 작동하는 프롬프트를 고치면 iteration 2 초반의 실수(측정 전 과교정)를 반복한다.

**재검토 조건 — 둘 중 하나가 실사용에서 관측되면 분리한다.**
1. `a-code` 가 코드 사실이 아닌 findings(날조)를 내기 시작한다
2. 관측 중복·유실 계열 결함을 놓친 라운드가 나온다 (D6 급을 GO 로 통과시킨다)

**부산물** — 골든셋 밖 실제 결함 11건 발견, 부모 워크트리로 인계
(`~/.red-team/eval/HANDOFF-<티켓>.md`). 그중 1건은 부모가 독립적으로 찾아 고친 것과 일치했다.

## fix-flow 연결 — 완료 (2026-07-29)

`fix-flow/SKILL.md` 에 두 단계를 넣었다. 루프 로직·P1/P2 처리·수렴 규칙은 red-team 에만 있고
fix-flow 는 호출만 한다.

- **4-1. 계획 문서화 + red-team 계획 게이트** — 4단계(코드 검증) 뒤, 5단계(구현) 앞
- **6-1. red-team 코드 게이트** — **6단계(검증) 뒤**, 7단계(커밋) 앞

> DESIGN 원안은 코드 게이트를 "6단계 앞"이라고 했으나 **뒤로 옮겼다.** 컨텍스트 파일이
> `검증 상태`(tsc/lint/테스트)를 요구하고, 그게 없으면 리뷰어가 타입 에러를 결함으로 보고하는
> 노이즈가 생긴다. 루프는 `구현 → 검증 → 라운드 → 반영 → 검증 → 라운드 → … → GO → 커밋`.

`pre-existing` findings 는 3단계 Epic 아래 별도 티켓 후보로 보고하고 원 티켓에 `Relates`,
그리고 다음 라운드 컨텍스트의 `스코프 밖` 에 적어 재제기를 막는다.

## 세션 분리 — 가능하다 (SKILL.md "다른 세션에서 이어받기")

라운드의 입력은 `(저장소 경로, context.md)` 뿐이고 둘 다 디스크에 있다. 세션 안에 남는 상태가 없다.
이어받는 절차는 `resume.py` 한 줄이다 — 저장소 위치에서 `runs/<repo>/<branch>/` 를 찾아
최근 라운드·verdict·다음 할 일을 알려주고, `--next <gate>` 가 컨텍스트를 이관한다.

**포인터 파일(`last.json`)은 제거했다.** 초기엔 zax 의 `.zax-*-last.json` 패턴을 따라 뒀는데,
`<repo>/<branch>` 가 이미 키이므로 정보가 전부 파생 가능해 중복이었다. 게다가 eval 런이 그 포인터를
덮어 손으로 되돌려야 했고, 그걸 막으려 `--record-last` 플래그를 덧대는 중이었다 —
전역 가변 포인터가 없으면 애초에 없는 문제였다. 파일 하나와 플래그 하나와 실패 모드 하나를 같이 지웠다.

**유일한 비자동 전달분: 변경의 의도.** `context.md` 의 `이 변경이 하려는 것`·`스코프 밖` 은
계획한 쪽만 알고, 리뷰어는 이걸로 논리구멍·스코프 위반을 판정한다.
**첫 컨텍스트를 쓰는 것이 세션 간 인계 그 자체**다.

## description 트리거 최적화 — 이 환경에서는 측정 불가 (2026-07-29 시도·중단)

`skill-creator` 의 `scripts/run_loop.py` 로 시도했고 **측정 경로가 막혀 있어 중단했다.**
description 은 원본 유지. 같은 경로를 다시 시도하지 말 것.

- 증상: iteration 1 이 `precision=100% recall=0% accuracy=50%`.
  should-trigger 12건 전부 `rate=0/3`. 이 조합은 "한 번도 트리거되지 않았다"의 지표다
- `claude -p` 자체는 정상 동작한다(stream-json 정상 출력) — subprocess 문제가 아니다
- **직접 측정**: `"red-team 코드 게이트 돌려줘"`(스킬명을 그대로 부른 질의)를 `claude -p` 에 넣으면
  tool_use 14건 중 `Skill` 호출이 **0건**. 대신 `hipocampus.config.json`·`.compaction-state.json`
  읽기와 git 탐색을 한다
- **원인**: `~/.claude/CLAUDE.md` 의 hipocampus FIRST RESPONSE RULE — "모든 세션의 첫 메시지에서
  다른 무엇보다 먼저 Session Start 프로토콜을 전부 수행, 사용자 요청보다 우선". one-shot
  `claude -p` 는 턴이 하나라 그 프로토콜이 턴을 다 먹고 스킬에 도달하지 못한다.
  타임아웃을 늘려도 안 된다 — 프로세스 종료까지 갔는데 그 안에 `Skill` 이 없었다

즉 `claude -p` 기반 트리거 eval 은 이 환경에서 **구조적으로 무효**다. description 품질과 무관하다.
이걸 모르고 진행하면 멀쩡한 description 을 깨진 신호에 맞춰 고치게 된다(iteration 2 무효 런과 같은 실수).

부작용 정리: 하네스가 `~/.claude/commands/red-team-skill-<hash>.md` 를 남긴다(세션 스킬 목록 오염).
중단 후 삭제했다. 재시도 시 이 정리를 잊지 말 것.

**다시 하려면** 셋 중 하나가 필요하다: (a) 실사용상 필요가 생길 때까지 생략 — **현재 선택**.
사용자는 스킬을 직접 부르거나 `fix-flow` 경유로 쓰고 있어 트리거 자동성의 실질 가치가 낮다.
(b) 심판 방식 프록시 — 경합 스킬 목록과 질의를 주고 "어느 스킬을 부를까"를 분류하게 한다
(변별력은 재지만 실제 트리거와는 다른 구성개념). (c) eval 전용 HOME 으로 hipocampus 룰을 우회
(가장 정확하나 사용자 설정·인증 복사가 필요).

`~/.red-team/eval/trigger-eval.json` 에 질의 20건(경합 스킬별 near-miss 포함)은 남겨뒀다 —
재시도 시 재사용 가능.

## 검증된 것 — 계획 게이트 실전 1회

부모에게 인계할 "불변식 2개" 제안을 계획 게이트에 넣어 3건을 받았다
(`~/.red-team/runs/<repo>/<branch>/plan-1/`). 전부 실제 결함이었고 **2건이 내 분석을 정정했다**:
`UserPopupController` 오판(전역 본인인증 모달), "4xx 도달 불가능" 전제 오류(서버 경로에서
`redirect()` 의 `NEXT_REDIRECT` 가 base 의 `catch → null` 에 삼켜진다).
계획 게이트가 구현 전에 값을 낸다는 것이 실측으로 확인됐다.
