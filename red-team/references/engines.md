# 리뷰 엔진 — 최초 설정·배정표·조정·한도 플레이북

> SKILL.md 의 「참고 문서」에서 참조된다. **엔진을 처음 설정할 때(0단계), 사용자가 배정
> 조정·엔진 전환·한도를 언급할 때, 라운드의 배정·토큰 기록을 해석할 때 읽는다.**

## 최초 설정 (0단계) — 묻지 않고 기본값을 정하지 않는다

`~/.red-team/config.json` 이 없거나 `engines` 키가 없으면(구버전 단수 `engine` 만 있는
경우 포함) **먼저 사용자에게 한 번 묻고 저장한다.** 어떤 엔진이 깔려 있는지는 환경마다 다르다.

질문은 현재 호스트에서 아래 순서로 한 분기만 고른다.

- Claude Code에서 `AskUserQuestion`이 있으면 `multiSelect: true`로 묻는다.
- Codex에서 `request_user_input`이 노출되어 있으면 사용한다. 없으면 텍스트 질문 하나를 보내고 기다린다.
- 그 밖의 headless 환경은 텍스트로 같은 내용을 묻고 기다린다.

선택지는 두 엔진이고, 사용자가 고른 것을 콤마로 이어 `--set-engine`에 넘긴다.

- 질문: "red-team 리뷰 엔진으로 무엇을 쓸까요? (복수 선택 시 리뷰어가 축별로 분산됩니다)"
- 선택지 `codex` — `acpx` + OpenAI codex CLI 필요. 지금까지 모든 실측이 이 엔진으로 나왔다
- 선택지 `claude` — 로그인된 `claude` CLI 필요. 별도 CLI 설치 없이 돈다
- 둘 다 선택 (권장, 둘 다 설치된 환경) → `--set-engine codex,claude`

묻기 전에 어느 CLI 가 실제로 있는지 확인해 없는 엔진은 선택지 설명에 표시한다
(`which acpx`, `which claude`).

claude 엔진의 준비 확인은 한 줄이면 된다:

```bash
claude -p "reply with exactly: ok"    # 'ok' 가 나오면 준비됨
```

**`--bare` 를 붙이지 않는다** — 인증까지 끊긴다(실측: `evidence.md`). hook·skill 만 끄는
수단은 `--disable-slash-commands` + `--settings '{"hooks":{}}'` 이고, 리뷰어는 이 상태로
돈다 — 리뷰어 세션에 다른 스킬의 세션 시작 프로토콜이 끼면 턴을 그쪽에 다 쓰고 리뷰가 밀린다.

`run_round.py`는 stdin을 `DEVNULL`로 고정한다. codex를 직접 부르면 반드시
`< /dev/null`을 붙인다. 없으면 EOF 대기로 멈춘다.

## 축별 모델·effort 배정

**main agent 모델과 이 표의 reviewer 모델은 별개다.** main agent는 Claude Code/Codex 세션의
설정·프로필을 그대로 쓰고, `~/.red-team/config.json`의 `assignments`는 `run_round.py`가
만드는 reviewer subprocess에만 적용된다. Codex의 native subagent 설정으로 이 표를 복제하지
않는다. 각 subprocess가 아래 model/effort를 명시적으로 받아 두 설정이 섞이지 않는다.

리뷰어는 전원 같은 스펙으로 돌지 않는다. 축 성격이 tier 를 정하고, tier 가 엔진별
모델·reasoning effort 를 정한다 (`run_round.py` 의 `GATES`/`TIERS`):

| 축 | tier | codex | claude | 성격 |
|---|---|---|---|---|
| `a-code` | deep | gpt-5.6-sol / high | opus / high | 회귀·논리구멍 — 복합 추론 |
| `b2-interaction` | deep | gpt-5.6-sol / high | opus / high | 핸들러→데이터소스 다단계 추적 |
| `b4-null-propagation` | mid | gpt-5.6-terra / high | sonnet / medium | 미묘하지만 범위가 좁다 |
| `b1-state-matrix` | cheap | gpt-5.6-luna / medium | sonnet / medium | 표 채우기 전수, 체크리스트형 |
| `b3-visibility` | cheap | gpt-5.6-luna / medium | sonnet / medium | 색·대비 계산, 기계적 |
| `a-plan` | deep | gpt-5.6-sol / high | opus / high | 계획 결함은 여기서 잡는 게 가장 싸다 |
| `b5-plan-ordering` | mid | gpt-5.6-terra / high | sonnet / medium | 계획의 순서·경합·외부 신호 보장 범위 |

deep 축에 recall 우선 모델을 두는 근거와 두 엔진 분산의 의도는 `evidence.md`(축별 모델
배정의 근거)에 있다. `--model`/`--effort` 는 전 리뷰어 강제 override 다(엔진별 비교 실측용).

