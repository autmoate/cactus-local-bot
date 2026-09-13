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
from local_calendar.calendar import CalendarStore

pytestmark = pytest.mark.needle

CASES = Path(__file__).resolve().parent.parent / "eval_cases.json"


def load_cases() -> list[dict]:
    return json.loads(CASES.read_text(encoding="utf-8"))


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
    return {
        "input": case["input"],
        "tool": got.get("name"),
        "tool_ok": tool_ok,
        "executed": final.get("executed", False),
        "should_execute": case.get("should_execute", True),
        "confidence": final.get("confidence"),
        "latency_ms": final.get("total_ms"),
        "result": (final.get("result") or "")[:120],
        "canonical": final.get("canonical"),
    }


def test_eval_suite(capsys):
    cases = load_cases()
    rows = []
    for case in cases:
        store = CalendarStore(Path(tempfile.mkdtemp()) / "eval.db")
        agent = Agent(store, mode="needle")
        rows.append(run_case(agent, case))
    for r in rows:
        ok = r["tool_ok"] and r["executed"] == r["should_execute"]
        print(f"{'OK ' if ok else 'FAIL'} {r['input'][:52]!r:56} -> {r['tool']} "
              f"conf={r['confidence']} executed={r['executed']}")
    hits = sum(1 for r in rows
               if r["tool_ok"] and r["executed"] == r["should_execute"])
    print(f"score: {hits}/{len(rows)}")
    assert hits >= 0.75 * len(rows), rows


def main() -> None:
    repeat = int(sys.argv[sys.argv.index("--repeat") + 1]) if "--repeat" in sys.argv else 1
    mode = "hybrid" if "--hybrid" in sys.argv else "needle"
    cases = load_cases()
    rows: list[dict] = []
    for r in range(repeat):
        for i, case in enumerate(cases):
            store = CalendarStore(Path(tempfile.mkdtemp()) / "eval.db")
            agent = Agent(store, mode=mode)
            t0 = time.perf_counter()
            res = run_case(agent, case)
            res["run"] = r
            rows.append(res)
            print(f"[{r}.{i:02d}] {'OK ' if res['tool_ok'] and res['executed'] == res['should_execute'] else 'FAIL'} "
                  f"{res['input'][:52]!r:56} -> {res['tool']} conf={res['confidence']} "
                  f"{round((time.perf_counter() - t0) * 1000)}ms")
    print("-" * 70)
    print(f"score: {summarize(rows) * 100:.0f}% ({len(rows)} runs, mode={mode})")
    lat = [r["latency_ms"] for r in rows if r["latency_ms"]]
    if lat:
        print(f"latency: ø {sum(lat) / len(lat):.0f} ms, max {max(lat):.0f} ms")


def summarize(rows: list[dict]) -> float:
    hits = [r for r in rows
            if r["tool_ok"] and r["executed"] == r["should_execute"]]
    return len(hits) / len(rows) if rows else 0.0


if __name__ == "__main__":
    main()
