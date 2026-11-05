---
name: red-team
description: 계획 문서나 구현 diff 에 적대적 리뷰 라운드를 돌려 GO 가 나거나 사람이 중단(ABORTED)을 선언할 때까지 반복한다. 리뷰어를 축별로 병렬 실행하고 결함을 P1(결정 필요)/P2(일괄 승인)로 분류해 사용자에게 올린다. "red-team", "레드팀", "적대적 리뷰", "코덱스 리뷰 돌려줘", "이 계획 검증해줘", "커밋 전에 털어줘", "구현 다 됐으니 리뷰", "GO 날 때까지" 같은 요청에 쓴다. **티켓 키만 언급해도 이어받는다** — "red-team TICKET-123", "TICKET-123 이어서", "지난번 리뷰 이어서 진행" 처럼 부르면 그 티켓의 마지막 라운드를 찾아 다음 할 일을 알려준다. 커밋·PR 직전 게이트로도 쓰고, fix-flow 가 계획·코드 게이트에서 호출한다.
---
> **이 스킬은 `zigbang/zigbang-skills` 의 `review` 플러그인으로 옮겨졌다.** 이 저장소는 archive 됐고 더 갱신되지 않는다.

## 에이전트가 할 일

이 SKILL.md 가 읽혔다면 사용자가 예전 설치본(심링크)을 쓰고 있다. **작업을 진행하지 말고** 사용자에게 아래를 알린 뒤, 승인을 받으면 2~3번을 실행한다.

1. 새 플러그인을 설치한다 (직방 구성원 — 저장소가 private 이다):
   ```text
   /plugin marketplace add zigbang/zigbang-skills
   /plugin install review@zigbang
   ```
   Codex 는 `zigbang-skills` 의 `.agents/plugins/marketplace.json` 에서 `review` 를 설치한다.
2. 예전 심링크를 지운다 (남기면 이 안내가 계속 뜬다):
   ```bash
   rm -f ~/.claude/skills/red-team ~/.claude/skills/pr-triage \
         ~/.agents/skills/red-team ~/.agents/skills/pr-triage
   ```
3. 세션을 다시 시작하고 `review:red-team` 으로 원래 요청을 다시 한다.

`~/.red-team/` (엔진 설정·라운드 기록)은 그대로 둔다 — 진행 중이던 라운드도 새 플러그인에서 이어진다.
새 위치: https://github.com/zigbang/zigbang-skills/tree/main/plugins/review
