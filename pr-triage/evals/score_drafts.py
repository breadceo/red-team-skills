#!/usr/bin/env python3
"""회신 초안 품질을 과거 응답과 대조해 채점한다.

usage:
  score_drafts.py --answers answers.json --drafts drafts.json

drafts.json: [{"qid": 0, "body": "…초안 전문…"}, ...]

**주관적 '잘 썼나'를 재지 않는다.** 과거 응답이 실제로 담았던 요소를 초안이 담았는지만 본다 —
그 요소들이 리뷰어가 납득하는 데 필요했다는 것은 그 응답으로 대화가 닫혔다는 사실이 증거다.

재는 요소
  판정 명시   판정을 첫 두 문장 안에 밝혔나 (리뷰어가 결론부터 보게)
  커밋 인용   반영했다면 커밋 해시를 짚었나 — 정답이 짚었을 때만 요구한다
  코드 근거   파일:줄 또는 심볼을 인용했나 — 기록이 아니라 코드를 인용하라는 규칙
  번호 대응   리뷰에 번호 매긴 지적이 여럿이면 각각 답했나 (하나만 답하면 나머지가 재제기된다)
  길이비     정답 대비 초안 길이 (너무 짧으면 근거가 빠졌다는 신호)
"""
import argparse, json, re
from pathlib import Path


def has_commit(s):
    # 마크다운 코드백틱 안의 7~10자 hex, 또는 괄호 안 hex
    return bool(re.search(r"[`(\s]([0-9a-f]{7,10})[`)\s,.]", s))


def has_code_ref(s):
    return bool(re.search(r"[\w/.-]+\.(?:ts|tsx|kt|java|js|jsx|py|yml|json)\s*[:#]\s*\d+", s)
                or re.search(r"`[\w.]+\(\)`|`[A-Z]\w+\.(?:tsx?|kt)`", s))


def verdict_early(s):
    head = " ".join(re.sub(r"^#+.*$", "", s, flags=re.M).split())[:160]
    return bool(re.search(r"맞습니다|맞고|정확|유효|반영|수정|이미|범위 밖|후속|유지|의도|"
                          r"아닙니다|해당하지", head))


def numbered_items(s):
    """리뷰/응답에서 번호 매긴 항목 수 — `1.` `**1.` `## 1.` `①` 등."""
    n = len(set(re.findall(r"(?:^|\n)\s*(?:#+\s*)?\*{0,2}(\d)[.)]\s", s)))
    return n + len(set(re.findall(r"[①②③④⑤]", s)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    ap.add_argument("--drafts", required=True)
    ap.add_argument("--questions", default=None, help="번호 대응 채점에 쓸 문항(리뷰 본문)")
    a = ap.parse_args()

    gold = {x["qid"]: x for x in json.loads(Path(a.answers).read_text())["answers"]}
    qs = {}
    if a.questions:
        qs = {q["qid"]: q for q in json.loads(Path(a.questions).read_text())["questions"]}
    drafts = json.loads(Path(a.drafts).read_text())
    drafts = drafts["drafts"] if isinstance(drafts, dict) else drafts

    rows, tally = [], {"판정": [0, 0], "커밋": [0, 0], "코드": [0, 0], "번호": [0, 0]}
    for d in drafts:
        g = gold.get(d["qid"])
        if not g:
            continue
        gr, dr = g["reply"], d["body"]
        r = {"qid": d["qid"], "len": f"{len(dr)}/{len(gr)}"}

        r["판정"] = "○" if verdict_early(dr) else "✗"
        tally["판정"][0] += r["판정"] == "○"; tally["판정"][1] += 1

        # 정답이 커밋을 짚었을 때만 요구한다 — 미룬 판정에는 커밋이 없다
        if has_commit(gr):
            r["커밋"] = "○" if has_commit(dr) else "✗"
            tally["커밋"][0] += r["커밋"] == "○"; tally["커밋"][1] += 1
        else:
            r["커밋"] = "–"

        if has_code_ref(gr):
            r["코드"] = "○" if has_code_ref(dr) else "✗"
            tally["코드"][0] += r["코드"] == "○"; tally["코드"][1] += 1
        else:
            r["코드"] = "–"

        want = max((numbered_items(x["body"]) for x in qs.get(d["qid"], {}).get("reviews", [])),
                   default=0) if qs else numbered_items(gr)
        if want >= 2:
            got = numbered_items(dr)
            r["번호"] = f"{'○' if got >= want else '✗'}({got}/{want})"
            tally["번호"][0] += got >= want; tally["번호"][1] += 1
        else:
            r["번호"] = "–"
        rows.append(r)

    print(f"초안 {len(rows)}건 채점\n")
    print(f"{'qid':>4}  {'판정':<4} {'커밋':<4} {'코드':<4} {'번호':<8} 길이(초안/정답)")
    for r in rows:
        print(f"{r['qid']:>4}  {r['판정']:<5}{r['커밋']:<5}{r['코드']:<5}{r['번호']:<9}{r['len']}")
    print("\n요소별 포함률 (정답이 그 요소를 담은 건에 대해서만)")
    for k, (ok, n) in tally.items():
        print(f"  {k}  {ok}/{n}" + (f"  ({ok/n*100:.0f}%)" if n else "  (해당 없음)"))
    short = [r["qid"] for r in rows
             if int(r["len"].split("/")[0]) < int(r["len"].split("/")[1]) * 0.4]
    if short:
        print(f"\n정답의 40% 미만 길이: q{short} ← 근거가 빠졌을 수 있다")


if __name__ == "__main__":
    main()
