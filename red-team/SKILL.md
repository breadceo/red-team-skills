---
name: red-team
description: 계획 문서나 구현 diff 에 적대적 리뷰 라운드를 돌려 GO 가 나거나 사람이 중단(ABORTED)을 선언할 때까지 반복한다. 리뷰어를 축별로 병렬 실행하고 결함을 P1(결정 필요)/P2(일괄 승인)로 분류해 사용자에게 올린다. "red-team", "레드팀", "적대적 리뷰", "코덱스 리뷰 돌려줘", "이 계획 검증해줘", "커밋 전에 털어줘", "구현 다 됐으니 리뷰", "GO 날 때까지" 같은 요청에 쓴다. **티켓 키만 언급해도 이어받는다** — "red-team TICKET-123", "TICKET-123 이어서", "지난번 리뷰 이어서 진행" 처럼 부르면 그 티켓의 마지막 라운드를 찾아 다음 할 일을 알려준다. 커밋·PR 직전 게이트로도 쓰고, fix-flow 가 계획·코드 게이트에서 호출한다.
---

# red-team

## 왜 이 스킬이 있나

혼자 쓴 코드를 혼자 검토하면 이미 옳다고 믿는 축에서만 검토한다. 코드 정합성 축 하나로
8라운드 GO 를 받은 변경이 PR 자동 리뷰에서 실제 결함 6건을 맞은 것이 창립 사례다
(`references/evidence.md`) — 그래서 축을 나눠 **독립된 리뷰어를 동시에** 돌린다.
**리뷰어는 지목한 곳만 본다** — 축 프롬프트 전부가 "전수 나열하라"로 시작하는 이유이고,
어겨지면 recall 이 바로 떨어진다.

## 두 개의 게이트

| 게이트 | 언제 | 리뷰어 |
|---|---|---|
| **계획** | 구현 전, 계획 문서가 있을 때 | `a-plan` + `b5-plan-ordering` |
| **코드** | 구현 후, 커밋·PR 전 | `a-code` + `b1-state-matrix` + `b2-interaction` + `b3-visibility` + `b4-null-propagation` |

계획 게이트를 건너뛰지 않는다 — 이미 쓴 코드는 버리기 아까워서 계획이 코드에 맞춰 휘어지고,
계획 단계의 결함이 압도적으로 싸다.

코드 게이트는 **UI 파일이 있든 없든 5축을 다 돌린다** — UI 가 없으면 빈 손 종료가
정상이다. "이번엔 생략" 판단이 놓치는 결함이 정확히 창립 사례의 그것들이다. 비용 절감은
옵트인 MoE(`references/moe.md`)로 한다 — 생략이 아니라 유예라, GO 는 전체 축에서만 확정된다.

## 라운드 실행

### 0. 리뷰 엔진 (최초 1회, 설치 직후)

`~/.red-team/config.json` 에 `engines` 키가 없으면 **`references/engines.md` 의 최초 설정
플로우를 따라 사용자에게 묻고 `--set-engine` 으로 저장한다** — 묻지 않고 기본값을 정하지
않는다. 배정표·배정 조정·한도 플레이북·`codex_home`·토큰 기록 해석도 같은 문서다 —
사용자가 "배정 바꿔줘"·"엔진 전환"·"한도/토큰"을 언급하면 그 플로우를 탄다.

### 1. 컨텍스트 파일을 쓴다

리뷰어 5명이 공유하는 단 하나의 입력이다 — 대충 쓰면 5명이 다 같이 헛돈다.
**`assets/context-template.md` 를 복사해 채운다.** 절별 규칙:

- `## 변경 대상 인벤토리` — **라운드 수를 줄이는 장치.** 목록을 뽑은 **명령을 함께
  적는다** — 리뷰어가 재실행해 전수 주장을 반증할 수 있다. 외부 신호는 **보장하지 않는
  것**까지 적는다. SDK·라이브러리 주장의 근거 우선순위는 템플릿에 있다 — 인용 없는 주장은
  무효다. (실측: `references/evidence.md`)
- `## 스코프 밖` 과 `## 이미 반영된 지적` 이 **수렴 장치**다. 비어 있으면 라운드가 같은
  지적을 되풀이하며 끝나지 않는다. 라운드마다 반드시 갱신한다.
- `## 티켓` 은 선택 절 — 적어두면 게이트 GO 시점에 그 산출물을 최신화한다
  (`references/external-sync.md`). 없으면 그 기능만 조용히 꺼진다. 이관 시 자동 승계된다.

