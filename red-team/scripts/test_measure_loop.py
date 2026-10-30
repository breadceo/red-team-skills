"""measure_loop — 재개율·재개 처리·의도 대조·paraphrase 채움률이 고정 입력에서 맞게 나오는지."""
import json, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from measure_loop import measure, same_file, files_in, report

assert same_file("src/a.ts", "/repo/src/a.ts") and not same_file("a.ts", "ba.ts"), "경로 꼬리 매칭"
assert files_in("- [b2] `apps/web/Button.tsx:42` 죽은 버튼 → 연결 (커밋 aaa)") == {"apps/web/Button.tsx"}
assert files_in("- 이미 닫힘: [a-code] src/x.py:3 → 닫혔다") == {"src/x.py"}

root = pathlib.Path(tempfile.mkdtemp()) / "runs2"
b = root / "org__repo" / "feat-x"
R = lambda f, cls="regression": {"classification": cls, "severity": "P2", "file": f, "reviewer": "a-code"}


def rnd(name, findings, decisions, extra=None):
    d = b / name
    d.mkdir(parents=True)
    rj = {"gate": name.split("-")[0], "repo_root": "/wt/repo", "reviewers": {"a-code": "GO", "b2-interaction": "GO"},
          "findings": findings, **(extra or {})}
    (d / "round.json").write_text(json.dumps(rj))
    (d / "decisions.md").write_text(decisions)


rnd("code-1", [R("/wt/repo/src/a.ts:10"), R("src/z.ts")],
    "## 반영 — x\n- [a-code] src/a.ts:10 널 처리 → 가드 추가 (커밋 aaa)\n- [a-code] src/z.ts 요지 → 고침\n\n## 보류 — x\n")
# code-2: a.ts 재개(→ 다시 반영 = fix 가 틀렸었다), z.ts 재개(→ 이미 닫힘), b.ts 는 새 결함, pre-existing 은 안 센다
rnd("code-2", [R("/wt/repo/src/a.ts:12"), R("src/z.ts:4"), R("src/b.ts"), R("src/a.ts", "pre-existing")],
    "## 의도 대조 — x\n- 불일치 — [b2]: 재시도를 재조회로 읽음 → context 수정, 재라운드 code-3\n\n"
    "## 반영 — x\n- [a-code] src/a.ts:12 가드가 한 분기 빠짐 → 보강 (커밋 bbb)\n\n"
    "## 판정 완료 — x\n- 이미 닫힘: [a-code] src/z.ts:4 → aaa 가 닫았다 — 근거: src/z.ts:4\n",
    {"paraphrase": {"a-code": "재시도 버튼이 쿼리를 다시 읽게 한다"}})
# code-4: 번호가 건너뛰어 code-2 와 쌍이 아니다 — 재개로 세면 안 된다
rnd("code-4", [R("src/a.ts")], "## 의도 대조 — x\n- 일치\n\n## 반영 — x\n", {"paraphrase": {"a-code": "x", "b2-interaction": "y"}})
# plan 게이트는 code 와 섞이지 않는다
rnd("plan-1", [R("docs/plan.md")], "## 반영 — x\n- docs/plan.md 순서 고침\n")

m = measure(root)
assert m["pairs"] == 1, m
assert m["regressions"] == 3, m                     # code-2 의 regression 3 (pre-existing 제외)
assert m["reopened"] == 2 and m["reopen_applied"] == 1 and m["reopen_closed"] == 1 and m["reopen_unrecorded"] == 0, m
assert m["intent_rounds"] == 2 and m["intent_mismatch"] == 1, m
assert m["para_rounds"] == 2 and m["para_expected"] == 4 and m["para_filled"] == 3, m
assert m["by_repo"] == {"org__repo": 2}, m
out = report(m, by_repo=True)
assert "2/3 (67%)" in out and "1/2 (50%)" in out and "3/4 (75%)" in out and "org__repo 2" in out, out

# 빈 루트 — 0 으로 나눠 죽지 않는다
assert "0/0" in report(measure(pathlib.Path(tempfile.mkdtemp())))
print("PASS — 재개율·재개 처리·번호 건너뜀 제외·게이트 분리·의도 대조·paraphrase 채움률·0 분모")
