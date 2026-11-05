# red-team-skills (archived)

**`red-team`·`pr-triage` 스킬은 [`zigbang/zigbang-skills`](https://github.com/zigbang/zigbang-skills/tree/main/plugins/review) 의 `review` 플러그인으로 옮겨졌습니다.** 커밋 히스토리도 함께 옮겼습니다. 이 저장소는 읽기 전용입니다.

## 옮겨 가는 법

```text
/plugin marketplace add zigbang/zigbang-skills
/plugin install review@zigbang
```

```bash
rm -f ~/.claude/skills/red-team ~/.claude/skills/pr-triage \
      ~/.agents/skills/red-team ~/.agents/skills/pr-triage
```

`~/.red-team/` 은 그대로 둡니다. 예전 clone 은 지워도 됩니다.

예전 이슈·PR 은 이 저장소에서 계속 볼 수 있습니다.
