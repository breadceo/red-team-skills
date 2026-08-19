# 정상 경로 이탈 — 부분 실패 복구와 티켓 중단(ABORTED)

> SKILL.md 의 「참고 문서」에서 참조된다. **라운드에 `PARSE-FAIL`·파일접근오류·
> `coverage: partial`·`INVALID` 가 떴을 때, 또는 GO 전에 티켓을 접기로 했을 때 읽는다.**

## verdict 의미론

- 리뷰어 **전원** `PARSE-FAIL` 이면 `verdict` 가 **`INVALID`** 로 찍힌다 — GO 가 아니다.
  엔진이 로그인 안 됐거나 세션이 끊겨도 findings 0 은 결함 없음과 똑같이 보이기 때문에
  스크립트가 대신 막는다.
- **일부만 PARSE-FAIL 인 경우는 `coverage: partial` 로 기록된다** — `verdict` 는 GO/NO-GO
  로 나오지만 그 GO 는 게이트 통과가 아니고, `resume.py` 가 다음 라운드로 넘어가지 못하게
  막는다(축이 빠진 GO 를 통과로 보지 않는 MoE 규칙과 같은 취급이다). 어느 축이 왜 빠졌는지는
  `round.json` 의 `skipped`(호출 안 됨)·`unparsed`(PARSE-FAIL)로 갈라 남는다.
- 혼합 엔진 라운드에서 한 엔진 소속 리뷰어가 전원 PARSE-FAIL 이면 경고가 뜬다 — 그 GO 는
  반쪽짜리이므로 해당 엔진 상태(로그인 등)를 확인하고 재실행한다.
- **축이 `NO-GO` 를 냈는데 findings 가 0 이면 `verdict_dissent` 에 그 축이 남는다** —
  라운드 `verdict` 는 findings 에서만 도출하므로(근거 없는 NO-GO 가 게이트를 세우지 못하게
  하는 방어다) 그 반대가 `GO` 로 집계된다. `coverage` 는 `full` 이라 partial 로도 안 잡힌다.
  그 GO 는 게이트 통과가 아니고 `resume.py` 가 막는다.

## 축이 GO 라고 안 한 GO — `verdict_dissent`

리뷰어가 **라운드 밖 근거**로 NO-GO 를 내는 경우다: 이전 라운드 지적이 코드에 아직 열려
있거나, `context.md` 가 "미반영 N건이 있으니 NO-GO 를 유지하라" 고 지시한 경우.
축 판정은 정확했고 집계가 그것을 덮은 것이라 **재실행할 것이 없다** — 다른 경고들과 달리
치유 명령이 없는 이유다. 사람이 근거를 읽고 가른다:

1. `round.json` 의 `verdict_dissent` 에 적힌 축의 raw(`<축>.txt`)에서 NO-GO 사유를 읽는다.
2. 사유가 **아직 열린 결함**이면 게이트 통과가 아니다 — 고치고 같은 게이트로 라운드를 더 돈다.
3. 사유가 **이미 닫힌 것**(그 사이 반영됨)이면 다음 라운드 `context.md` 의
   `이미 반영된 지적` 에 적어 같은 NO-GO 가 반복되지 않게 한 뒤 라운드를 다시 돈다.

`round.json` 을 손으로 고쳐 `verdict_dissent` 를 지우지 않는다 — 재계산에서 되살아난다.

## 부분 재실행은 원 라운드에 병합한다 — `round.json` 을 손으로 고치지 않는다

`PARSE-FAIL` 이나 파일접근오류가 한두 축에만 나면 **라운드를 버리지 않는다.** 그 축만 다시
돌려 같은 라운드에 병합한다 — 그때까지 그 라운드는 `coverage: partial` 이라 게이트 통과가 아니다:

```bash
python3 "<red-team-skill>/scripts/run_round.py" \
  --gate code --merge-into ~/.red-team/runs2/<owner>__<repo>/<branch키>/code-9 \
  --reviewers b1-state-matrix
```

경로를 손으로 조립하지 않는다 — `<branch키>` 는 해시 접미가 붙을 수 있으니 러너·`resume.py`
가 출력한 `--merge-into` 명령을 그대로 쓴다.
`--cwd` 와 `--context` 는 생략한다 — 그 라운드의 `repo_cwd` 와 `context.md` 를 쓴다.
다른 컨텍스트를 주면 거절한다(라운드가 자체 재현성을 잃는다). 병합이 하는 일:

- 그 리뷰어의 `.txt/.json/.prompt.md` 를 새 결과로 교체하고 이전 것은
  `<reviewer>.superseded-<stamp>.*` 로 **남긴다** (교체 사실은 `reruns` 에 누적)
- 그 리뷰어의 **이전 findings 를 걷어낸 뒤** 새 findings 를 넣는다 (남기면 처리 끝난 지적이 부활한다)
- `access_errors` 에서 그 리뷰어를 지우고 `verdict`·`counts` 를 전수 재계산한다.
  **전원 PARSE-FAIL 이면 GO 가 아니라 `INVALID`** 라는 규칙은 재계산에서도 유지된다

두 번 실패하면 raw 출력을 직접 읽어 findings 를 손으로 옮긴다 — 리뷰어의 판단을 버리지 않는다.
`--merge-into` 가 없던 동안에는 `--out` 으로 별 디렉토리에 돌린 뒤 `round.json` 을 손으로
고치는 수밖에 없었고(`code-9` 실측), 그러면 무엇이 왜 바뀌었는지가 사라진다.

## 티켓을 접을 때 — `ABORTED` 마커 파일

GO 전에 리뷰 루프 자체를 끝내기로 결정했다면(설계 대상 소멸, 전제 붕괴 — SKILL.md 루프
절의 세 번째 escape 참고) `runs2/<owner>__<repo>/<branch키>/ABORTED` 파일에 사유를 쓴다 —
정확한 브랜치 디렉토리는 `resume.py` 출력의 `경로` 줄에서 확인한다. **이 파일의 존재가 곧
중단 상태다** — `resume.py` 가 이후 어떤 진행 안내(다음 라운드, top-up, 재실행)도 거부하고,
재개는 그 파일을 지우는 것으로만 한다. `run_round.py` 를 직접 실행하면 경고가 뜬다(차단은
하지 않는다 — `--out` 실험·eval 흐름을 막지 않기 위해서다).

- 본문에 **저장소 원격 URL 과 원본 브랜치명을 함께 적는 것을 권장**한다 — cross-host 동일
  owner/repo(#10) 같은 잔여 충돌이 있고, 안내에 표시되는 본문으로 어느 작업의 중단인지
  즉시 식별되는 가치가 있다.
- decisions.md 에는 서사(사유·보존할 발견·재개 조건)를 남기는 것을 권장한다 — 단 그것은
  사람용이고, **기계가 읽는 것은 마커 파일뿐이다.** 산문을 판정 입력으로 삼지 않는 것이
  이 설계의 불변식이다(기각 이유: `evidence.md`).
