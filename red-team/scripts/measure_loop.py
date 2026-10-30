#!/usr/bin/env python3
"""루프 지표 — issue #97(반영 fix 비봉인)·#98(paraphrase) 의 측정. runs2 전체를 소급해 센다.

usage: measure_loop.py [--root <runs 루트>] [--since YYYY-MM-DD] [--by-repo]

| 지표 | 무엇을 세나 | 출처 |
|---|---|---|
| 재개율 | round N+1 의 `regression` finding 중 round N decisions.md `## 반영` 항목의 **파일**을 다시 가리킨 것 | round.json + decisions.md |
| 재개 처리 | 재개된 finding 이 N+1 decisions.md 에서 `## 반영`(fix 가 틀렸었다) / `이미 닫힘:`(헛 재제기) / 미기록 중 어디로 갔나 | decisions.md |
| 의도 대조 | `## 의도 대조` 절이 있는 라운드 중 `불일치` 인 라운드 | decisions.md |
| paraphrase | round.json 에 `paraphrase` 키가 있는 라운드에서 리뷰어 대비 채워진 비율 | round.json |

파일 단위 매칭이라 거친 proxy 다 — 같은 파일의 다른 결함도 재개로 센다. 그래도 변경 전후를
같은 자로 재는 것이 목적이고, 변경 전 기록에는 `이미 닫힘:`·`## 의도 대조` 가 없으므로
그 두 열은 0 이 정상이다(baseline 은 재개율만 비교 대상).
"""
import argparse, json, re, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_round import section, sections  # 절 규격은 한 곳에서만

ROUND_RE = re.compile(r"(plan|code)-(\d+)")
# 경로 토큰: 디렉토리/파일.확장자(:줄)? — 백틱·괄호 안이어도 잡는다
PATH_RE = re.compile(r"(?<![\w/])((?:[\w.@-]+/)*[\w.@-]+\.(?:tsx?|jsx?|mjs|cjs|py|kt|kts|swift|java|go|rs|rb|"
                     r"css|scss|less|html|vue|svelte|json|ya?ml|toml|md|sql|sh))(?::\d+)?\b")


def norm(path: str, root: str = "") -> str:
    p = re.sub(r":\d+(?:-\d+)?$", "", path.strip()).lstrip("./")
    if root and p.startswith(root.rstrip("/") + "/"):
        p = p[len(root.rstrip("/")) + 1:]
    return p


def files_in(text: str) -> set[str]:
    return {norm(m.group(1)) for m in PATH_RE.finditer(text)}


def same_file(a: str, b: str) -> bool:
    """짧은 쪽이 긴 쪽의 경로 꼬리와 일치하면 같은 파일 — 경계는 `/` 다(`a.ts` 가 `ba.ts` 가 아니다)."""
    if a == b:
        return True
    s, l = sorted((a, b), key=len)
    return l.endswith("/" + s)


def hits(files: set[str], pool: set[str]) -> bool:
    return any(same_file(f, p) for f in files for p in pool)


def rounds(branch: Path, gate: str) -> list[tuple[int, Path]]:
    out = []
    for d in branch.iterdir():
        if (m := ROUND_RE.fullmatch(d.name)) and m.group(1) == gate and (d / "round.json").exists():
            out.append((int(m.group(2)), d))
    return sorted(out)


def load(d: Path) -> dict:
    try:
        return json.loads((d / "round.json").read_text())
    except (OSError, ValueError):
        return {}


def regressions(rj: dict) -> list[set[str]]:
    root = rj.get("repo_root") or rj.get("repo_cwd") or ""
    out = []
    for f in rj.get("findings", []):
        if f.get("classification") != "regression":
            continue
        fs = {norm(f[k], root) for k in ("file", "origin_file") if isinstance(f.get(k), str) and f[k].strip()}
        if fs:
            out.append(fs)
    return out


def decisions(d: Path) -> str:
    p = d / "decisions.md"
    return p.read_text() if p.exists() else ""


