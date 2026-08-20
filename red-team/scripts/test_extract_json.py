#!/usr/bin/env python3
"""extract_json 폴백 계약 (issue #25).

판정을 낸 리뷰어가 출력 형식(펜스 유무) 때문에 PARSE-FAIL 로 버려지면 안 된다.
수락 조건: dict + findings 가 list + verdict 가 str — 이보다 약하면 findings:null 이
뒤의 len() 을 죽이고, verdict 없는 객체가 성공 축으로 집계된다(code-1 리뷰 실측).
프레임워크 없이 `python3 test_extract_json.py` 로 돈다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_round import extract_json

VERDICT = {"verdict": "GO", "findings": []}


def check(name, cond):
    assert cond, name
    print(f"  ok  {name}")


# 1. 기존 경로 — json 태그 펜스 (회귀 방지)
raw = "서술입니다.\n\n```json\n" + json.dumps(VERDICT) + "\n```\n"
check("json 태그 펜스", extract_json(raw) == VERDICT)

# 2. 언어 태그 없는 펜스
raw = "서술입니다.\n\n```\n" + json.dumps(VERDICT) + "\n```\n"
check("태그 없는 펜스", extract_json(raw) == VERDICT)

# 3. bare JSON — issue #25 실측 형태 (서술 끝에 펜스 없이 판정)
raw = ("검토 결과, `visibility` 축에서 새로 발생한 회귀는 없습니다.\n\n"
       "{\n  \"verdict\": \"GO\",\n  \"findings\": []\n}\n")
check("bare JSON (실측 형태)", extract_json(raw) == VERDICT)

# 4. 서술 중간 예시 JSON 이 있어도 마지막 판정이 이긴다
example = {"verdict": "NO-GO", "findings": [{"title": "예시"}]}
raw = ("예를 들어 이런 형태였다면:\n```json\n" + json.dumps(example) + "\n```\n"
       "하지만 실제 판정은:\n```json\n" + json.dumps(VERDICT) + "\n```\n")
check("마지막 펜스가 이긴다", extract_json(raw) == VERDICT)

raw = ("예시: " + json.dumps(example) + "\n실제 판정:\n" + json.dumps(VERDICT) + "\n")
check("마지막 bare 가 이긴다", extract_json(raw) == VERDICT)

# 5. json 태그 펜스가 있으면 그 뒤의 bare 예시보다 우선한다 (기존 동작 보존)
raw = ("```json\n" + json.dumps(VERDICT) + "\n```\n"
       "덧붙여 이런 예시도 있다: " + json.dumps(example) + "\n")
check("태그 펜스 > 뒤따르는 bare", extract_json(raw) == VERDICT)

# 6. 수락 조건 — dict + findings:list + verdict:str 전부 있어야 한다
check("findings 없는 dict 거부", extract_json('{"verdict": "GO"}') is None)
check("verdict 없는 dict 거부", extract_json('{"findings": []}') is None)
check("verdict:null 거부", extract_json('{"verdict": null, "findings": []}') is None)
check("findings:null 거부", extract_json('{"verdict": "GO", "findings": null}') is None)
check("findings:list 아님 거부", extract_json('{"verdict": "GO", "findings": {}}') is None)
check("findings 비-dict 원소 거부",
      extract_json('{"verdict": "NO-GO", "findings": [null]}') is None)
check("JSON 없음", extract_json("그냥 산문입니다.") is None)
check("빈 입력", extract_json("") is None)

# bare 는 문서를 끝내는 객체만 수락한다 (code-2 P1) — 산문 속 형식 예시를
# 미완주 리뷰어의 판정으로 오인하지 않는다
raw = ('검토 과정에서 사용할 예시: {"verdict":"NO-GO","findings":[]}\n'
       "실제 검토를 완료하지 못했습니다.")
check("산문 속 예시만 있으면 거부", extract_json(raw) is None)
raw = json.dumps(VERDICT) + "\n판정 뒤에 서술이 더 붙었다."
check("판정 뒤 서술 — bare 거부 (기존 PARSE-FAIL 유지)", extract_json(raw) is None)
check("리스트 래핑 — 문서끝 객체가 아니므로 거부",
      extract_json('[' + json.dumps(VERDICT) + ']') is None)

# 7. 문자열 리터럴 안 중괄호 — 정규식 균형 카운팅이면 어긋나는 케이스
tricky = {"verdict": "GO", "findings": [{"title": "코드 `a}b{` 인용"}]}
raw = "서술.\n" + json.dumps(tricky, ensure_ascii=False) + "\n"
check("문자열 안 중괄호", extract_json(raw) == tricky)

# 8. **실패 시 전면 차단 정책**(code-8 P1 최종): bare 스캔에서 raw_decode 가 한 번
#    실패하면 이후는 아무것도 후보가 아니다 — 실패 span 의 문법(문자열·escape·주석·
#    인용 규약)은 알 수 없어 어떤 휴리스틱 경계도 속을 수 있다(code-4·5·7·8 4연속
#    실측). 실패 뒤 판정 유실은 main 동일 PARSE-FAIL. 실측 사고(#25) 원본은 판정 앞
#    opener 가 없어 이 정책에서도 회수된다(테스트 3).
raw = '{"findings": 깨짐}\n\n' + json.dumps(VERDICT) + "\n"
check("깨진 span 뒤 — 전면 차단", extract_json(raw) is None)
raw = '{"findings": 깨짐\n\n' + json.dumps(VERDICT) + "\n"
check("닫히지 않은 고아 { 뒤 — main 동일 PARSE-FAIL", extract_json(raw) is None)
raw = ("{'comment': '}', 'nested': " + '{"verdict":"NO-GO","findings":[]}')
check("single-quote 안 closer 에 안 속는다", extract_json(raw) is None)
raw = '{// } 이 닫힘은 주석 안이다\n{"verdict":"GO","findings":[]}'
check("주석 안 closer 에 안 속는다", extract_json(raw) is None)
raw = '{\\} still outer {"verdict":"GO","findings":[]}'
check("escape 뒤 closer 에 안 속는다", extract_json(raw) is None)

# 9. 중첩 — 바깥 판정의 findings 원소가 자체로 "findings" 키를 가져도
#    span-skip 이 바깥 객체를 통째로 읽으므로 안쪽이 판정으로 오인되지 않는다
#    (code-1 P1: 역방향 스캔은 안쪽을 먼저 수락해 진짜 NO-GO 를 잃었다)
nested = {"verdict": "NO-GO", "findings": [{"findings": [], "title": "nested"}]}
raw = "서술.\n" + json.dumps(nested) + "\n"
check("중첩 내부 객체가 판정을 가리지 않는다", extract_json(raw) == nested)

nested2 = {"verdict": "NO-GO", "findings": [{"title": "t", "file": "a.py"}]}
raw = "서술.\n" + json.dumps(nested2) + "\n"
check("중첩 객체 무해", extract_json(raw) == nested2)

# 10. 4300자리 초과 정수 — json 이 JSONDecodeError 가 아닌 ValueError 를 낸다.
#     파서가 죽으면 라운드째 죽으므로(code-1 P2) 건너뛰고 계속 가야 한다.
big = '{"verdict": "GO", "findings": [], "n": ' + "9" * 5000 + "}"
check("거대 정수 bare — 죽지 않는다", extract_json("서술.\n" + big + "\n") is None)
check("거대 정수 펜스 — 죽지 않는다",
      extract_json("```json\n" + big + "\n```") is None)
raw = "```json\n" + big + "\n```\n```json\n" + json.dumps(VERDICT) + "\n```\n"
check("거대 정수 건너뛰고 다음 판정", extract_json(raw) == VERDICT)

# 11. 극단 중첩 — json 이 RecursionError 를 낸다. 죽지 않고 건너뛴다 (code-3 P2).
deep = '{"verdict": "GO", "findings": [' + "[" * 50000 + "]" * 50000 + "]}"
check("극단 중첩 bare — 죽지 않는다", extract_json("서술.\n" + deep + "\n") is None)
check("극단 중첩 펜스 — 죽지 않는다",
      extract_json("```json\n" + deep + "\n```") is None)

# 12. 실패 span 건너뛰기 (code-4 P1 두 건)
# (a) 깊은 객체 중첩 — 여는 { 마다 재파싱하면 시간이 비선형으로 폭증한다(실측 16s+).
#     span-skip 은 각 문자를 1회만 방문하므로 즉시 끝나야 한다.
import time
deep_obj = '{"a":' * 50000 + "1" + "}" * 50000
t0 = time.monotonic()
check("깊은 객체 중첩 bare — 죽지 않는다", extract_json("서술.\n" + deep_obj + "\n") is None)
check("깊은 객체 중첩 — 비선형 폭증 없음 (<5s)", time.monotonic() - t0 < 5)
# (b) 닫히지 않은 외곽 객체의 내부 조각을 판정으로 오인하지 않는다
raw = '{"verdict":"GO","findings":[ 깨진 외곽… {"verdict":"NO-GO","findings":[]}'
check("깨진 외곽의 내부 조각 거부", extract_json(raw) is None)
# (b') 짝이 안 맞는 closer 도 span 을 조기 종료시키지 않는다 (code-5 P2) —
#      단일 depth 카운터면 `]` 가 `{` 를 닫아 내부 조각이 다시 후보가 된다
raw = '{"outer": ] 아직 외곽 객체 내부 {"verdict":"GO","findings":[]}'
check("mismatched closer 뒤 내부 조각 거부", extract_json(raw) is None)
# (b'') 닫히지 않은 배열 컨테이너 안의 판정 조각도 거부한다 (code-6 P2) —
#       진입점을 `{` 만 찾으면 앞의 unmatched `[` 를 보지 못한다
raw = '[{"verdict":"NO-GO","findings":[]}'
check("미닫힌 배열 안 조각 거부", extract_json(raw) is None)
# (c) 산문 속 중괄호(파싱 실패)도 전면 차단 — 유효 JSON 이 아닌 span 의 경계는
#     신뢰하지 않는다. main 동일 PARSE-FAIL 이라 악화 아님 (code-8 정책)
raw = "함수 {foo} 를 보라.\n" + json.dumps(VERDICT) + "\n"
check("산문 중괄호 뒤 — 전면 차단 (main 동일)", extract_json(raw) is None)
# 유효 JSON 조각(인라인 예시 등)이 앞에 있는 경우는 파스로 소비되고 회수는 유지된다
raw = '인라인 예시 {"a": 1} 와 배열 [1, 2] 뒤.\n' + json.dumps(VERDICT) + "\n"
check("유효 인라인 JSON 뒤 판정 회수", extract_json(raw) == VERDICT)

# 13. count_access_errors 도 같은 입력에서 죽지 않는다 (extract_json 보다 먼저 불린다)
from run_round import count_access_errors
big_line = '{"result": ' + "9" * 5000 + "}"
deep_line = "[" * 50000 + "]" * 50000
assert count_access_errors(big_line + "\n" + deep_line, "/tmp/x") == 0
print("  ok  count_access_errors — 거대 정수·극단 중첩 생존")

# 14. parse_output 도 같은 계열에서 죽지 않는다 — count_access_errors 다음,
#     extract_json 이전에 불리는 자리라 여기가 뚫리면 PARSE-FAIL 기록 전에 죽는다
from run_round import parse_output
text, tok = parse_output("codex", big_line + "\n" + deep_line, None)
check("parse_output codex — 거대 정수·극단 중첩 생존", tok is None)
text, tok = parse_output("claude", "{" + deep_line + "}", None)
check("parse_output claude — 극단 중첩 생존", tok is None)

# 15. 펜스 스캐너 (code-6 P1, code-3 pre-existing 해소)
# (a) 언어 태그 펜스 뒤의 태그 없는 판정 펜스 — 닫는 ``` 를 opener 로 오인하지 않는다
raw = "```python\ncode()\n```\n\n서술.\n\n```\n" + json.dumps(VERDICT) + "\n```\n"
check("언어 태그 펜스 뒤 태그 없는 판정 펜스", extract_json(raw) == VERDICT)
# (b) 닫는 펜스 없는 opener 유사 문자열이 많아도 비선형 폭증이 없다
t0 = time.monotonic()
check("깨진 펜스 다수 — 죽지 않는다", extract_json("x```\n" * 8000 + "tail") is None)
check("깨진 펜스 다수 — 비선형 폭증 없음 (<2s)", time.monotonic() - t0 < 2)
# (c) 연속 소객체 다수 뒤 판정 — suffix 재복사 없이 선형으로 끝난다
t0 = time.monotonic()
raw = "{}" * 50000 + json.dumps(VERDICT)
check("연속 소객체 뒤 판정 회수", extract_json(raw) == VERDICT)
check("연속 소객체 — 비선형 폭증 없음 (<5s)", time.monotonic() - t0 < 5)
# (d) `{` 없는 연속 배열 — opener 재검색이 선형이어야 한다 (code-7 P2)
t0 = time.monotonic()
check("연속 배열 — 죽지 않는다", extract_json("[]" * 160000) is None)
check("연속 배열 — 비선형 폭증 없음 (<5s)", time.monotonic() - t0 < 5)

# 16. 4백틱 closer — main 의 정규식은 회수했다. 줄 스캐너도 닫아야 한다 (code-7 P2)
raw = "```json\n" + json.dumps(VERDICT) + "\n````\n"
check("4백틱 closer 펜스 회수", extract_json(raw) == VERDICT)

# 17. json 펜스는 main 파리티 (code-9 P1 + b4 실측 회귀)
# (a) 인라인 opener — 실제 리뷰어가 줄 중간에서 펜스를 열었다 (code-9 b4 실측)
raw = ("main 의 동작을 대조하겠습니다.```json\n"
       + json.dumps(VERDICT, indent=2) + "\n```")
check("인라인 ```json opener 회수 (b4 실측)", extract_json(raw) == VERDICT)
# (b) 미닫힌 비-json 펜스 뒤의 json 판정 펜스 — main 이 회수하던 입력
raw = ("설명\n```python\nprint(1)\n최종 판정:\n```json\n"
       + json.dumps(VERDICT) + "\n```\n")
check("미닫힌 python 펜스 뒤 json 펜스 회수", extract_json(raw) == VERDICT)
# (c) 닫는 펜스가 태그를 반복(```json 으로 닫음) — main 의 closer(\n```)가 그 줄
#     앞 세 백틱에 매치해 회수하던 입력 (code-10 P2)
raw = "```json\n" + json.dumps(VERDICT) + "\n```json\n"
check("태그 반복 closer 회수", extract_json(raw) == VERDICT)
# (d) 블록 안 인라인 ```json 은 닫지 않는다 (main 동일 — \n``` 불일치)
raw = "```json\n" + json.dumps(VERDICT) + "\n```\n"
check("기본 닫힘 재확인", extract_json(raw) == VERDICT)

# 18. 유효하지만 극단적으로 깊은 객체는 depth 상한(64)으로 거부 — 수락하면 뒤의
#     json.dumps(indent=2) 저장이 제곱 증폭/RecursionError 로 라운드째 죽는다 (code-9 P1)
deep_valid = '{"verdict":"GO","findings":[],"extra":' + '{"x":' * 62000 + "1" + "}" * 62001
check("유효 62k 중첩 — 거부", extract_json(deep_valid) is None)
mid_valid = '{"verdict":"GO","findings":[],"extra":' + '{"x":' * 200 + "1" + "}" * 201
check("유효 200 중첩 — depth 상한(64) 거부", extract_json(mid_valid) is None)
ok_depth = {"verdict": "GO", "findings": [{"a": {"b": {"c": [1, 2]}}}]}
check("정상 깊이 통과", extract_json(json.dumps(ok_depth)) == ok_depth)

print("all ok")
