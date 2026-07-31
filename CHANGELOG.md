# 변경 내역

기준선은 이 저장소의 첫 커밋(`4e6382c`, 2026-07-30)이다. 그 이전에 사내 스킬 마켓플레이스
번들로 설치했다면 아래 **업데이트 방법**을 먼저 읽는다 — `update` 로는 새 버전이 오지 않는다.

## 업데이트 방법

### 마켓플레이스로 설치했던 경우 — 한 번은 `add` 로 다시 받아야 한다

```bash
zarketplace add red-team          # pr-triage 도 같이 설치된다 (같은 저장소)
```

`zarketplace update` 를 쓰면 **구버전이 조용히 온다.** 이유는 이렇다 — `update` 는 로컬
manifest 의 항목 유형으로 출처를 판단하는데(`update.js:69`), 번들로 설치한 항목에는
`owner`/`repo` 가 없어서 git 이 아니라 서버 번들 경로로 간다. 그 번들은 저장소 이전 시점의
스냅샷이라 이 문서의 변경이 하나도 들어 있지 않다. 에러도 경고도 없다.

`add` 로 한 번 다시 받으면 manifest 가 git 출처(`type: github`)로 기록되므로,
**그 다음부터는 `zarketplace update` 가 저장소를 따라간다.**

### git 으로 설치한 경우

```bash
cd <클론 경로> && git pull
```

symlink 로 걸어 두었으면 그것으로 끝이다. `--copy` 로 설치했다면 `zarketplace update` 를
한 번 더 돌린다.

### 설치 후 확인

```bash
python3 <설치 경로>/red-team/scripts/run_round.py --help | grep from-zax
```

`--from-zax` 가 보이면 최신이다. 안 보이면 아직 구버전이다.

---

## 2026-07-31

### ✨ red-team — 컨텍스트 diet: 오래된 반영 기록을 1줄 인덱스로 접기

컨텍스트는 리뷰어 5명 × 매 라운드에 실리므로 `이미 반영된 지적` 누적이 곧 토큰
누적이다. `resume.py --next` 의 carry-forward 가 이제 최근 2개 라운드 블록은 전문을
유지하고, 그 이전 블록은 톱레벨 불릿 첫 줄(매칭 가능한 식별자)만 남기고 접는다 —
재제기 억제에 필요한 것은 식별자 한 줄이고, 결정 전문은 각 라운드 decisions.md 에
그대로 있다(기록 삭제가 아니라 가시성 압축). 스코프 밖·판정 기준 절은 건드리지 않는다
— 잘못 접어 재제기가 살아나면 라운드 하나가 추가돼 절감분을 다 되먹는다.

### ✨ red-team — 축별 배정을 config 로 조정 + AskUserQuestion 설정 플로우

코드의 GATES/TIERS 는 **추천 기본값**이 되고, 사용자별 조정은 `config.json` 의
`assignments` 에 얹힌다. 우선순위: CLI `--model/--effort`(전 리뷰어 강제) >
`assignments[축]` > tier/prefer 기본.

- `--show-assignments` — 유효 배정표 + 축 성격(왜 이 tier 인가) + 오버라이드/추천 병기
- `--set-assignment '축=engine/model/effort'` 저장, `'축='` 로 추천 복귀 (반복 지정 가능)
- 가용 목록 밖 엔진을 가리키는 오버라이드는 자동 무시 — 한도 소진 시
  `--set-engine codex` 한 방 전환이 축별 설정에 발목 잡히지 않고, 복귀하면 되살아난다
- SKILL.md 에 사용자 요청("리뷰 배정 바꿔줘", "claude 아껴야 해" 등) 시
  AskUserQuestion 으로 [codex only / claude only / 추천 복귀 / 축별 세부 조정]을
  제시하는 플로우와 한도 소진 플레이북을 문서화 — 한도가 차기 전에 미리 설정하게 한다

### ✨ red-team — 배정별·축별 비용 대비 성과 리포트 (`report_usage.py`)

라운드가 쌓이면 `report_usage.py [repo/브랜치 조각]` 으로 배정(engine/model/effort)별·
축별 실행 수, 토큰, 비용, regression 수, $/regression 표를 본다. 분자는
`classification == regression`(raw findings 는 말 많은 모델을 과대평가한다),
구버전 라운드(assignments 없음)와 토큰 미집계 라운드는 분리 표기된다.
decisions.md 의 반영 수 조인("accepted/$")은 필요해지면 붙인다.

### ✨ red-team — 리뷰어별 토큰 사용량·비용 집계