def judged_closed(dec: str) -> set[str]:
    """`## 판정 완료` 안의 `이미 닫힘:` 항목이 가리키는 파일."""
    body = section(dec, "판정 완료")
    return files_in("\n".join(l for l in body.splitlines() if l.lstrip("-* ").startswith("이미 닫힘")))


def measure(root: Path, since: datetime | None = None) -> dict:
    m = {"pairs": 0, "regressions": 0, "reopened": 0, "reopen_applied": 0, "reopen_closed": 0,
         "reopen_unrecorded": 0, "intent_rounds": 0, "intent_mismatch": 0,
         "para_rounds": 0, "para_expected": 0, "para_filled": 0, "by_repo": {}}
    for branch in (p for p in root.glob("*/*") if p.is_dir()):
        repo = branch.parent.name
        for gate in ("plan", "code"):
            rs = rounds(branch, gate)
            for i, (n, d) in enumerate(rs):
                rj = load(d)
                if since and datetime.fromtimestamp((d / "round.json").stat().st_mtime) < since:
                    continue
                dec = decisions(d)
                if "## 의도 대조" in dec:
                    m["intent_rounds"] += 1
                    m["intent_mismatch"] += "불일치" in section(dec, "의도 대조")
                if isinstance(rj.get("paraphrase"), dict):
                    m["para_rounds"] += 1
                    m["para_expected"] += len(rj.get("reviewers", {}))
                    m["para_filled"] += sum(1 for r in rj.get("reviewers", {}) if rj["paraphrase"].get(r))
                if i == 0 or rs[i - 1][0] != n - 1:
                    continue  # 직전 라운드가 없거나 번호가 건너뛰면 쌍이 아니다
                prev_applied = files_in(section(decisions(rs[i - 1][1]), "반영"))
                m["pairs"] += 1
                regs = regressions(rj)
                m["regressions"] += len(regs)
                if not prev_applied:
                    continue
                cur_applied, cur_closed = files_in(section(dec, "반영")), judged_closed(dec)
                for fs in regs:
                    if not hits(fs, prev_applied):
                        continue
                    m["reopened"] += 1
                    m["by_repo"][repo] = m["by_repo"].get(repo, 0) + 1
                    if hits(fs, cur_applied):
                        m["reopen_applied"] += 1
                    elif hits(fs, cur_closed):
                        m["reopen_closed"] += 1
                    else:
                        m["reopen_unrecorded"] += 1
    return m


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else f"{a}/0"


def report(m: dict, by_repo: bool = False) -> str:
    lines = [
        f"라운드 쌍(N→N+1) {m['pairs']} · N+1 regression findings {m['regressions']}",
        f"재개율 — 직전 `## 반영` 파일을 다시 가리킨 regression: {pct(m['reopened'], m['regressions'])}",
        f"  재개 처리: 반영(fix 가 틀렸었다) {m['reopen_applied']} · 이미 닫힘(헛 재제기) {m['reopen_closed']}"
        f" · 미기록 {m['reopen_unrecorded']}",
        f"의도 대조 — 불일치 라운드: {pct(m['intent_mismatch'], m['intent_rounds'])} (절이 있는 라운드 기준)",
        f"paraphrase — 채움률: {pct(m['para_filled'], m['para_expected'])} (키가 있는 라운드 {m['para_rounds']})",
    ]
    if by_repo and m["by_repo"]:
        lines.append("재개 건수 by repo: " + ", ".join(f"{k} {v}" for k, v in sorted(m["by_repo"].items(), key=lambda x: -x[1])))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path.home() / ".red-team" / "runs2"))
    ap.add_argument("--since", default=None, help="YYYY-MM-DD — 이 날짜 이후 round.json 만 (변경 전후 비교용)")
    ap.add_argument("--by-repo", action="store_true")
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since) if a.since else None
    print(f"root={a.root}" + (f" since={a.since}" if a.since else ""))
    print(report(measure(Path(a.root), since), a.by_repo))


if __name__ == "__main__":
    main()
