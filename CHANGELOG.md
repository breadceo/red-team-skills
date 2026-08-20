# 변경 내역

기준선은 이 저장소의 첫 커밋(`4e6382c`, 2026-07-30)이다. 그 이전에 사내 스킬 마켓플레이스
번들로 설치했다면 아래 **업데이트 방법**을 먼저 읽는다 — `update` 로는 새 버전이 오지 않는다.

## 업데이트 방법

### 마켓플레이스로 설치했던 경우 — 한 번은 `add` 로 다시 받아야 한다

```bash
zarketplace add red-team          # pr-triage 도 같이 설치된다 (같은 저장소)
```

`zarketplace update` 를 쓰면 **구버전이 조용히 온다.** 이유는 이렇다 — `update` 는 로컬
manifest 의 항목 유형으로 출처를 판단하는데(`update.js:69`), 번들로 설치한 항목에는
`owner`/`repo` 가 없어서 git 이 아니라 서버 번들 경로로 간다. 그 번들은 저장소 이전 시점의
스냅샷이라 이 문서의 변경이 하나도 들어 있지 않다. 에러도 경고도 없다.

`add` 로 한 번 다시 받으면 manifest 가 git 출처(`type: github`)로 기록되므로,
**그 다음부터는 `zarketplace update` 가 저장소를 따라간다.**

### git 으로 설치한 경우

```bash
cd <클론 경로> && git pull
```

symlink 로 걸어 두었으면 그것으로 끝이다. `--copy` 로 설치했다면 `zarketplace update` 를
한 번 더 돌린다.

### 설치 후 확인

```bash
python3 <설치 경로>/red-team/scripts/run_round.py --help | grep from-zax
```

`--from-zax` 가 보이면 최신이다. 안 보이면 아직 구버전이다.

---

## 2026-08-20

### 🐛 red-team — 판정을 낸 리뷰어가 출력 형식 때문에 PARSE-FAIL 로 버려지지 않는다 (#25)

