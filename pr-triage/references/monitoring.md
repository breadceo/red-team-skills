# 호스트별 PR 감시

> SKILL.md의 0단계에서 참조된다. 감시를 시작할 때 현재 호스트에 맞는 한 분기만 실행한다.

`<pr-triage-skill>`은 **현재 읽은 `pr-triage/SKILL.md`가 들어 있는 절대 디렉토리**다.
설치 경로를 추측하거나 다른 skills 디렉토리를 검색하지 않는다.

## Claude Code

`Monitor` 도구가 있을 때만 사용한다.

```text
Monitor(command: "python3 <pr-triage-skill>/scripts/watch_comments.py --pr <N>",
        description: "PR #<N> 신규 리뷰 코멘트", persistent: true)
```

## Codex Desktop

`automation_update`를 검색해 사용할 수 있을 때 10분 주기 automation을 만든다. automation의
지시는 아래 한 문장이다.

```text
python3 <pr-triage-skill>/scripts/watch_comments.py --pr <N> --once 를 실행하고,
[pr-triage] 신규 코멘트가 출력된 경우에만 pr-triage SKILL.md 1절부터 처리한다.
```

## 그 밖의 호스트 또는 CLI

장기 실행 셸을 유지할 수 있으면 아래 프로세스를 실행한다.

```bash
python3 <pr-triage-skill>/scripts/watch_comments.py --pr <N>
```

장기 실행도 예약 도구도 없으면 자동 감시를 제공한다고 말하지 않는다. 사용자가 확인을 요청할
때마다 `--once`로 한 번 실행한다. 존재하지 않는 `Monitor`, `/loop`, automation 도구를 추측하지 않는다.

첫 실행은 기존 코멘트를 알리지 않는다. 미처리분은 SKILL.md 1절에서 확인한다. 장기 프로세스는
신규가 24시간 없으면 종료한다(`--max-empty-hours`). 알림 하나가 트리아지 라운드 하나다.