**계획 게이트의 첫 라운드 전에는 `references/plan-coverage.md` 를 읽고 문서 6-구조 +
계획 생존성 clarify 를 수행한다.** `누락` 행은 사용자에게 물어 해소하고, **보류가 남으면
라운드를 돌리지 않는다.** 결과는 context.md 의 `## 판정 기준` 절에 남긴다.

### 2. 돌린다

`<red-team-skill>` = 이 파일의 절대 디렉토리(다른 설치본 검색 금지).

```bash
python3 "<red-team-skill>/scripts/run_round.py" \
  --cwd "<저장소 경로>" --gate code --context "<context.md 경로>"
```

계획 게이트는 `--gate plan`. 코드 게이트는 러너가 `git diff` 를 **한 번** 떠서 리뷰어
전원의 프롬프트에 `## Diff 스냅샷` 으로 첨부한다(`diff.md` 로 보존 — 실측:
`references/evidence.md`). 기본은 작업 트리, **브랜치 diff 면 `--diff-base <base>`** —
`## 리뷰 대상` 과 같은 기준이어야 한다. 200K자 초과면 안내만 한다. context.md 에 diff 를
직접 붙이지 않는다 — 이관되는 사람 문서라 낡은 채 승계된다.

### 산출물은 저장소 밖에 쌓인다

```
~/.red-team/runs2/<owner>__<repo>/<branch키>/<gate>-<n>/
    context.md · <reviewer>.prompt.md · <reviewer>.txt/.json · diff.md
    round.json      ← 병합 결과 + verdict + counts + access_errors (+ reruns)
    decisions.md    ← 처리 결과 (사람이 쓴다)
    <reviewer>.superseded-<stamp>.*  ← 재실행으로 교체된 산출물 (지우지 않는다)
```

**포인터 파일은 없다** — `<owner>__<repo>/<branch키>` 가 곧 키다(키 설계·구 `runs/` 자동
이전·포인터 폐지 이력: `references/evidence.md`). 저장소 안에 쌓으면 `git status` 가
흔들리고 PR 에 섞인다. "이미 반영된 지적"은 **직전 라운드의 `round.json` 을 그대로
읽는다** — 기억에 의존하지 않는다. `--out` 은 eval 용 별도 경로 — `resume.py` 가 찾지
않아 실험이 진행을 오염시키지 않는다.

### 부분 실패가 나면 — 라운드를 버리지 않는다

`PARSE-FAIL`·파일접근오류가 한두 축에만 나면 **`references/recovery.md` 를 읽고 그 축만
`--merge-into` 로 재실행해 원 라운드에 병합한다.** 그때까지 그 라운드는
`coverage: partial` 이라 게이트 통과가 아니다. `round.json` 을 손으로 고치지 않는다.

## findings 처리

`classification` 이 먼저다. **`regression` 이 아닌 것은 GO 를 막지 않는다.**

- `pre-existing` → 반영하지 않는다. **별도 티켓 후보로 요약** 보고. 단 "이 변경이 노출
  빈도·지속 시간을 악화시켰다"는 근거가 있으면 `regression` 이다.
- `out-of-scope` → 스코프 규칙이 새는 신호 — 다음 라운드 컨텍스트를 보강한다.

`regression` 만 반영 대상이고 **둘 다 사용자에게 올린다. 자동으로 코드에 들어가는 것은
없다**(P2 자동 반영 폐지 실측: `references/evidence.md`). `severity` 는 사용자가 무엇에
시간을 쓸지 가른다:

**P2 — 묶어서 일괄 승인.** 각 항목에 `claim`/`fix` 한 줄. 올리기 전에 `fix` 가 정말
유일한지 직접 확인한다 — 하나라도 어긋나면 P1 으로 올린다:
- 고칠 코드가 주변 코드에서 유일하게 결정되는가 (같은 파일이 이미 쓰는 값, 이미 노출된 함수)
- 변경된 파일 안에서 끝나는가
- 사용자에게 보이는 **문구가 그대로인가**
- **변경 전에도 같은 동작이었는가** — 그렇다면 regression 이 아니라 pre-existing 이다

**P1 — 하나씩 보여준다.** `claim`/`failure`/`fix` 를 그대로 보여주고 네 판단(실재하는가,
어떻게 고칠 것인가)을 한 줄 덧붙인다. 결정을 대신하지 않는다.