subscription 기반이라 청구서로는 엔진별 사용량을 알 수 없었는데, 두 CLI 모두 호출별
usage 를 내려준다는 것을 확인하고 라운드에 집계를 붙였다.

- claude 는 `--output-format json` 의 `usage`/`total_cost_usd`, codex 는 acpx
  `--format json`(ACP 스트림)의 `result.usage` 에서 토큰을 꺼낸다 (`parse_output`)
- `round.json` 의 `assignments` 에 리뷰어별 `tokens: {input, output, total, cost_usd}` 가
  남고, 라운드 끝에 합계가 출력된다. 비용은 API 환산가 — claude 는 CLI 값,
  codex 는 로컬 단가표(`CODEX_PRICES`, cached input 10%)로 계산한다
- 래핑 파싱이 실패하면(CLI 출력 형식 변경 등) 원문 폴백으로 라운드는 그대로 돌고
  토큰 집계만 빠진다 — 집계 기능이 리뷰를 죽이는 경로를 만들지 않는다
- raw 로그(`<리뷰어>.txt`)는 이제 JSON 래핑/스트림 형태다 — 사람이 읽을 리뷰 본문이
  필요하면 `.prompt.md` 와 `.json`(findings)을 본다

### ✨ red-team — 리뷰어 축별 모델·effort·엔진 차별화

지금까지 라운드의 리뷰어 5명은 전원 같은 엔진·같은 모델·같은 effort(예: codex 면
gpt-5.6-terra / medium)로 돌았다. 축 성격은 서로 다른데 스펙이 같으니, 체크리스트형
축(상태 매트릭스·대비 계산)에는 과했고 복합 추론 축(회귀·인터랙션 추적)에는 부족했다 —
특히 CodeRabbit 리뷰 벤치마크 기준 Terra 는 recall 52.5% 로 절제형이라, 결함을 **찾는**
스킬의 deep 축에 쓰기엔 목적과 반대 성향이었다(Sol 은 69.7%).

바뀐 것:

- `GATES` 가 축별 spec(`tier`, `prefer`)을 갖는다. tier(deep/mid/cheap)가 엔진별
  모델·reasoning effort 를 정한다 — codex 는 sol-high / terra-high / luna-medium,
  claude 는 opus-high / sonnet-medium (haiku 는 쓰지 않는다).
- `--set-engine codex,claude` 로 **가용 엔진을 복수 저장**할 수 있다. 두 엔진이 모두
  가용하면 deep 축이 codex/claude 로 갈라져 같은 모델의 맹점이 전 축에 복제되는 것을
  막는다. 한 엔진만 저장하면 기존처럼 전 리뷰어가 통일된다(prefer 는 폴백).
- codex 의 effort 는 `CODEX_CONFIG` env(codex-acp 어댑터가 세션 config 에 병합)로,
  claude 는 `--effort` 플래그로 전달한다. `--model`/`--effort` CLI 는 전 리뷰어 강제
  override 로 남는다.
- `round.json` 에 `assignments`(리뷰어별 engine/model/effort/tier)가 추가됐다.
  `reviewers`(verdict 문자열)와 `engine` 필드 형태는 유지되므로 기존 소비자
  (summarize_round.py, resume.py, pr-triage)는 그대로 동작한다.
- 혼합 라운드에서 한 엔진 소속 리뷰어가 전원 PARSE-FAIL 이면 경고한다 — 남은 엔진의
  GO 에 묻혀 조용히 통과하는 것을 막는다(7/30 `Not logged in` 사고의 재발 경로).

- 최초 설정(0단계)은 AskUserQuestion 멀티셀렉트로 묻는다 — codex/claude 를 체크박스로
  고르고, 고른 것이 그대로 `--set-engine` 콤마 목록이 된다. 구버전 config
  (단수 `engine` 키만 있음)로 올라온 경우에도 한 번 다시 물어 분산 여부를 고르게 한다.

마이그레이션: 없음. 기존 `config.json` 의 단수 `engine` 키를 그대로 읽고(라운드는
그대로 돈다), 다음 스킬 진입 시 0단계가 엔진 구성을 한 번 다시 확인한다.

---

## 2026-07-30

첫 커밋 이후의 변경 3건. 리뷰 엔진 설정(`~/.red-team/config.json`)과 누적 라운드
(`~/.red-team/runs/`)는 그대로 쓰이므로 재설정할 것이 없다.

### 🐛 pr-triage — 감시기가 트리아지 커서를 되돌리던 버그 (`1c93e47`)

