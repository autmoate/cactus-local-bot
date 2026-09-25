"""Fixture evaluation for the Thunderbird spike (§16, §21, §22).

Measures business metrics, not JSON equality:
  event_detection_recall, false_positive_rate, candidate_count,
  title_ok, temporal_ok (start+end), location_ok, final_event_ok,
  approval_ready, fields_needing_edit, latency p50/p95.

Whole-mail and selection are reported as separate groups. N2 and N3 run
sequentially (never in one venv, never both in RAM at once).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from extractor import LocalNeedleHost  # noqa: E402
from models import MailMessage  # noqa: E402
from thunderbird_sim import SimulatedThunderbird  # noqa: E402
from workflow import CalendarSpike  # noqa: E402

CASE_FILE = HERE / "cases.jsonl"
MY_ADDRESSES = ("me@example.org",)


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _message(case: dict) -> MailMessage:
    m = case["message"]
    return MailMessage(id=case["id"], subject=m.get("subject", ""),
                       sender=(m.get("from") or [""])[0], to=m.get("to") or [],
                       cc=m.get("cc") or [],
                       received_at=datetime.fromisoformat(m["received_at"]),
                       body_text=m.get("body", ""))


def _norm(text: str) -> str:
    text = re.sub(r"[^0-9a-zäöüß ]+", " ", (text or "").casefold())
    return re.sub(r"\s+", " ", text).strip()


def title_ok(gold: str, cand: str) -> bool:
    g, c = _norm(gold), _norm(cand)
    return bool(g) and (g == c or g in c or c in g)


def location_ok(gold: str, cand: str) -> bool:
    g, c = _norm(gold), _norm(cand)
    if not g:
        return not c
    return g == c or g in c or c in g


def _dt(cand) -> str:
    return cand.strftime("%Y-%m-%dT%H:%M:%S") if cand else ""


def match_event(expected: dict, candidate) -> dict:
    return {
        "title_ok": title_ok(expected.get("title", ""), candidate.title),
        "start_ok": _dt(candidate.start) == expected.get("start", ""),
        "end_ok": _dt(candidate.end) == expected.get("end", ""),
        "location_ok": location_ok(expected.get("location", ""),
                                   candidate.location),
    }


def score_case(expected: dict, candidates: list, latency_ms: float = 0.0) -> dict:
    exp_count = expected.get("event_count", 0)
    n = len(candidates)
    row = {"candidate_count": n, "latency_ms": latency_ms,
           "event_detected": n >= 1, "count_ok": n == exp_count,
           "false_positive": bool(exp_count == 0 and n > 0),
           "title_ok": False, "start_ok": False, "end_ok": False,
           "location_ok": False, "final_event_ok": False,
           "approval_ready": False, "fields_needing_edit": []}

    if exp_count == 0:
        row["final_event_ok"] = n == 0
        row["approval_ready"] = n == 0
        row["event_detected"] = False
        if n:
            row["fields_needing_edit"] = ["false_positive"]
        return row

    if not candidates:
        row["fields_needing_edit"] = ["missing_event"]
        return row

    if exp_count == 1:
        checks = match_event(expected, candidates[0])
        row.update(checks)
        row["temporal_ok"] = checks["start_ok"] and checks["end_ok"]
        row["final_event_ok"] = all(checks.values())
        mandatory = candidates[0].needs_review and (
            candidates[0].status == "incomplete" or "mehrere Termine erkannt"
            in candidates[0].review_reasons)
        row["approval_ready"] = row["final_event_ok"] and not mandatory
        row["fields_needing_edit"] = [k.replace("_ok", "") for k, v in checks.items()
                                      if not v]
    else:
        used = set()
        matched = []
        for ev in expected.get("events", []):
            for idx, cand in enumerate(candidates):
                if idx in used:
                    continue
                checks = match_event(ev, cand)
                if checks["title_ok"] and checks["start_ok"] and checks["end_ok"]:
                    used.add(idx)
                    matched.append(checks)
                    break
        row["title_ok"] = len(matched) == exp_count and all(
            m["title_ok"] for m in matched)
        row["start_ok"] = len(matched) == exp_count and all(
            m["start_ok"] for m in matched)
        row["end_ok"] = len(matched) == exp_count and all(
            m["end_ok"] for m in matched)
        row["location_ok"] = len(matched) == exp_count and all(
            m["location_ok"] for m in matched)
        row["temporal_ok"] = row["start_ok"] and row["end_ok"]
        row["final_event_ok"] = row["count_ok"] and row["temporal_ok"] and \
            row["title_ok"]
        row["approval_ready"] = row["final_event_ok"] and not any(
            c.needs_review for c in candidates)
        row["fields_needing_edit"] = sorted(
            {k for m in matched for k, v in m.items() if not v})
    return row


def _expected_for(case: dict, mode: str) -> dict | None:
    if mode == "selection":
        if not case.get("selection"):
            return None
        return case.get("selection_expected") or case["expected"]
    return case["expected"]


def aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    def mean(key, subset):
        return round(sum(bool(r.get(key)) for r in subset) / len(subset), 4) \
            if subset else 0.0
    lats = sorted(r["latency_ms"] for r in rows if r["latency_ms"])
    out = {"n": len(rows),
           "final_event_ok": mean("final_event_ok", rows),
           "approval_ready": mean("approval_ready", rows),
           "count_ok": mean("count_ok", rows)}
    pos = [r for r in rows if r.get("positive")]
    neg = [r for r in rows if not r.get("positive")]
    if pos:
        out.update({"event_detection_recall": mean("event_detected", pos),
                    "title_ok": mean("title_ok", pos),
                    "start_ok": mean("start_ok", pos),
                    "end_ok": mean("end_ok", pos),
                    "temporal_ok": mean("temporal_ok", pos),
                    "location_ok": mean("location_ok", pos),
                    "final_event_ok_positive": mean("final_event_ok", pos),
                    "approval_ready_positive": mean("approval_ready", pos)})
    if neg:
        out["false_positive_rate"] = round(
            sum(1 for r in neg if r["false_positive"]) / len(neg), 4)
    if lats:
        out["latency_p50_ms"] = round(st.median(lats))
        out["latency_p95_ms"] = round(lats[min(len(lats) - 1, int(len(lats) * 0.95))])
    return out


def run_backend(backend: str, cases: list[dict], python: str | None,
                weights: str | None, limit: int | None) -> dict:
    host = LocalNeedleHost(backend, python=python, weights=weights)
    ok, why = host.available()
    if not ok:
        host.close()
        return {"backend": backend, "error": why, "groups": {}}
    spike = CalendarSpike(host, my_addresses=MY_ADDRESSES)
    sim = SimulatedThunderbird()
    rows, detail = [], []
    try:
        selected = cases[:limit] if limit else cases
        for case in selected:
            message = _message(case)
            sim.load(message, case.get("selection"))
            for mode in ("message", "selection"):
                expected = _expected_for(case, mode)
                if expected is None:
                    continue
                result = spike.extract(sim.displayed_message(), mode=mode,
                                       selection=sim.selected_text())
                row = score_case(expected, result["candidates"],
                                 result.get("latency_ms", 0.0))
                row.update({"id": case["id"], "mode": mode,
                            "category": case["category"],
                            "positive": expected.get("event_count", 0) > 0,
                            "status": result["status"]})
                rows.append(row)
                detail.append({**row, "expected": expected,
                               "got": [c.to_dict() for c in result["candidates"]],
                               "raw_calls": (result.get("raw") or {}).get("raw", []),
                               "prompt": (result.get("raw") or {}).get("prompt", ""),
                               "error": result.get("error", "")})
                print(f"  {case['id']:>4} {mode:9} {result['status']:9} "
                      f"final={int(row['final_event_ok'])} "
                      f"review={int(bool(row['fields_needing_edit']))}", flush=True)
    finally:
        host.close()
    report = {"backend": backend, "model": host.model, "n_cases": len(selected),
              "groups": {m: aggregate([r for r in rows if r["mode"] == m])
                         for m in ("message", "selection")},
              "categories": {}}
    for cat in sorted({r["category"] for r in rows}):
        report["categories"][cat] = {
            m: aggregate([r for r in rows if r["mode"] == m and r["category"] == cat])
            for m in ("message", "selection")}
    report["rows"] = detail
    return report


def _print_report(report: dict) -> None:
    print(f"\n=== {report['backend']} ({report.get('model','?')}) ===")
    if report.get("error"):
        print("  ERROR:", report["error"])
        return
    for mode, m in report["groups"].items():
        if not m.get("n"):
            continue
        print(f"  [{mode}] n={m['n']} final={m['final_event_ok']:.2f} "
              f"approval={m['approval_ready']:.2f} count={m['count_ok']:.2f} "
              f"detect={m.get('event_detection_recall',0):.2f} "
              f"fp={m.get('false_positive_rate',0):.2f} "
              f"title={m.get('title_ok',0):.2f} temporal={m.get('temporal_ok',0):.2f} "
              f"loc={m.get('location_ok',0):.2f} p50={m.get('latency_p50_ms','?')}ms")


def main() -> int:
    ap = argparse.ArgumentParser(prog="tb-calendar-eval")
    ap.add_argument("--backend", choices=["n2", "n3", "both"], default="n2")
    ap.add_argument("--n2-python", default=None)
    ap.add_argument("--n3-python", default=None)
    ap.add_argument("--weights", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cases = load_cases(CASE_FILE)
    backends = ["n2", "n3"] if args.backend == "both" else [args.backend]
    for backend in backends:
        python = args.n2_python if backend == "n2" else args.n3_python
        report = run_backend(backend, cases, python, args.weights, args.limit)
        _print_report(report)
        out = Path(args.out) if args.out else HERE / "reports" / f"{backend}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False),
                       encoding="utf-8")
        print(f"  report -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