### 처리 결과를 decisions.md 로 남긴다 — 다음 라운드의 입력이다

사용자 결정이 끝나면 라운드 디렉토리에 `decisions.md` 를 쓴다(머리에만 담아두면 다음
라운드가 같은 지적을 다시 받는다). 형식은 **`assets/decisions-template.md`** 를 복사한다 —
`## 반영` / `## 후속 티켓` / `## 보류` 세 절이 고정이다.

`보류` 가 비어야 다음 라운드로 간다 — 미결을 두고 돌리면 리뷰어가 다시 보고하고, 그게
"수렴하지 않는다"의 실제 원인이다.

### 티켓을 접을 때 — `ABORTED` 마커 파일

GO 전에 루프를 끝내기로 했으면(전제 붕괴 — 루프 절 세 번째 escape) 그 브랜치 디렉토리의
`ABORTED` 파일에 사유를 쓴다 — 절차·권장 본문은 `references/recovery.md`. **파일의 존재가
곧 중단 상태**이고 재개는 파일 삭제뿐이다. 기계가 읽는 것은 마커뿐 — decisions.md 의
서사는 사람용이다.

**미결을 diff 안 문서로 옮겨 적으면 외부 봇의 지적 표면적이 된다**(#1003 실측:
`references/evidence.md`). 외부 봇이 리뷰하는 PR 은 미결 목록을 diff 밖(티켓·PR 본문)에 둔다.

## 루프

```
컨텍스트 작성 → 라운드 → P2 반영 + P1 결정 → '이미 반영/스코프 밖' 누적 → 다음 라운드
```

**GO** = 전 리뷰어의 `regression` findings 0 (`round.json` 의 `verdict`). 종결은 **GO,
또는 사람이 선언한 중단(`ABORTED`)** 뿐 — "GO 날 때까지"는 트리거 문구지 중단을 무시하는
규약이 아니다.

**다음 컨텍스트는 새로 쓰지 않는다** — 직전 `context.md` 복사 후 `decisions.md` 의
`반영`→`이미 반영된 지적`, `후속 티켓`→`스코프 밖`. 두 절이 길어지는 것이 정상이고 그
누적이 수렴을 만든다.

라운드 상한은 5다. 넘어가면 프롬프트를 더 돌리는 게 답이 아니다:

- 매 라운드 **같은 결함의 다른 표면** → 불변식을 하나 세워 수렴시킨다. 불변식은
  context.md 판정 기준 절에 `한 문장 / 보장 주체(파일:줄) / 의존처(제거 시 함께 재설계)`
  로 적는다 — 의존 기록이 없으면 그 누락이 다음 라운드의 결함으로 나온다.
- 매 라운드 **서로 다른 pre-existing 층** → 이 변경 범위가 아니다. 보고된 결함만 최소로
  닫고 나머지는 근본 원인 기준 별도 티켓 + 다음 컨텍스트 `스코프 밖` 고정으로 GO 를 낸다.
- 매 라운드 P1 이 **같은 파일**(same-origin) → 구조적 결합 신호다. (a) 그 사용부를 얇은
  어댑터 뒤로 격리하도록 계획을 바꾸거나 (b) 생존성 7·8행을 재심해 사용자에게
  **중단(ABORTED)/피벗**을 올린다. `resume.py` 가 3라운드 연속 동일 파일을 자동 경고한다 —
  같은 모듈로 흩어지는 반복은 사람이 같은 신호로 읽는다(실측: `references/evidence.md`).

## 게이트 GO 후 — 외부 산출물 최신화

`## 티켓` 이 채워져 있고 `verdict: GO` + partial 아님 + `access_errors`·`보류` 없음이면
**`references/external-sync.md` 를 읽고 그대로 수행한다** — 계획 GO 는 티켓 본문 최신화,
코드 GO 는 AC 코멘트 등록. 승인 없이 write 하지 않는다. `## 티켓` 이 없거나 비면 조용히
건너뛴다(정상 케이스다).

## 다른 세션에서 이어받기

라운드의 입력은 `(저장소 경로, context.md)` 뿐이고 둘 다 디스크에 있다 — 그래서 구현
세션과 리뷰 세션을 분리할 수 있고, 분리가 **권장 기본**이다(비용 실측:
`references/evidence.md`). 이어받을 때는 이것만 실행한다:

```bash
python3 "<red-team-skill>/scripts/resume.py" [TICKET-123]
```

