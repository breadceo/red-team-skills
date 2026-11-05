---
name: pr-triage
description: 내 PR 에 달린 리뷰 코멘트를 감시하다가, 신규 코멘트가 올라오면 분류하고 실제 결함은 고쳐서 회신까지 한다. 코드와 red-team 의사결정 기록(context.md·decisions.md)을 근거로 "실제 결함 / 이미 반영 / 스코프 밖 / 중복 / 제안 반박 / by-design" 으로 가르고, 분류를 한 번 확인받고, 수정은 red-team 코드 게이트를 통과시킨 뒤 회신 초안을 올린다 — 라운드 안의 사람 게이트는 분류 확인과 게시 승인 둘뿐이고, PR 이 approve·LGTM 을 받으면 본문 최신화를 안내해 지시가 있을 때 갱신한다. "PR 올렸으니 리뷰 코멘트 처리해줘", "리뷰 오면 계속 대응해줘", "코멘트 감시해줘", "리뷰 반영해줘", "리뷰 봇 지적 처리", "PR 리뷰 온 거 확인해줘" 같은 요청에 쓴다. **내가 작성자**이고 남이 남긴 코멘트를 소비하는 방향이다 — 내가 리뷰어가 되어 남의 PR 을 읽고 코멘트를 생산하는 것은 `review-buddy` 다. **`red-team` 스킬과 `gh` CLI 가 함께 있어야 동작한다.**
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
3. 세션을 다시 시작하고 `review:pr-triage` 으로 원래 요청을 다시 한다.

`~/.red-team/` (엔진 설정·라운드 기록)은 그대로 둔다 — 진행 중이던 라운드도 새 플러그인에서 이어진다.
새 위치: https://github.com/zigbang/zigbang-skills/tree/main/plugins/review
