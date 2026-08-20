#!/usr/bin/env python3
"""extract_json 폴백 계약 (issue #25).

판정을 낸 리뷰어가 출력 형식(펜스 유무) 때문에 PARSE-FAIL 로 버려지면 안 된다.
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

# 6. 수락 조건 — findings 없는 dict / dict 아닌 JSON / JSON 없음 → None
check("findings 없는 dict 거부", extract_json('{"verdict": "GO"}') is None)
check("findings 담은 객체는 리스트 안이라도 회수", extract_json('[{"findings": []}]') == {"findings": []})
check("findings 없는 리스트 거부", extract_json('["a", {"verdict": "GO"}]') is None)
check("JSON 없음", extract_json("그냥 산문입니다.") is None)
check("빈 입력", extract_json("") is None)

# 7. 문자열 리터럴 안 중괄호 — 정규식 균형 카운팅이면 어긋나는 케이스
tricky = {"verdict": "GO", "findings": [{"title": "코드 `a}b{` 인용"}]}
raw = "서술.\n" + json.dumps(tricky, ensure_ascii=False) + "\n"
check("문자열 안 중괄호", extract_json(raw) == tricky)

# 8. 깨진 JSON 뒤에 정상 판정 — 깨진 것을 건너뛰고 찾는다
raw = '{"findings": 깨짐\n\n' + json.dumps(VERDICT) + "\n"
check("깨진 JSON 건너뛰기", extract_json(raw) == VERDICT)

# 9. bare 판정 안의 중첩 객체가 잘못 걸리지 않는다 (역방향이라 안쪽 { 를 먼저 보지만
#    findings 키가 없어 거부되고 바깥 판정이 걸린다)
nested = {"verdict": "NO-GO", "findings": [{"title": "t", "file": "a.py"}]}
raw = "서술.\n" + json.dumps(nested) + "\n"
check("중첩 객체 무해", extract_json(raw) == nested)

print("all ok")
