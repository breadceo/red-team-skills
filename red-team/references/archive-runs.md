# 오래된 리뷰 산출물 아카이브

`~/.red-team` 용량을 줄일 때만 읽는다. 먼저 기본 dry-run의 대상 수와 예상 절감량을 확인한다.

```bash
python3 "<red-team-skill>/scripts/archive_runs.py" --older-than 30 --dry-run
python3 "<red-team-skill>/scripts/archive_runs.py" --older-than 30 --apply
```

- 기본 대상은 `coverage == "full"`인 `runs2` 라운드의 오래된 raw·prompt·superseded 파일이다.
- `round.json` 없는 실행/준비 중 라운드, symlink·손상·partial·coverage 미상 라운드, lock 점유 라운드는 건너뛴다.
- 구형 `runs`까지 처리할 때만 `--include-legacy`를 붙인다.
- dry-run과 apply는 기존 또는 끊어진 `.gz`를 같은 `conflicts`로 집계한다. 0이 아니면 원본과 `.gz`를 확인한 뒤 수동 정리한다.
