# 오래된 리뷰 산출물 아카이브

완료된 라운드 뒤에는 30일 지난 `runs2` 대상이 자동으로 압축된다. 리뷰 판정과 분리되어
실패해도 라운드는 그대로 끝나며, 비상 중지할 때만 `RED_TEAM_DISABLE_AUTO_ARCHIVE=1`을 쓴다.

수동으로 확인하거나 즉시 정리할 때는 먼저 dry-run의 대상 수와 예상 절감량을 확인한다.

```bash
python3 "<red-team-skill>/scripts/archive_runs.py" --older-than 30 --dry-run
python3 "<red-team-skill>/scripts/archive_runs.py" --older-than 30 --apply
```

- 기본 대상은 `coverage == "full"`인 `runs2` 라운드의 오래된 raw·prompt·superseded 파일이다.
- `round.json` 없는 실행/준비 중 라운드, symlink·손상·partial·coverage 미상 라운드, lock 점유 라운드는 건너뛴다.
- 구형 `runs`까지 처리할 때만 `--include-legacy`를 붙인다.
- dry-run과 apply는 기존·끊어진 `.gz` 또는 접근·경합으로 안전하게 판정하지 못한 파일을 같은 `conflicts`로 집계한다. 0이 아니면 원본과 `.gz`를 확인한 뒤 수동 정리한다.