**사용자가 티켓 키만 말했으면 키를 넘겨 호출한다.** 후보가 여럿이면 보여주며 멈추고,
현재 디렉토리가 그 티켓의 워크트리가 아니면 올바른 경로를 경고한다. 키 생략 시 저장소
위치에서 파생한다. **출력이 시키는 대로 한다** — ABORTED 종결, findings 미처리, `보류`
차단, GO/NO-GO 다음 행동, same-origin 경고를 스크립트가 판정해 알려준다.

게이트 전환은 `python3 "<red-team-skill>/scripts/resume.py" --next code` — 컨텍스트를 **누적 이관**한다(덮으면 이전 지적이
되살아난다). 남는 것은 `<!-- TODO(resume) -->` 표시된 `## 리뷰 대상`·`## 검증 상태` 두
절뿐 — 채운 뒤 스크립트가 출력한 `run_round.py` 명령을 그대로 실행한다(`--out` 포함 —
생략하면 디렉토리가 갈라진다).

**자동 전달되지 않는 유일한 것은 변경의 의도다.** **첫 컨텍스트를 쓰는 것이 세션 간 인계
그 자체다** — "diff 보면 알 것"이라고 생략하지 않는다. 리뷰어는 무엇이 *의도된 동작*인지
알 수 없다.

## 구현 중 계획이 바뀌면

`plan-*.md`(구현자 입력)와 `context.md`(리뷰어 입력)가 갈라지면 **조용히 틀린 GO** 가
난다. 계획을 고치게 됐거나 `run_round.py` 의 계획서 신선도 경고(mtime)가 뜨면
**`references/plan-drift.md` 를 읽고 반영처를 가른다** — 판정 기준의 변경은 `context.md`
를 손으로 고친 뒤 라운드를 다시 돌린다.

## 오케스트레이터와 병행 (zax)

zax(`/task`·`/workflow`) 흐름 안에서 게이트로 쓸 때는 **`references/zax.md` 를 읽고
따른다** — 게이트를 끼우는 두 지점, `--from-zax` 컨텍스트 초안, NO-GO 의 귀속처, 하지 말
것 목록이 거기 있다.

## 주의

- 리뷰어는 **읽기 전용**으로 돈다(codex `--non-interactive-permissions deny`, claude
  `--allowedTools Read,Grep,Glob,Bash`). 코드를 고치는 것은 이 스킬을 돌리는 쪽이다.
- 전원 `PARSE-FAIL` = `verdict: INVALID`, 일부 실패 = `coverage: partial` — 둘 다 GO 가
  아니다. 처리는 `references/recovery.md`.
- 커밋되지 않은 변경을 리뷰하는 동안 작업 트리를 건드리지 않는다 — 브랜치 변경·파일
  수정은 리뷰어를 흔들리는 바닥 위에 세운다.

## 참고 문서

| 문서 | 언제 읽나 |
|---|---|
| `references/plan-coverage.md` | **계획 게이트 첫 라운드 전 필수** — clarify, code-hub 확인 |
| `references/engines.md` | 엔진 최초 설정(0단계), 배정 조정·전환·한도, 토큰 기록 해석 |
| `references/recovery.md` | `PARSE-FAIL`·접근오류·`partial`·`INVALID`, 티켓 중단(ABORTED) |
| `references/external-sync.md` | `## 티켓` 이 채워진 라운드가 GO 를 받았을 때 |
| `references/zax.md` | zax 워크플로우와 함께 쓸 때만 |
| `references/moe.md` | `--lean`/MoE 를 켜거나 켜진 상태를 이어받았을 때 |
| `references/plan-drift.md` | 구현 중 계획 수정 시, 계획서 신선도 경고 시 |
| `references/evidence.md` | 규칙의 실측 근거 확인·규칙 완화 검토 시 |
| `references/design.md` | 설계 배경·eval 이력·기각 대안, 축·정책 변경 검토 시 |
| `assets/context-template.md` | 1단계에서 복사해 채우는 context.md 템플릿 |
| `assets/decisions-template.md` | findings 처리 후 복사해 채우는 decisions.md 템플릿 |

## 유지보수

maintainer: [@breadceo](https://github.com/breadceo). 소스는
https://github.com/breadceo/red-team-skills — **개선은 PR 로.** 마켓플레이스 재업로드·설치본
직접 수정 금지(이유: `references/evidence.md`). `pr-triage` 와 경로·기록 규칙을 공유한다.
