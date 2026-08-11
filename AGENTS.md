# red-team-skills 저장소 규칙

## 스킬 문서 — progressive disclosure 를 유지한다 (issue #6)

이 저장소의 스킬 문서는 Agent Skills의 3단 로딩(메타데이터 → SKILL.md → 번들 리소스)을
따른다. **스킬 문서를 편집할 때마다 아래를 지킨다:**

- **SKILL.md 는 "무엇을 하라"만.** "왜 그런지"(실측·회고·사고 이력·기각한 대안)는
  `references/evidence.md`(red-team) / `references/measurement.md`(pr-triage) 또는 해당
  주제의 references 파일에 쓴다. 예외: 사람이 판단하는 자리의 기준(P2 승격 4조건,
  pre-existing 판별, 분류 표 등)은 본문에 남긴다.
- **조건부 절차는 references/ 로.** 특정 상황에서만 읽는 절차(zax 병행, MoE, 부분 실패
  복구, 게이트 GO 후 외부 산출물, edge-case 분기)는 본문에 "언제 읽는지 + 포인터"만 두고
  전문은 references 에 둔다. 그대로 복사해 쓰는 파일(context.md·decisions.md 템플릿)은
  `assets/` 에 둔다.
- **새 references/·assets/ 파일은 반드시 SKILL.md 의 `## 참고 문서` 절에 "언제 읽는지"와
  함께 등재한다.** 참조 없는 문서는 모델이 존재를 모른다(과거 DESIGN.md 가 참조 0건으로
  떠 있던 사고).
- **중복 금지.** 같은 내용이 SKILL.md 와 references 양쪽에 있으면 안 된다 — 옮겼으면
  본문에서 지운다.
- **규칙 문장을 지워서 분량을 맞추지 않는다.** 예산이 넘으면 삭제가 아니라 이동이다.
- **frontmatter `description` 은 트리거 표면이다** — 분량 예산과 무관하게 함부로 줄이지
  않는다.

**커밋 전에 반드시 실행:**

```bash
python3 tools/check_skill_docs.py
```

SKILL.md 추정 토큰 ≤ 5,000(한글 1.1 tok/char + 그 외 0.27 근사), references/·assets/ 전
파일의 SKILL.md 참조, 스킬 루트 고아 .md 부재를 검사한다. FAIL 이면 고치고 나서 커밋한다.

## 기타

- 스크립트 로직을 바꾸면 해당 `scripts/test_*.py` 를 전부 돌린다.
- 두 스킬은 경로·기록 규칙(`~/.red-team/runs2/…`)을 공유한다 — 한쪽만 고치면 갈라진다.
- 설치본(`~/.claude/skills/`, `~/.agents/skills/`)을 직접 고치지 않는다. 이 저장소에서
  고치고 PR 을 올린다.