**데이터 유실 버그다. pr-triage 를 쓰고 있으면 이 항목 때문에라도 업데이트한다.**

`watch_comments.py` 는 시작할 때 상태 파일을 한 번 읽고, 신규 코멘트가 올라올 때마다 그
시작 시점 스냅샷을 되쓰고 있었다. 감시는 몇 시간씩 살아 있고 그동안
`fetch_comments.py --mark-triaged` 가 같은 파일에 `triaged` 를 쓰므로, **감시가 시작된 뒤에
처리한 표시가 전부 사라졌다** — 실측된 한 PR 에서는 감시 시작 시점의 16건만 남았다.

`--mark-triaged` 는 잘못이 없었다. 제대로 쓰고 성공을 보고했고, 그 뒤에 감시기가 덮었다.
그래서 증상이 "왜 처리한 코멘트가 다시 올라오지" 로만 보였다.

수정: 저장 직전에 디스크를 다시 읽고 `notified` 를 합집합으로 병합한다. 감시기는 그 목록에
추가만 하므로 병합으로 충분하고 락은 필요 없다.

> 이미 되돌아간 상태 파일은 자동 복구되지 않는다. `~/.red-team/runs/<repo>/<branch>/pr-<N>-triage.json`
> 의 `triaged` 가 실제 처리분보다 적으면, 해당 PR 에서 `fetch_comments.py --new-only` 로 미처리
> 목록을 다시 뽑아 확인한다(이미 답한 코멘트는 `→` 표시가 없다).

### ✨ red-team — `zax:task` 와 병행하기 위한 `--from-zax` (`3e8fb2f`)

```bash
run_round.py --cwd <저장소 경로> --gate plan --from-zax <task-name>
```

`~/.zb-task/<task>/PLAN.md` 와 `CONTEXT.md` 를 읽어 리뷰 컨텍스트 초안을
`~/.zb-task/<task>/redteam-context.md` 에 만든다. **초안을 만들면 그 자리에서 멈춘다** —
`## 스코프 밖` 과 `## 검증 상태` 를 채운 뒤 같은 명령을 다시 실행하면 라운드가 돈다.
파일이 이미 있으면 **덮지 않는다**(판정 기준은 사람이 정하는 문서다).

기존 `--context` 방식은 그대로 동작한다. 두 인자를 함께 주면 에러다.

### 🐛 red-team — 계획 신선도 경고가 `PLAN.md` 를 놓치던 버그 (`3e8fb2f`)

라운드 시작 시점에 "계획서가 컨텍스트보다 새롭다"를 알리는 경고가 `plan*.md` 패턴을 쓰고
있었다. **Python glob 은 파일시스템과 무관하게 대소문자를 구분**하므로 `PLAN.md` 는 한 번도
걸리지 않았다 — 그 이름을 쓰는 배치에서는 경고가 처음부터 죽어 있었다. 대소문자 무시로 바꿨다.

손으로 `plan-1.md` 같은 이름을 쓰고 있었다면 이전에도 정상 동작했다. 동작 변화 없음.

### ✨ red-team — 초안에 Spec AC · Gherkin 을 싣는다 (`a410247`)

`CONTEXT.md` 의 `## Spec AC 매핑` 과 `## Gherkin 시나리오` 를 `## 판정 기준` 절로 옮겨
싣는다. 위치는 "이 변경이 하려는 것" 뒤, "스코프 밖" 앞 — 리뷰어가 이걸 **기준으로** 읽어야
하기 때문이다. 이전에는 리뷰어가 수용 기준을 코드에서 역추론했다. 둘 다 없는 태스크면 절
자체를 만들지 않는다(빈 제목만 남기면 리뷰어가 그 공백을 추측으로 채운다).

### 📄 문서 — zax 워크플로우에서 게이트를 어디에 두는가 (`a410247`)

`red-team/SKILL.md` 에 절이 하나 늘었다. 요지는 **게이트가 두 곳뿐인 이유**다 —
`/workflow prd`(형식) · `spec-crew` Crew E(문서 정합) · `/workflow validate`(추적성) ·
`/workflow pr`(역할 리뷰) · `/task done`(tsc·lint·test)이 이미 있고, 이 다섯이 못 보는 축이
하나 남는다. 함께 실린 것: NO-GO 라우팅(어떤 findings 가 `/workflow spec` 으로 올라가야
하는지), 하지 말 것 4개.

### 🧪 테스트

`red-team/scripts/test_from_zax.py` 추가. 프레임워크 없이 `python3 test_from_zax.py` 로 돈다.
