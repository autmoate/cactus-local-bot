"""End-to-end eval suite (plan §47-48): cases from eval_cases.json.

Base needle2 is variance-prone across trivial prompt perturbations (measured;
see README), so pytest asserts the suite-level tool accuracy (plan §45), while
the CLI runner prints per-case details and supports --repeat/--hybrid:

    uv run python tests/test_e2e.py [--repeat 3] [--hybrid]
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import pytest

from local_calendar.agent import Agent, execute_call
from local_calendar import calendar as cal
from local_calendar.calendar import CalendarStore

pytestmark = pytest.mark.needle

CASES = Path(__file__).resolve().parent.parent / "eval_cases.json"


def load_cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))


def check_expected(agent: Agent, case: dict) -> bool:
    """Final-semantics check (plan §48): the final DB state / execution status /
    final response must match the expected fields — not just the tool name."""
    want = case.get("expected")
    if not want:
        return True
    if "result_contains" in want:
        if want["result_contains"].lower() not in (case.get("_result") or "").lower():
            return False
    if "result_not_contains" in want:
        if want["result_not_contains"].lower() in (case.get("_result") or "").lower():
            return False
    if "absent" in want:  # no entry with this title may remain
        for absent_title in want["absent"]:
            for ev in agent.store.events_between(
                    cal.now() - cal.timedelta(days=400),
                    cal.now() + cal.timedelta(days=400)):
                if absent_title.lower() in ev.title.lower():
                    return False
    if "exists" in want:  # multi-item: every described entry must exist
        for want_ev in want["exists"]:
            if not _event_matches(agent.store, want_ev):
                return False
        return True
    if not any(k in want for k in ("all_day", "title_contains", "span_days",
                                   "start_date", "start_time", "end_time",
                                   "participants")):
        return True  # response-only expectation (reads, ask_user)
    events = agent.store.events_between(
        cal.now() - cal.timedelta(days=400), cal.now() + cal.timedelta(days=400))
    if not events:
        return False
    ev = max(events, key=lambda e: e.id or 0)  # the entry the run just wrote
    return _event_fields_match(ev, want)


def _event_matches(store, want: dict) -> bool:
    events = store.events_between(
        cal.now() - cal.timedelta(days=400), cal.now() + cal.timedelta(days=400))
    return any(_event_fields_match(ev, want) for ev in events)


def _event_fields_match(ev, want: dict) -> bool:
    if "title_contains" in want and want["title_contains"].lower() not in ev.title.lower():
        return False
    if "all_day" in want and ev.all_day != want["all_day"]:
        return False
    if "span_days" in want and (ev.end.date() - ev.start.date()).days != want["span_days"]:
        return False
    if "start_date" in want and ev.start.date().isoformat() != want["start_date"]:
        return False
    if "start_time" in want and ev.start.strftime("%H:%M") != want["start_time"]:
        return False
    if "end_time" in want and ev.end.strftime("%H:%M") != want["end_time"]:
        return False
    if "participants" in want:
        have = {p.lower() for p in ev.participants}
        if not set(p.lower() for p in want["participants"]) <= have:
            return False
    return True


def run_case(agent: Agent, case: dict) -> dict:
    for step in case.get("setup") or []:
        execute_call(agent.store, step["tool"], step["args"])
    final = None
    for trace in agent.handle(case["input"]):
        final = trace
    calls = [s for s in final["steps"] if s["name"] == "needle_complete"]
    last = calls[-1]["output"] if calls else {}
    got = (last.get("function_calls") or [{}])[0]
    want = case.get("expected_tool")
    tool_ok = (got.get("name") == want) if want else not got.get("name")
    case["_result"] = final.get("result") or ""
    return {
        "input": case["input"],
        "tool": got.get("name"),
        "tool_ok": tool_ok,
        "executed": final.get("executed", False),
        "should_execute": case.get("should_execute", True),
        "semantic_ok": check_expected(agent, case),
        "confidence": final.get("confidence"),
        "latency_ms": final.get("total_ms"),
        "result": (final.get("result") or "")[:120],
        "canonical": final.get("canonical"),
    }


def test_eval_suite(capsys):
    # the suite runs needle-only; controller cases (hybrid_only) run via main(--hybrid)
    cases = [c for c in load_cases() if not c.get("hybrid_only")]
    rows = []
    for case in cases:
        store = CalendarStore(Path(tempfile.mkdtemp()) / "eval.db")
        agent = Agent(store, mode="needle")
        rows.append(run_case(agent, case))
    for r in rows:
        ok = r["tool_ok"] and r["executed"] == r["should_execute"] and r["semantic_ok"]
        print(f"{'OK ' if ok else 'FAIL'} {r['input'][:52]!r:56} -> {r['tool']} "
              f"conf={r['confidence']} executed={r['executed']} "
              f"sem={r['semantic_ok']} | {r['result'][:60]}")
    hits = sum(1 for r in rows
               if r["tool_ok"] and r["executed"] == r["should_execute"]
               and r["semantic_ok"])
    print(f"score: {hits}/{len(rows)}")
    # semantic-aware threshold (plan §45/48): the honest number is lower than the
    # old tool-name-only score; known gaps are documented in the README
    assert hits >= 0.55 * len(rows), rows


def main() -> None:
    repeat = int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1
    mode = "hybrid" if "--hybrid" in sys.argv else "needle"
    cases = load_cases()
    if mode != "hybrid":
        cases = [c for c in cases if not c.get("hybrid_only")]
    rows: list[dict] = []
    for r in range(repeat):
        for i, case in enumerate(cases):
            store = CalendarStore(Path(tempfile.mkdtemp()) / "eval.db")
            agent = Agent(store, mode=mode)
            t0 = time.perf_counter()
            res = run_case(agent, case)
            res["run"] = r
            rows.append(res)
            ok = res['tool_ok'] and res['executed'] == res['should_execute'] and res['semantic_ok']
            print(f"[{r}.{i:02d}] {'OK ' if ok else 'FAIL'} "
                  f"{res['input'][:52]!r:56} -> {res['tool']} conf={res['confidence']} "
                  f"sem={res['semantic_ok']} {round((time.perf_counter() - t0) * 1000)}ms")
    print("-" * 70)
    print(f"score: {summarize(rows) * 100:.0f}% ({len(rows)} runs, mode={mode})")
    lat = [r["latency_ms"] for r in rows if r["latency_ms"]]
    if lat:
        print(f"latency: ø {sum(lat) / len(lat):.0f} ms, max {max(lat):.0f} ms")


def summarize(rows: list[dict]) -> float:
    hits = [r for r in rows
            if r["tool_ok"] and r["executed"] == r["should_execute"]
            and r.get("semantic_ok", True)]
    return len(hits) / len(rows) if rows else 0.0


if __name__ == "__main__":
    main()