`extract_json` 이 `` ```json `` 태그 펜스만 찾아서, 리뷰어가 정상 완주하고도 판정 JSON 을
펜스 없이 내면 PARSE-FAIL → `coverage: partial` 이 됐다(실측: b2b PR #915 라운드 16
b3-visibility — raw 528k 정상, 엔진 에러 0, 서술 끝에 판정이 그대로 있었다). 모델 출력
형식은 매 실행 흔들리므로 간헐 재현되는 유실이고, 재실행은 같은 결과를 다시 사 온다.

- **3단 폴백**: ① json 태그 펜스(1순위 유지) → ② 언어 태그 없는 펜스 → ③ bare JSON.
  json 펜스는 **main 파리티 단일 패스**다 — opener 는 줄 위치와 무관한 3개+ 백틱 json 토큰
  (실제 리뷰어가 인라인 opener 로 출력한 실측), closer 는 줄 시작 ```(태그를 반복한 닫는 줄 포함 — main 파리티). 태그 없는
  펜스는 줄 단위 전역 짝짓기다 — 태그별로 따로 스캔하면 언어 태그 펜스의 닫는 ```
  가 태그 없는 opener 로 오인돼 판정을 잃는다(pre-existing 결함 해소). 둘 다 단일
  패스라 lazy 정규식의 O(n²) 재탐색이 없다. bare 는 정방향 span-skip
  `raw_decode` 스캔 — 성공한 객체의 끝으로 점프해 중첩 내부 객체가 판정으로 오인되지
  않고, 각 경로에서 마지막 수락 후보가 이긴다(서술 중간 예시 JSON 보다 진짜 판정).
  bare 는 **문서를 끝내는 객체만** 수락한다 — 산문 속 형식 예시를 미완주 리뷰어의
  판정으로 오인하지 않기 위한 경계이고, 실측 사고의 판정은 서술 마지막에 있었다.
  bare 수락은 **유효 JSON 파스의 연쇄 위에만** 놓인다 — `{`/`[` opener 를 앞에서부터
  raw_decode 로 소비하다가 **한 번이라도 실패하면 이후는 후보가 아니다**. 실패 span
  의 문법(문자열·escape·주석·인용 규약)은 알 수 없어 어떤 휴리스틱 경계도 그 안의
  closer 에 속을 수 있다는 것이 리뷰 4라운드의 실측 결론이고, 이 정책은 깊은 중첩의
  비선형 폭증과 깨진 컨테이너 내부 조각 오인을 증명 가능하게 함께 없앤다. 실패 이후
  판정 유실은 main 과 같은 PARSE-FAIL 이며, 실측 사고 원본은 이 정책에서도 회수됨을
  검증했다. 전 경로가 입력 길이에 선형이다(opener 위치 캐시 포함).
- **수락 조건 강화**: dict + `findings` 가 dict 만 담은 list + `verdict` 가 str +
  중첩 깊이 ≤ 64(세 경로 공통 — 유효하지만 극단적으로 깊은 객체를 수락하면 뒤의
  indent 직렬화가 제곱 증폭/RecursionError 로 라운드를 죽인다). `findings` 키 존재만 보던 기존 조건은 `findings: null` 이 뒤의 `len()`
  을 죽이고, `[null]` 원소가 집계부 `setdefault` 를 죽이고, verdict 없는 객체를 성공
  축으로 집계했다. 거대 정수(4300자리 초과)의 `ValueError` 와 극단 중첩의
  `RecursionError` 도 라운드를 죽이지 않고 건너뛴다 — 파서·진단 루프·
  `count_access_errors`·`parse_output`(뒤 둘은 pre-existing 이지만 extract_json 보다
  먼저 불려 생존 보장을 우회하던 자리) 공통.
- **PARSE-FAIL 진단 한 줄**: raw 크기·엔진 에러 수를 병기해 실측 3유형(모델 용량 /
  실행 환경 / 파서 미스)이 로그에서 바로 갈린다 — 사람이 raw 를 열어 재지 않아도 된다.
  claude 엔진의 `is_error` 표기도 에러로 센다.
- 테스트: `test_extract_json.py` 신규(57건 — 기존 경로 회귀 방지, 실측 bare 형태,
  마지막 판정 우선, 수락 조건, 예시 오인 거부, 중첩·거대 정수·극단 중첩, 깨진
  외곽·미닫힌 배열·인용부호/주석/escape 안 closer 오인 거부, 선형 시간 바운드 4종,
  parse_output·count_access_errors 생존).

## 2026-08-19

### 🐛 red-team — 축 전원 NO-GO 인데 라운드 GO 로 집계되던 불일치를 표면화 (#23)

라운드 verdict 는 findings 에서만 도출되므로, 축이 findings 0건으로 NO-GO 를 내면
(라운드 밖 근거 — 미반영 지적, context.md 의 지시) 조용히 GO 로 흡수됐다. 실측:
b2b PR #915 code-10 — 5축 전원 NO-GO, 라운드 GO, coverage full 이라 어떤 가드에도
안 걸렸다.

- verdict 공식은 그대로 둔다 — findings 도출은 근거 없는 NO-GO 가 게이트를 세우는
  것을 막는 방어다. 대신 불일치를 버리지 않는다: `recompute()` 가 `verdict_dissent`
  를 기록하고, 러너가 경고하고, `resume.py` 가 게이트 통과 보고를 거부한다.
- 치유 명령이 없는 유일한 경고다 — 축들이 옳았으므로 재실행할 것이 없다. 사람이
  해당 축 raw 의 NO-GO 근거를 읽고 판단한다.

## 2026-08-18

### 📝 red-team — fix 문장을 쓸 때의 전제 검증 규칙 (#21)

인계된 fix 는 적힌 그대로 실행되는 단위다. B2C-53709(PR #915)에서 3라운드 연속,
fix 가 "같은 조건", "기존 실패 경로" 처럼 **이미 있는 것을 가리키면서 그 대상의
전제를 확인하지 않아** 회귀가 났다 — P2 체크리스트는 fix 가 유일하게 결정되는지만
묻지, 재사용 대상의 전제가 아직 유효한지는 묻지 않는다.

- SKILL.md `## findings 처리` 아래 `### fix 를 쓸 때` 신설 — 트리거 어휘(같은/그대로/
  기존/둘 다/양쪽/N곳에)와 3검사 요약. 심각도와 무관한 실수라 P2 체크리스트 밖에 뒀다.
- 전문(3상황 표·3검사·"fix 를 고르는 대신 분기를 인계하는" 탈출구)은
  `references/fix-writing.md`, 실측 회귀 3건은 `references/evidence.md`.
- 토큰 예산(≤5k)은 배경 산문 10곳을 references 로 옮겨 지불 — 규칙 문장 삭제 없음.

### ✨ red-team — a-code 에 테스트 변별력(discriminability) 검사 추가 (#20)

변경이 게이트를 통과하면서 기존 테스트를 조용히 무장해제할 수 있다. 실측:
B2C-53427(ceo-client)에서 sync throw 양성 대조 테스트를 뒤 커밋의 구조적 backstop 이
자명하게 참으로 만들었다 — 테스트는 계속 통과했지만 아무것도 보증하지 않았고,
5축 코드 게이트는 code-8·code-9 에서 GO 를 냈다(잡은 것은 PR 리뷰 봇).

- 6번째 리뷰 축(라운드당 ~500-600k 토큰)이 아니라 a-code 항목 5로 넣었다 —
  diff 가 기존 단정을 구조적으로 참이 되게 하는 가드/backstop/기본값, 그리고
  교체된 테스트의 신구 버전이 다른 축을 검증하는 경우를 열거한다.
- 작성자 쪽 대응은 context 템플릿 `검증 상태` 절 — diff 가 구조적 보증을 세우면
  양성 대조를 재실행한다(대조는 그것을 만든 커밋에서만 유효하다).
- 패턴이 다른 티켓에서 재발하면 전용 축을 재검토하는 조건을 `evidence.md` 에 남겼다.

## 2026-08-13

### 🗜️ red-team — 오래된 리뷰 산출물 아카이브 (#18)

- 완료된 라운드 뒤 30일 지난 `runs2` 대상은 자동으로 압축하며, 실패해도 리뷰 판정은 유지한다.
- 기본 dry-run으로 30일 지난 raw·prompt·superseded 파일의 예상 절감량을 먼저 확인한다.
- `coverage == "full"`이고 advisory lock을 잡을 수 있는 완료 라운드만 처리한다.
- stdlib gzip을 검증한 뒤 원자적 no-clobber 게시하고 원본을 삭제한다.
- 구형 `runs/`는 `--include-legacy`를 명시했을 때만 포함한다.

## 2026-08-11

### 📦 red-team·pr-triage — Claude Code와 Codex 공용 Agent Skills (#16)

- 같은 clone을 Claude Code의 `.claude/skills`와 Codex의 `.agents/skills`에 symlink하는 설치법을 추가했다.
- `pr-triage`는 배포본의 sibling `red-team`만 불러온다. 전역·대상 저장소 `.claude`의 다른
  버전을 조용히 섞던 fallback을 제거하고 회귀 테스트를 추가했다.
- PR 감시는 Claude Code `Monitor`, Codex Desktop automation, 장기 실행 CLI를 명시적으로
  분기한다. 없는 도구를 플랫폼 중립 산문으로 추측하지 않는다.
- main agent 모델은 호스트 세션 설정, 축별 모델은 reviewer subprocess 설정이라는 경계를
  문서화했다. 기존 GATES/TIERS와 모델 기본값은 바꾸지 않았다.
- 저장소 규칙은 `AGENTS.md`를 정본으로 두고 `CLAUDE.md`가 import한다.

## 2026-08-07

### 📚 red-team·pr-triage — 스킬 문서가 가벼워졌다: 필요한 순간에만 읽는 구조 (#6)

**스킬이 트리거될 때 컨텍스트에 들어가는 문서가 각 ~14k → ~5k 토큰(1/3)이 됐다.**
매 호출의 컨텍스트 비용이 그만큼 줄고, 본문은 매번 필요한 절차와 판단 기준만 담아
따라가기 쉬워졌다. **지워진 규칙·근거는 없다** — 전부 필요한 순간에 읽는 자리로 옮겼다.

- **조건부 상황은 그때만 읽는다.** 엔진 최초 설정·배정 조정, 부분 실패(`PARSE-FAIL`·
  `partial`) 복구, 계획 게이트 첫 라운드 전 clarify, 게이트 GO 후 티켓/이슈 최신화,
  zax 병행, MoE, docs-only PR·fp 봇 재게시 같은 edge-case 는 각 스킬의 `references/`
  문서로 내려갔고, SKILL.md 의 새 `## 참고 문서` 절이 **언제 읽는지**를 안내한다.
- **실측 근거·회고는 `evidence.md`(red-team)·`measurement.md`(pr-triage)로.** 규칙이
  의심되거나 완화·변경하려 할 때만 읽으면 된다 — 근거의 무게는 그대로다.
- **context.md·decisions.md 를 이제 `assets/` 템플릿에서 복사한다** — 본문에서 코드블록을
  긁어내던 방식보다 빠르고 어긋나지 않는다.
- **`DESIGN.md` 가 `references/design.md` 로 들어와 SKILL.md 에서 참조된다** — 참조 0건
  고아 문서라 모델이 존재를 몰랐던 문제가 사라졌다.
- **동작 변화는 없다.** frontmatter `description`(트리거)·스크립트·리뷰어 프롬프트
  무변경, 스크립트 테스트 8개 통과, 구/신 문서 블라인드 A/B(30문항 분류)로 판정 품질
  회귀 없음을 확인했다.
- **이 구조는 유지된다** — `tools/check_skill_docs.py` 가 토큰 예산(≤5k)·미참조
  리소스·고아 .md 를 검사하고, 저장소 `CLAUDE.md` 가 이후 편집에서 검사 실행을 요구한다.

### 🐛 red-team — runs/ 키 네임스페이스 개선: 디렉토리 충돌 제거 (#8)

라운드 키 `runs/<repo>/<branch>` 가 두 축에서 충돌할 수 있었다 — `<repo>` 는 origin
basename 만 써서 `team-a/app` 과 `team-b/app` 이 같은 `runs/app/` 을 공유했고,
`<branch>` 는 비가역 slug 라 `feature/foo` 와 `feature-foo` 가 같은 디렉토리가 됐다.
충돌하면 라운드 번호·컨텍스트 이관·`latest_round`·ABORTED 마커가 전부 섞였다.

**달라지는 것**

- **라운드 루트가 `runs2/` 로 분리된다** — 구 키는 slug 산출물 전체라, 새 키를 아무리
  단사로 설계해도 같은 루트 안에서는 이미 디스크에 있는 구 디렉토리가 새 키 자리를
  선점하는 전환기 충돌을 막을 수 없었다(코드 게이트 7라운드의 실측 결론). 루트가 다르면
  `runs2/` 아래는 새 코드가 만든 것뿐이라 그 클래스가 통째로 사라진다.
- **repo 키에 owner 가 들어간다** — `runs2/team-a__app/...`. origin URL(https·ssh·scp형)에서
  owner/repo 를 뽑고, 못 뽑는 origin(로컬 경로 등)은 basename 폴백(단, `__` 를 담은
  이름은 pair 키와 겹치지 않게 해시로 가른다).
- **브랜치 키가 단사가 된다** — slug 가 문자를 뭉갠 브랜치(`feature/foo`)에만 원문
  sha1 8자를 접미한다(`feature-foo--87171ad4`). 무손실 브랜치는 키가 그대로다.
  가역 인코딩 대신 단사로 충분하다 — 디렉토리명→브랜치 역방향 소비처가 없고,
  resume 의 워크트리 매칭도 브랜치→키 방향으로 같은 함수를 쓴다. 접미 패턴
  `--<hex8>` 은 예약이라, 그 패턴으로 끝나는 실존 브랜치·`__` 경계가 모호한
  owner/repo(`a__b/c` vs `a/b__c`)도 해시로 갈린다 — 잔여 충돌은 sha1 32bit 뿐이다.
- **구 레이아웃은 자동 이전된다** — 새 경로가 비어 있고 구 경로가 있으면 1회 rename.
  구 디렉토리의 라운드 기록(round.json 의 repo_cwd) **전부**를 대조해 하나라도 다른
  저장소의 origin 을 가리키면 — 바로 그 충돌 케이스 — 건드리지 않고 경고만 한다.
  경로 파생이 `branch_dir()` 한 곳이라 resume·pr-triage 도 자동으로 따라온다.
- **이전(rename)은 쓰기 지점 두 곳에서만 일어난다** — 라운드 생성(resolve_out)과 resume
  의 대상 확정 후 선행 이전. 조회·감시·비교·dry-run 은 전부 무변경 해석이 기본값이라,
  pr-triage 감시나 다른 티켓 키 조회가 디렉토리를 옮기는 부수효과가 없다. dry-run 은
  읽기는 구 경로에서 하되 표시는 실제 실행이 만들 경로를 보여준다.

### ✨ red-team·pr-triage — 게이트 통과 시 외부 산출물(티켓·이슈·PR 본문) 최신화 프로토콜 (#5)

지금까지 게이트는 통과시키는 것까지만 했다 — 계획이 라운드를 돌며 고쳐져도 Jira 티켓 /
GitHub issue 본문은 처음 쓴 그대로였고, 코드 게이트의 AC 충족 근거는 `~/.red-team/runs/`
밖에서 보이지 않았고, 리뷰 반영으로 코드가 바뀌어도 PR description 은 `zax:pr` 초판에
멈춰 있었다. 이제 게이트를 통과하는 순간 그 세 자리가 실제와 일치하게 된다.

**달라지는 것**

- **티켓만 읽는 사람(QA·기획·다음 세션)이 틀린 계획을 읽지 않는다** — 계획 게이트 GO 시
  티켓/이슈 본문에서 확정 계획과 어긋난 문장만 고치는 초안이 올라오고, 게시하면
  `plan-N GO 반영` 코멘트로 변경 사유가 남는다.
- **"무슨 AC 를 무엇으로 충족했나"가 티켓에서 보인다** — 코드 게이트 GO 시 AC 체크리스트
  코멘트(AC 문장 + 코드 위치·커밋·테스트 근거 + 후속 티켓 분리분 + 라운드 식별자)가
  등록된다. 같은 라운드 재실행은 새 코멘트를 만들지 않고 기존 것을 수정한다(멱등).
- **머지 시점의 PR 본문이 실제 변경과 일치한다** — PR 이 approve·LGTM 을 받으면 본문
  최신화를 안내하고, 사용자가 실행하라고 할 때 decisions.md 에 기록된 실제 코드 변경
  기준으로 갱신한다. 회신만 한 분류는 넣지 않는다 — PR 본문은 변경 요약이지 리뷰 응답
  로그가 아니다.

**켜는 법** — context.md 에 `## 티켓` 절(Jira 키 / `owner/repo#N`)을 적으면 끝. 절이
없으면 아무것도 달라지지 않는다 — 티켓 없는 작업과 기존 라운드는 무영향이다. `resume.py`
이관으로 자동 승계되고, `--from-zax` 초안에는 빈 절이 깔린다.

**보증** — 셋 다 같은 원칙으로 묶여 있다:

- 승인 없이 아무것도 게시하지 않는다 — 항상 `변경 전 → 변경 후` 로 승인받고, 자동 게시는
  옵션으로도 없다.
- 변경이 없으면 아무 일도 하지 않는다 — 빈 편집·중복 코멘트를 만들지 않는다.
- 외부 쓰기 실패가 게이트를 취소하지 않는다 — 초안을 사람에게 인계하고 계속 간다.
- 승인을 기다리는 동안 남이 고친 것을 덮어쓰지 않는다 — 게시 직전에 초안의 입력
  전부(본문·코멘트 fingerprint·decisions.md·PR head)를 재대조하고, 달라졌으면 폐기 후
  재승인한다. (GitHub API 에 본문 CAS 가 없어 재조회~write 사이 초 단위 창은 남는다 —
  수용된 한계, 사고 시 편집 이력으로 복구)
- Jira 의 이미지·첨부를 조용히 지우지 않는다 — markdown round-trip 이 ADF 미디어를
  유실시키므로, 원형 보존을 확인할 수 없으면 write 하지 않고 항목별 패치로 인계한다.
- 다른 저장소의 동명 이슈·PR 을 건드리지 않는다 — 모든 gh 명령에 `-R <owner/repo>` 고정.

새 스크립트는 없다 — 수행 주체는 SKILL.md 를 따르는 메인 세션이고, 수단은 기존 `gh` CLI 와
Atlassian MCP(대화형 인증이라 스크립트화 불가 — 스크립트로 만들지 않은 이유)다.
검증: 계획 게이트 10라운드(codex, a-plan+b5)에서 지적 25건+ 를 반영해 plan-10 GO,
저장소 테스트 7개 전부 PASS, 작업 A 를 이슈 #5 본문에 직접 dogfooding. 후속 후보:
빈 본문 APPROVED 리뷰의 수집기 표시(`fetch_comments.py` 본문 필터 — 수집 로직 변경이라
별도 이슈로 분리).

### ✨ red-team — B2C-52951 17라운드 회고 반영: 중단 상태·생존성 질문·same-origin 경고·SDK 근거 계약

계획 게이트를 17라운드 돌리고도 티켓을 접어야 했던 회고(설계 대상이 삭제 예정 파일이었고,
사용자 통증이 스코프 밖이었다)에서 나온 스킬 공백 5건. 이 변경 자체를 계획 게이트 23라운드
(codex)로 검증해 GO 를 받았다.

- **중단(abort) 종결 상태**: `runs/<repo>/<branch>/ABORTED` 마커 파일 — 존재가 곧 상태다.
  `resume.py` 가 어떤 진행 안내보다 먼저 검사해 차단하고(본문 전체 + 지울 경로 출력),
  재개는 파일 삭제로만. `run_round.py` 직접 실행은 경고만 한다(`--out`·eval 보호).
  decisions.md 산문 파싱 설계는 5라운드 연속 엣지 케이스(prefix 오인·fence·주석)가 나와
  버렸다 — 불변식: 기계가 읽는 상태는 사람 산문을 파싱하지 않는다. 상위 문서(description·
  README·DESIGN)의 "GO 까지 반복" 계약도 "GO 또는 중단"으로 갱신.
- **계획 생존성 7·8행**: 6-구조 표에 "설계 수명·로드맵 충돌"과 "통증 위치 대조"를 추가
  (SKILL.md + `--from-zax` 초안). 구버전 진행 중 티켓은 다음 라운드 전 수동 기입(과도기 규칙).
- **same-origin P1 경고**: 같은 게이트 최근 3라운드의 P1 이 전부 같은 파일이면 `resume.py`
  가 구조적 결합 신호를 경고(비차단). findings 계약에 선택 필드 `origin_file`(저장소 루트
  기준 상대 경로) 신설 — 계획 게이트 finding 의 `file` 은 계획 문서를 가리켜 폴백이 게이트별로
  다르다(plan: origin_file 전용 / code: file 폴백 + 계획문서 제외). round.json 에 `repo_root`
  키 신설(하위 디렉토리 실행 정규화). 손상·계약위반 기록은 라운드째 격리 — 비차단 경고가
  resume 를 죽이지 않는다. 라운드 상한 escape 에 세 번째 항목(격리 또는 전제 재심 → 중단/피벗)
  추가, zax 수렴 규칙도 Spec 불일치 반복과 구분.
- **SDK 근거 계약 단일화**: 우선순위는 파일 존재가 아니라 구현 경계 — 호출부 최근접 설치
  사본이 동작을 구현하면 소스가 최종 권위(패키지명@설치버전 병기), 래퍼·바이너리 위임이면
  위임 인용 + 버전 명시 문서, 로컬 사본 없으면 문서 보조 근거. SKILL.md·초안·a-plan·b5·
  a-code 다섯 곳에 동일 적용. code-hub 부재 경로도 정렬.
- **불변식 기록 형식**: 불변식을 세우면 보장 주체/의존처를 함께 적는다 — plan-15→16 에서
  의존이 문서에 없어 P1 이 된 사례의 방지.
- 테스트: `test_abort_sameorigin.py` 신규(차단·본문 전체·--next 거부·재개, 감지·정규화·
  게이트별 후보·손상 격리), `test_from_zax.py` 커버리지 행 8개 검증.

### 🐛 red-team — codex 리뷰어 프롬프트를 argv 대신 stdin 으로 전달

큰 프롬프트를 argv 로 넘기면 `acpx … codex exec "<프롬프트>"` 가 SIGKILL 로 죽고 산출물이
0바이트로 남았다(4회 재현). 임계는 프롬프트 크기가 아니라 argv 경로 자체다 — 실측으로
ASCII 660B 는 통과, 한글 1.8KB·ASCII 5KB 는 결정적으로 사망. 리뷰 프롬프트는 컨텍스트에
diff 스냅샷까지 붙어 100KB 를 넘으므로 codex 리뷰어는 항상 죽는 경로였다.

- `engine_cmd()` 가 `(argv, env)` → `(argv, env, stdin)` 3-tuple 을 돌려준다. codex 는
  `codex exec --file -` 로 argv 에서 프롬프트를 빼고 stdin 으로 넘긴다(acpx 0.11.2 확인).
  claude 는 `-p` 유지, stdin 은 None.
- `run()` 은 stdin 이 있으면 `subprocess.run(input=...)`, 없으면 기존대로
  `stdin=DEVNULL`. 두 경로 모두 EOF 를 주므로 교착 방어는 그대로다.
- 스모크: 112KB 프롬프트 → exit 0, 9.6s, 파싱·토큰 집계 정상(43.7k tok).
- 테스트(`test_engine_config.py`): argv 에 프롬프트가 없고 stdin 으로 가는지 단정,
  e2e 가짜 엔진이 stdin 마커를 grep 해 GO 를 내므로 argv 회귀 시 라운드가 PARSE-FAIL 로 무너진다.

## 2026-08-05

### ✨ pr-triage — 봇 fingerprint 추적·재게시 프로토콜·리액션

봇 리뷰 실측 3종(product-hub#1003 docs-only 94건 중 무효 72%, zigbang-client#9588 오탐
4회 재게시, hermes 계열 594 PR 전수 — 산문 무시·👎만 종결 신호)에서 드러난 공백을 막는다.
스크립트에는 **결정론적으로 계산 가능한 신호만** 넣고(산문 대조 금지 원칙 유지), 판단
규칙은 SKILL.md 로 갔다.

- **fp 파싱** (`fetch_comments.py`) — `fp_markers()` 가 마커 스캔의 단일 정의다(strict
  `^>` blockquote 제거 + 비탐욕 `<!--\s*hermes:fp=(.+?)\s*-->` finditer, #9588 실물 6건
  검증). fp 마커는 🤖 보다 강한 봇 서명으로 `is_bot()` 에 추가됐다 — hermes 는 사람
  리뷰어 계정으로 🤖 없이 재게시한다. 코멘트당
  `fps: [{fp, fp_seq, fp_first_id, fp_replied, fp_reply_url}]` **리스트**(단수 fp 필드
  없음 — 한 코멘트 마커 2개 실물이 4건), `--new-only` 필터 **전** 전체 items 기준 계산,
  목록에 회차 `↻N` 병기 — 재게시 회차는 `fp_seq` 가 유일한 진실이다.
- **diff 대조 플래그** (`fetch_comments.py`) — `pulls/{pr}/files` 를 1회만 호출해
  inline 코멘트에만 `in_diff`/`line_in_hunk` 를 계산한다(side=RIGHT 만 판정 —
  outdated line·LEFT·patch 부재·3000 상한 근접은 null). top-level 봇 코멘트(#1003 류)
  에는 적용 0건 — 같은 응답을 공유하는 `--show-files` 목록 + 모델 대조가 경로다.
  플래그는 표시만 하고 **필터하지 않는다**.
- **재게시 프로토콜·리액션** (`post_replies.py`) — replies.json 에 `fps`(문자열
  리스트)·`reaction`("-1"|"+1") 선택 필드. reaction 은 **fps 필수 게이트**(fp 는 봇
  코멘트에만 있으므로 사람 코멘트 👎 차단 — 값 자체는 모델 복사 신뢰), review+reaction
  은 리액션 API 부재(404)라 거부. 게시 전 검증이 **is_bot 판정 본문을 전 분기(fp 마커·
  선두 🤖·서명 주석)에서 거부**한다 — 봇 본문 raw 인용이 내 회신을 리뷰로 뒤집는
  자기오염(커서 후퇴·fp_seq 인플레이션) 봉쇄이고, fetch 와 같은 헬퍼를 import 해 규칙
  동치를 강제한다. 게시 성공분의 fps 는 **모든 게시 후 일괄**로 상태 `fp_replies` 에
  기록한다(keep-first — 1회차 전문 반박 앵커 보존, save 직전 재읽기 병합). `--cwd`·
  `--bot-marker` 추가, 리액션 실패는 회신 성공과 별도 보고, dry-run 에 리액션 예정 표시.
  SKILL.md 7절에 봇 리뷰어 분기 신설 — 1회차 전문+👎 / 2회차 `fp_reply_url` 링크+3줄
  요약 / 3회차 이후 회신 중단·사용자 보고, 회신 본문 봇 서명 금지, 수동 앵커 폴백(증상
  기반).
- **봇 종결 실패 텔레메트리** (`fetch_comments.py`) — 회신한 fp 의 재게시(내 회신
  이후 생성분만)를 `pr-triage-reposts.jsonl`(`RED_TEAM_HOME` 파생 — LOG 와 같은
  파생식)에 append 한다. **오분류 신호가 아니다** — 봇은 회신이 옳아도 재게시하므로
  분류 로그와 분리했고 채점(score.py)에도 안 들어간다. dedup 은 상태의
  `repost_logged`(`"<fp>:<comment_id>"` 문자열 set), 누적 (10, 30)건 crossing
  (`이전 < t <= 이후`)에서 봇 팀 전달 검토를 정확히 한 번 안내한다.
- 테스트: `pr-triage/scripts/test_fetch_comments.py`·`test_post_replies.py` — gh 를
  fixture 로 대체해 오프라인으로 돈다. fetch↔post 게이트 parity(동일 헬퍼) 포함.
- **harvest 기준선**: `is_bot` 의 fp 마커 분기로 인해 이번 변경 이후 `harvest_replies`
  재수집분은 이전 스냅샷과 직접 비교하지 않는다(과거 회신이 raw 마커를 인용한 경우 mine
  라벨이 이동할 수 있음).

### 📝 pr-triage·red-team — docs-only 사전확률·미결 등재 대조·자기 등재 재수확 금지

- **pr-triage 2절**: 82.7% 기본값은 **코드 PR** 실측이다 — docs-only PR 은 유효율이
  ~28% 로 역전된다(#1003). 기본값을 뒤집지 말고 `--show-files` 대조와 미결 등재 대조를
  먼저 통과시킨다. 지적 대상이 **PR diff 안 문서의 미결 표**에 이미 등재돼 있으면:
  등재로 충족되는 자기참조는 Q1(사실인가)에서 `already-applied`(등재 커밋 인용),
  해결 요구는 Q2 의 `out-of-scope` 근거다 — red-team 기록 없는 PR 에서도 수행한다
  (대조 대상이 기록이 아니라 diff 문서 자체다). 5절에 같은 사례 구분을 추가했다.
- **red-team findings 처리·pr-triage 6-1절**: 미결을 diff 안 문서로 옮겨 적으면 외부
  봇의 지적 표면적이 된다(#1003 — 등재 직후 46건). 외부 봇이 리뷰하는 PR 은 미결
  목록을 diff 밖(티켓·PR 본문)에 우선 둔다.
- **a-plan 리뷰어**: 문서가 스스로 미결/TODO/오픈이슈로 등재한 항목은 finding 이
  아니다 — 등재 내용이 사실과 다르거나 착수 블로커 오분류일 때만 지적한다.

---

## 2026-08-04

### ✨ red-team·pr-triage — 레포 밖 계약(BE)을 code-hub MCP 로 확인 (조건부)

FE 개발자가 clone 권한 없는 BE 레포의 계약(응답 형태·에러 코드·nullability)은 지금까지
검증 수단이 없어 계획서 주장이나 리뷰 코멘트를 그대로 믿거나 confidence 만 낮췄다.
code-hub MCP(사내 42개 레포 master 스냅샷, 3시간 증분)가 연결돼 있으면:

- **red-team 계획 게이트** — 6-구조 표의 6행(BE 계약 위반 기대)을 채울 때 에이전트가
  `findApi`/`getApiImplementation` 으로 계약 증거를 조회해 context.md 판정 기준 절에
  주입한다. 리뷰어 세션은 MCP 를 못 쓰므로(allowedTools 잠금) 주입 지점은 메인 세션이다.
- **pr-triage 2절 ① 사실인가** — 레포 밖을 가리키는 주장을 code-hub 로 확인하고, 응답
  초안에 BE 코드(파일:줄)를 인용한다.

공통 규칙 둘: 증거에 **`master 기준, 동기화 <시각>` 라벨 필수**(미머지 BE PR 과 다를 수
있다 — 라벨 없으면 낡은 계약을 사실로 단정하는 오류 모드가 생긴다), **미머지 BE 변경
전제는 code-hub 로 검증 불가**로 명시하고 사용자에게 올린다. code-hub 는 선택 의존이다 —
없으면 기존 동작 그대로다. 코드 변경 없음(SKILL.md 만).

---

## 2026-08-03

### ✨ red-team — 계획 게이트에 "문서 6-구조 커버리지" clarify 단계

표면만 자꾸 고치게 되는 리뷰 지적의 대부분은 문서에 없던 축에서 온다 — 실측(B2C-52504
회고): 지적 32건 중 25건(78%)이 문서로 예방 가능했다. 계획 게이트 첫 라운드 전에
6-구조(상태 매트릭스 / 개념 사전+소비처 / 신뢰 경계 / 과도기 / 집계 시점 / BE 계약 위반
기대)를 PRD·계획서와 대조해 `반영(인용)/해당없음/누락` 으로 채우고, **`누락` 만
AskUserQuestion 으로 사용자에게 물어** context.md 의 판정 기준 절로 남긴다(보류가 남으면
라운드를 돌리지 않는다). 신규 리뷰 축이 아니라 에이전트 체크리스트다 — 리뷰어는 read-only
findings 출력이라 물을 수 없고, 커버리지 부재는 결함이 아니라 정보 부재라서다. `a-plan`
이 `반영` 행의 인용을 실제로 열어 검증하고, `--from-zax` 계획 초안에 빈 표가 깔린다.

### ✨ red-team — 중간 라운드 다이어트 (MoE, 옵트인)

라운드 비용은 거의 고정이라 라운드 수만큼 선형으로 쌓인다. 축을 "필요 없어 보여서" 빼는
판단은 창립 사고 패턴이므로, **생략이 아니라 유예**로 설계했다: 라운드 1 은 전체 축,
중간 라운드는 `--lean` 이 core(GATES 의 `core` 플래그 — a-code·a-plan) + 직전 라운드에서
regression 을 낸 축을 결정적으로 계산, 축이 빠진 라운드의 GO 는 `coverage: partial` 로
기록되어 **게이트 통과가 아니며** 러너·resume.py 가 빠진 축의 `--merge-into` top-up 명령을
안내한다 — 병합 후의 verdict 만 판정이다. `config.json` 의 `"moe": true` 로 기본화할 수
있고(`--full` 이 1회성 해제), 기본은 꺼짐이다. 코드 게이트 라운드당 리뷰어 비용의 약
70~80% 가 core 외 4축이다(report_usage 45라운드).

### ✨ red-team — diff 스냅샷을 리뷰어 프롬프트에 첨부

리뷰어 5명이 각자 `git diff` 를 다시 뜨는 탐색 턴이 실측 턴당 ~120K 토큰이었다. 코드
게이트는 러너가 라운드 시작 시 diff 를 한 번 떠서 전원 프롬프트에 `## Diff 스냅샷` 으로
첨부하고 `diff.md` 로 보존한다 — `--merge-into` 재실행·top-up 도 같은 스냅샷을 본다(라운드
재현성). 기본은 작업 트리(`git diff HEAD`), 브랜치 diff 는 `--diff-base <ref>`
(`git diff ref...HEAD`). 200K자 초과는 첨부를 생략하고 안내만 한다. context.md 에는 넣지
않는다 — 라운드 간 이관되는 사람 문서라 diff 가 낡은 채 승계된다.

### 📝 red-team — 리뷰 루프 세션 분리를 권장 기본으로

토큰 실측(74세션, $495 API 환산)에서 비용의 64% 는 리뷰어가 아니라 구현·라운드·findings
처리를 한 세션에 쌓은 메인 세션이었다(턴당 컨텍스트 429K vs 리뷰어 120K). "다른 세션에서
이어받기" 가 가능하다는 서술을 **권장 기본 워크플로우**로 격상하고 근거를 적었다.

### 🔍 검토 후 보류 — 리뷰어 세션의 CLAUDE.md 차단

리뷰어 첫 턴 컨텍스트 중앙값 ~50K 중 프롬프트는 3K 뿐이고 대부분이 CLAUDE.md·시스템
오버헤드라는 실측이 있으나, CLAUDE.md 자동 로드를 끄는 단독 플래그가 없다 — `--bare` 는
인증까지 끊는 실측 사고(0단계 참고)가 있고, 컨벤션 위반 탐지 품질 손실 가능성도 지적됐다.
`--setting-sources`·cwd 조합의 첫 턴 usage 실측이 선행돼야 하므로 별도 실험으로 남긴다.
같은 실측의 "라운드 상한 4→3" 제안은 미채택 — 상한 5 는 비용이 아니라 "구조를 재설계할
신호" 로서의 상한이고, 라운드당 비용은 MoE 가 낮춘다.

## 2026-07-31

### ✨ red-team — 컨텍스트 diet: 오래된 반영 기록을 1줄 인덱스로 접기

컨텍스트는 리뷰어 5명 × 매 라운드에 실리므로 `이미 반영된 지적` 누적이 곧 토큰
누적이다. `resume.py --next` 의 carry-forward 가 이제 최근 2개 라운드 블록은 전문을
유지하고, 그 이전 블록은 톱레벨 불릿 첫 줄(매칭 가능한 식별자)만 남기고 접는다 —
재제기 억제에 필요한 것은 식별자 한 줄이고, 결정 전문은 각 라운드 decisions.md 에
그대로 있다(기록 삭제가 아니라 가시성 압축). 스코프 밖·판정 기준 절은 건드리지 않는다
— 잘못 접어 재제기가 살아나면 라운드 하나가 추가돼 절감분을 다 되먹는다.

### ✨ red-team — 축별 배정을 config 로 조정 + AskUserQuestion 설정 플로우

코드의 GATES/TIERS 는 **추천 기본값**이 되고, 사용자별 조정은 `config.json` 의
`assignments` 에 얹힌다. 우선순위: CLI `--model/--effort`(전 리뷰어 강제) >
`assignments[축]` > tier/prefer 기본.

- `--show-assignments` — 유효 배정표 + 축 성격(왜 이 tier 인가) + 오버라이드/추천 병기
- `--set-assignment '축=engine/model/effort'` 저장, `'축='` 로 추천 복귀 (반복 지정 가능)
- 가용 목록 밖 엔진을 가리키는 오버라이드는 자동 무시 — 한도 소진 시
  `--set-engine codex` 한 방 전환이 축별 설정에 발목 잡히지 않고, 복귀하면 되살아난다
- SKILL.md 에 사용자 요청("리뷰 배정 바꿔줘", "claude 아껴야 해" 등) 시
  AskUserQuestion 으로 [codex only / claude only / 추천 복귀 / 축별 세부 조정]을
  제시하는 플로우와 한도 소진 플레이북을 문서화 — 한도가 차기 전에 미리 설정하게 한다

### ✨ red-team — 배정별·축별 비용 대비 성과 리포트 (`report_usage.py`)

라운드가 쌓이면 `report_usage.py [repo/브랜치 조각]` 으로 배정(engine/model/effort)별·
축별 실행 수, 토큰, 비용, regression 수, $/regression 표를 본다. 분자는
`classification == regression`(raw findings 는 말 많은 모델을 과대평가한다),
구버전 라운드(assignments 없음)와 토큰 미집계 라운드는 분리 표기된다.
decisions.md 의 반영 수 조인("accepted/$")은 필요해지면 붙인다.

### ✨ red-team — 리뷰어별 토큰 사용량·비용 집계

subscription 기반이라 청구서로는 엔진별 사용량을 알 수 없었는데, 두 CLI 모두 호출별
usage 를 내려준다는 것을 확인하고 라운드에 집계를 붙였다.

- claude 는 `--output-format json` 의 `usage`/`total_cost_usd`, codex 는 acpx
  `--format json`(ACP 스트림)의 `result.usage` 에서 토큰을 꺼낸다 (`parse_output`)
- `round.json` 의 `assignments` 에 리뷰어별 `tokens: {input, output, total, cost_usd}` 가
  남고, 라운드 끝에 합계가 출력된다. 비용은 API 환산가 — claude 는 CLI 값,
  codex 는 로컬 단가표(`CODEX_PRICES`, cached input 10%)로 계산한다
- 래핑 파싱이 실패하면(CLI 출력 형식 변경 등) 원문 폴백으로 라운드는 그대로 돌고
  토큰 집계만 빠진다 — 집계 기능이 리뷰를 죽이는 경로를 만들지 않는다
- raw 로그(`<리뷰어>.txt`)는 이제 JSON 래핑/스트림 형태다 — 사람이 읽을 리뷰 본문이
  필요하면 `.prompt.md` 와 `.json`(findings)을 본다

### ✨ red-team — 리뷰어 축별 모델·effort·엔진 차별화

지금까지 라운드의 리뷰어 5명은 전원 같은 엔진·같은 모델·같은 effort(예: codex 면
gpt-5.6-terra / medium)로 돌았다. 축 성격은 서로 다른데 스펙이 같으니, 체크리스트형
축(상태 매트릭스·대비 계산)에는 과했고 복합 추론 축(회귀·인터랙션 추적)에는 부족했다 —
특히 CodeRabbit 리뷰 벤치마크 기준 Terra 는 recall 52.5% 로 절제형이라, 결함을 **찾는**
스킬의 deep 축에 쓰기엔 목적과 반대 성향이었다(Sol 은 69.7%).

바뀐 것:

- `GATES` 가 축별 spec(`tier`, `prefer`)을 갖는다. tier(deep/mid/cheap)가 엔진별
  모델·reasoning effort 를 정한다 — codex 는 sol-high / terra-high / luna-medium,
  claude 는 opus-high / sonnet-medium (haiku 는 쓰지 않는다).
- `--set-engine codex,claude` 로 **가용 엔진을 복수 저장**할 수 있다. 두 엔진이 모두
  가용하면 deep 축이 codex/claude 로 갈라져 같은 모델의 맹점이 전 축에 복제되는 것을
  막는다. 한 엔진만 저장하면 기존처럼 전 리뷰어가 통일된다(prefer 는 폴백).
- codex 의 effort 는 `CODEX_CONFIG` env(codex-acp 어댑터가 세션 config 에 병합)로,
  claude 는 `--effort` 플래그로 전달한다. `--model`/`--effort` CLI 는 전 리뷰어 강제
  override 로 남는다.
- `round.json` 에 `assignments`(리뷰어별 engine/model/effort/tier)가 추가됐다.
  `reviewers`(verdict 문자열)와 `engine` 필드 형태는 유지되므로 기존 소비자
  (summarize_round.py, resume.py, pr-triage)는 그대로 동작한다.
- 혼합 라운드에서 한 엔진 소속 리뷰어가 전원 PARSE-FAIL 이면 경고한다 — 남은 엔진의
  GO 에 묻혀 조용히 통과하는 것을 막는다(7/30 `Not logged in` 사고의 재발 경로).

- 최초 설정(0단계)은 AskUserQuestion 멀티셀렉트로 묻는다 — codex/claude 를 체크박스로
  고르고, 고른 것이 그대로 `--set-engine` 콤마 목록이 된다. 구버전 config
  (단수 `engine` 키만 있음)로 올라온 경우에도 한 번 다시 물어 분산 여부를 고르게 한다.

마이그레이션: 없음. 기존 `config.json` 의 단수 `engine` 키를 그대로 읽고(라운드는
그대로 돈다), 다음 스킬 진입 시 0단계가 엔진 구성을 한 번 다시 확인한다.

---

## 2026-07-30

첫 커밋 이후의 변경 3건. 리뷰 엔진 설정(`~/.red-team/config.json`)과 누적 라운드
(`~/.red-team/runs/`)는 그대로 쓰이므로 재설정할 것이 없다.

### 🐛 pr-triage — 감시기가 트리아지 커서를 되돌리던 버그 (`1c93e47`)

**데이터 유실 버그다. pr-triage 를 쓰고 있으면 이 항목 때문에라도 업데이트한다.**

`watch_comments.py` 는 시작할 때 상태 파일을 한 번 읽고, 신규 코멘트가 올라올 때마다 그
시작 시점 스냅샷을 되쓰고 있었다. 감시는 몇 시간씩 살아 있고 그동안
`fetch_comments.py --mark-triaged` 가 같은 파일에 `triaged` 를 쓰므로, **감시가 시작된 뒤에
처리한 표시가 전부 사라졌다** — 실측된 한 PR 에서는 감시 시작 시점의 16건만 남았다.

`--mark-triaged` 는 잘못이 없었다. 제대로 쓰고 성공을 보고했고, 그 뒤에 감시기가 덮었다.
그래서 증상이 "왜 처리한 코멘트가 다시 올라오지" 로만 보였다.

수정: 저장 직전에 디스크를 다시 읽고 `notified` 를 합집합으로 병합한다. 감시기는 그 목록에
추가만 하므로 병합으로 충분하고 락은 필요 없다.

> 이미 되돌아간 상태 파일은 자동 복구되지 않는다. `~/.red-team/runs/<repo>/<branch>/pr-<N>-triage.json`
> 의 `triaged` 가 실제 처리분보다 적으면, 해당 PR 에서 `fetch_comments.py --new-only` 로 미처리
> 목록을 다시 뽑아 확인한다(이미 답한 코멘트는 `→` 표시가 없다).

### ✨ red-team — `zax:task` 와 병행하기 위한 `--from-zax` (`3e8fb2f`)

```bash
run_round.py --cwd <저장소 경로> --gate plan --from-zax <task-name>
```

`~/.zb-task/<task>/PLAN.md` 와 `CONTEXT.md` 를 읽어 리뷰 컨텍스트 초안을
`~/.zb-task/<task>/redteam-context.md` 에 만든다. **초안을 만들면 그 자리에서 멈춘다** —
`## 스코프 밖` 과 `## 검증 상태` 를 채운 뒤 같은 명령을 다시 실행하면 라운드가 돈다.
파일이 이미 있으면 **덮지 않는다**(판정 기준은 사람이 정하는 문서다).

기존 `--context` 방식은 그대로 동작한다. 두 인자를 함께 주면 에러다.

### 🐛 red-team — 계획 신선도 경고가 `PLAN.md` 를 놓치던 버그 (`3e8fb2f`)

라운드 시작 시점에 "계획서가 컨텍스트보다 새롭다"를 알리는 경고가 `plan*.md` 패턴을 쓰고
있었다. **Python glob 은 파일시스템과 무관하게 대소문자를 구분**하므로 `PLAN.md` 는 한 번도
걸리지 않았다 — 그 이름을 쓰는 배치에서는 경고가 처음부터 죽어 있었다. 대소문자 무시로 바꿨다.

손으로 `plan-1.md` 같은 이름을 쓰고 있었다면 이전에도 정상 동작했다. 동작 변화 없음.

### ✨ red-team — 초안에 Spec AC · Gherkin 을 싣는다 (`a410247`)

`CONTEXT.md` 의 `## Spec AC 매핑` 과 `## Gherkin 시나리오` 를 `## 판정 기준` 절로 옮겨
싣는다. 위치는 "이 변경이 하려는 것" 뒤, "스코프 밖" 앞 — 리뷰어가 이걸 **기준으로** 읽어야
하기 때문이다. 이전에는 리뷰어가 수용 기준을 코드에서 역추론했다. 둘 다 없는 태스크면 절
자체를 만들지 않는다(빈 제목만 남기면 리뷰어가 그 공백을 추측으로 채운다).

### 📄 문서 — zax 워크플로우에서 게이트를 어디에 두는가 (`a410247`)

`red-team/SKILL.md` 에 절이 하나 늘었다. 요지는 **게이트가 두 곳뿐인 이유**다 —
`/workflow prd`(형식) · `spec-crew` Crew E(문서 정합) · `/workflow validate`(추적성) ·
`/workflow pr`(역할 리뷰) · `/task done`(tsc·lint·test)이 이미 있고, 이 다섯이 못 보는 축이
하나 남는다. 함께 실린 것: NO-GO 라우팅(어떤 findings 가 `/workflow spec` 으로 올라가야
하는지), 하지 말 것 4개.

### 🧪 테스트

`red-team/scripts/test_from_zax.py` 추가. 프레임워크 없이 `python3 test_from_zax.py` 로 돈다.