## 엔진 설정 명령

```bash
python3 "<red-team-skill>/scripts/run_round.py" --set-engine codex          # 한 엔진만
python3 "<red-team-skill>/scripts/run_round.py" --set-engine codex,claude   # 둘 다 (권장)
```

**콤마로 여러 엔진을 저장하면 리뷰어가 축별로 분산된다** — 첫 항목이 기본(폴백)이다.
**이후 바꿀 때도 같은 명령이다.** 이번 라운드만 바꾸려면 `--engine <x[,y]>`,
CI·eval 처럼 프로세스 단위로 고정하려면 `RED_TEAM_ENGINE=<x[,y]>` 를 쓴다
(우선순위: `--engine` > 환경변수 > `config.json`). 단일 엔진을 주면 전 리뷰어가 통일된다.

Codex 리뷰어만 별도 홈(인증·MCP 설정)을 쓰려면 `~/.red-team/config.json` 에
`"codex_home": "/절대/경로"`를 둔다. 이 키가 없으면 red-team은 `CODEX_HOME`을 넘기지 않아
Codex 기본 `~/.codex`를 쓴다. 대상 디렉터리는 미리 만들고 그 홈으로 `codex login`을 한 번 실행한다.

## 배정·토큰 기록의 해석

어떤 배정으로 돌았는지는 `round.json` 의 `engine`(요약)과 `assignments`(리뷰어별
engine/model/effort/tier + **tokens**)에 남는다 — 라운드를 재현하거나 엔진별 결과 차이를
볼 때 본다. `tokens` 는 리뷰어별 `{input, output, total, cost_usd}` 다. subscription 이라
실청구는 아니고 **API 환산가**다 — claude 는 CLI 가 주는 값, codex 는 로컬 단가표
(`CODEX_PRICES`)로 계산하며 단가표에 없는 모델은 토큰만 남고 비용은 `null` 이다.
CLI 출력 형식이 바뀌어 래핑 파싱이 실패하면 라운드는 그대로 돌고 토큰 집계만 빠진다.

라운드가 몇 번 쌓이면 **배정이 값을 하는지**(regression/달러)를 표로 본다:

```bash
python3 "<red-team-skill>/scripts/report_usage.py" [repo/브랜치 조각]
```

분자는 `classification == regression` 이다 — 이유는 `evidence.md`(축별 모델 배정의 근거).
표는 판단 재료이고 배정 축소는 사람이 결정한다.

## 배정 바꾸기 (사용자가 명시적으로 요청할 때)

기본 배정은 코드의 **추천값**이고, 사용자별 조정은 `config.json` 의 `assignments` 에
얹힌다. 사용자가 "리뷰 배정 바꿔줘", "리뷰 모델 조정", "엔진 전환", "claude 아껴야 해",
"토큰/한도" 같은 요청을 하면 **손으로 config 를 고치지 말고 이 플로우를 탄다**:

1. `run_round.py --show-assignments` 로 현 배정·오버라이드·축 성격(추천 이유)을 확인한다.
2. **위의 호스트별 질문 분기로 묻는다.** 첫 질문은 빠른 선택지:
   - `codex only 전환` — claude 한도 소진/절약. `--set-engine codex` 한 방 (배정표 무손상)
   - `claude only 전환` — 반대 방향. `--set-engine claude`
   - `추천 배정 복귀` — 오버라이드 전부 제거 (`--set-assignment '축='` 반복)
   - `축별 세부 조정` — 다음 단계로
3. 축별 세부 조정이면 축마다 옵션을 주되, **각 옵션에 "이 축은 ~하는 일이라 ~을 추천"
   설명을 붙인다** — 재료는 `--show-assignments` 의 why 와 tier 다. 예: b2-interaction 은
   "다단계 교차 추적이라 opus/high 추천 — 비용 부담이면 sonnet/high (recall 하락 가능,
   report_usage 로 사후 검증)".
4. 고른 것을 `--set-assignment '축=engine/model/effort'` 로 저장하고,
   `--show-assignments` 를 다시 보여줘 확정한다.

## 한도 소진 플레이북

claude(또는 codex) 사용량이 다 떨어졌으면 축별 설정을 건드리지 말고 `--set-engine codex`
(복귀는 `--set-engine codex,claude`). 가용 목록 밖 엔진을 가리키는 축 오버라이드는
**그동안 자동 무시**되므로 전환-복귀가 배정표를 훼손하지 않는다. 사용자가 한도를 언급하면
이 전환을 먼저 제안한다 — 한도가 차기 *전에* 미리 설정해 두겠냐고 묻는 것도 좋다.
혼합 라운드에서 한 엔진 소속 리뷰어가 전원 PARSE-FAIL 이 났을 때의 처리는
`recovery.md`(verdict 의미론)를 본다.
