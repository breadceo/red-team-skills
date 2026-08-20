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

# 8. 깨진 JSON 뒤에 정상 판정 — 깨진 것을 건너뛰고 찾는다
raw = '{"findings": 깨짐\n\n' + json.dumps(VERDICT) + "\n"
check("깨진 JSON 건너뛰기", extract_json(raw) == VERDICT)

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

print("all ok")
