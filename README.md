# red-team-skills

Claude Code 스킬 두 개. **커밋 전에 스스로 털고, PR 에 달린 리뷰 코멘트를 그 기록 위에서 처리한다.**

| 스킬 | 하는 일 |
|---|---|
| [`red-team`](red-team/) | 계획 문서나 구현 diff 에 축별 병렬 적대적 리뷰 라운드를 GO 가 날 때까지 돌린다 |
| [`pr-triage`](pr-triage/) | 내 PR 에 달린 리뷰 코멘트를 감시·분류하고, 실제 결함만 고쳐 회신 초안까지 만든다 |

두 스킬은 짝이다. `red-team` 이 라운드를 돌리며 결정 기록을 남기고, `pr-triage` 는 남이 남긴
코멘트를 그 기록 위에서 판정한다. 같은 `~/.red-team/runs/<repo>/<branch>/` 경로 규칙을
공유하므로 **둘을 같은 위치에 설치한다** — 한쪽만 있으면 트리아지 커서와 리뷰 라운드가
다른 디렉토리를 가리키고 "이미 처리한 코멘트" 가 조용히 사라진다.

## 왜

혼자 쓴 코드를 혼자 검토하면 자기가 이미 옳다고 믿는 축에서만 검토한다. 코드 정합성 축으로
**8라운드**를 돌려 GO 를 받은 변경이 PR 자동 리뷰에서 **6건**을 맞았고 전부 실제 결함이었다.
8라운드가 놓친 이유는 축이 하나였기 때문이다 — "사용자에게 무엇이 보이고, 누르면 무엇이
되나" 를 아무도 안 봤다. 그래서 축을 나눠 독립된 리뷰어를 동시에 돌린다.

변경 내역과 **업데이트 방법**은 [CHANGELOG.md](CHANGELOG.md) 를 본다 — 마켓플레이스 번들로
설치했던 경우 `update` 가 아니라 `add` 로 한 번 다시 받아야 한다.

## 설치

```bash
git clone https://github.com/breadceo/red-team-skills.git
cd red-team-skills
ln -s "$PWD/red-team"  ~/.claude/skills/red-team
ln -s "$PWD/pr-triage" ~/.claude/skills/pr-triage
```

symlink 로 걸어도 스크립트가 실경로 기준으로 형제를 찾으므로 의존 해석은 그대로 동작한다.
프로젝트 로컬(`<repo>/.claude/skills/`)에 두어도 되고, 전역·로컬 어느 쪽이든 **둘을 같은
위치에** 두기만 하면 된다.

## 사전 조건

| 무엇 | 왜 |
|---|---|
| 리뷰 엔진 — `acpx` + OpenAI codex CLI **및/또는** 로그인된 `claude` CLI | `red-team` 이 리뷰어를 이 엔진으로 돌린다. 최초 1회 `run_round.py --set-engine <codex\|claude\|codex,claude>` 로 고른다 — 둘 다 저장하면 리뷰어 축별로 분산된다 |
| `gh` CLI 인증 | `pr-triage` 의 코멘트 수집·회신이 전부 `gh api` 다 |
| Python 3 | 표준 라이브러리만 쓴다. 추가 의존성 없음 |

## 첫 실행

```bash
# 0. 엔진 선택 (최초 1회, 이후 변경도 같은 명령 — 둘 다 있으면 codex,claude 권장)
python3 ~/.claude/skills/red-team/scripts/run_round.py --set-engine codex,claude

# 1. 코드 게이트 한 라운드
python3 ~/.claude/skills/red-team/scripts/run_round.py \
  --cwd <저장소 경로> --gate code --context <context.md 경로>

# 2. 이어받기 — 어디까지 됐고 다음에 뭘 할지 알려준다
python3 ~/.claude/skills/red-team/scripts/resume.py
```

라운드 산출물은 **저장소 밖**(`~/.red-team/runs/`)에 쌓인다. 리뷰 산출물이 `git status` 에
뜨면 리뷰 중 작업 트리가 흔들리고 PR 에 섞여 들어가기 때문이다.

비용 레버 세 개 (자세한 근거는 `red-team/SKILL.md`):

- **리뷰 루프를 구현 세션과 분리**해 돌린다 — 실측에서 비용의 64% 가 리뷰어가 아니라 모든
  것을 한 세션에 쌓은 메인 세션이었다. `resume.py` 가 분리를 공짜로 만든다.
- 코드 게이트는 러너가 **diff 스냅샷을 리뷰어 프롬프트에 첨부**한다 — 리뷰어마다 diff 를
  다시 뜨는 탐색 턴을 없앤다. 브랜치 diff 는 `--diff-base <base>`.
- **중간 라운드 다이어트 `--lean`** (옵트인 MoE) — 중간 라운드는 core + 직전 결함 축만 돌고,
  GO 는 언제나 전체 축 top-up 후에만 확정된다. 생략이 아니라 유예라 커버리지가 줄지 않는다.

자세한 사용법은 각 스킬의 `SKILL.md` 를, `red-team` 의 설계 판단과 측정 기록은
[`red-team/DESIGN.md`](red-team/DESIGN.md) 를 본다.

## 측정 도구는 있고, 채점 데이터는 없다

`pr-triage/evals/` 의 도구 4개는 저장소에 있다. 채점 대상(저자의 과거 PR 응답)과 골든셋은
특정 저장소의 커밋·파일 경로에 묶여 있어 남의 환경에서 재현되지 않으므로 넣지 않았다 —
**네 저장소를 대상으로 직접 수집해 다시 재면 된다.** `pr-triage/SKILL.md` 의 측정 절 참고.

문서에 적힌 수치(분류 분포 82.7% 등)는 저자 환경에서 PR 30개·리뷰 570건·응답 133건을
실측한 값이다. 네 팀의 리뷰 문화가 다르면 분포도 다르게 나온다.

## 기여

이슈·PR 환영한다. 두 스킬이 경로·기록 규칙을 공유하므로 **한쪽만 고치는 변경은 피한다**.

스킬 마켓플레이스류에 같은 이름으로 재업로드하지 않는다 — 그런 레지스트리는 대개 아이템
정체성이 이름 하나뿐이고 소유권 검사·버전·fork 개념이 없어서, 같은 이름으로 올리면 서로의
변경이 조용히 사라진다. 여기서 PR 로 받는 이유가 그것이다.

maintainer: [@breadceo](https://github.com/breadceo)
