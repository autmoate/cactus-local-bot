"""Phase 16/L: Gemma controller (production) vs. challengers on complex tasks.
Metric: final DB correctness + model-call count. No production changes."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import needle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_calendar.agent import Agent, execute_call  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402

CASES = [
    {"id": "multi-create", "input": "Trag am 17.9. um 10 Uhr Zahnarzt ein, am 18.9. um 11 Uhr Friseur und am 10.10. um 9 Uhr TÜV.",
     "check": {"exists": [{"title_contains": "zahnarzt", "start_date": "2026-09-17"},
                          {"title_contains": "friseur", "start_date": "2026-09-18"}]}},
    {"id": "find-slot -> move", "input": "Verschieb das Meeting mit Lisa dahin, wo wir nächste Woche beide 90 Minuten frei haben.",
     "setup": [{"tool": "calendar_create", "args": {"title": "Meeting Lisa", "date": "2026-09-18", "time": "10:00"}}],
     "check": {"exists": [{"title_contains": "meeting lisa", "start_date": "2026-09-15"}]}},
    {"id": "ambiguous delete", "input": "Lösch das Meeting mit Lisa.",
     "setup": [{"tool": "calendar_create", "args": {"title": "Meeting Lisa", "date": "2026-09-18", "time": "10:00"}},
               {"tool": "calendar_create", "args": {"title": "Meeting Lisa", "date": "2026-09-20", "time": "14:00"}}],
     "check": {"result_contains": "Mehrere"}},
    {"id": "list -> identify -> move", "input": "Verschieb den Termin Testtreffen auf den 20.9.",
     "setup": [{"tool": "calendar_create", "args": {"title": "Testtreffen", "date": "2026-09-16", "time": "15:00"}}],
     "check": {"exists": [{"title_contains": "testtreffen", "start_date": "2026-09-20"}]}},
    {"id": "find-slot -> create", "input": "Finde für mich und Lisa am 17.9. einen 60-Minuten-Slot und leg dort ein Meeting an.",
     "check": {"exists": [{"title_contains": "meeting", "start_date": "2026-09-17"}]}},
    {"id": "historical list", "input": "Was hatte ich am 7.9.?",
     "setup": [{"tool": "calendar_create", "args": {"title": "Historisch", "date": "2026-09-07", "time": "10:00"}}],
     "check": {"result_contains": "Historisch"}},
]


def _final_ok(store, check, result: str = "") -> bool:
    if check.get("result_contains"):
        return check["result_contains"].lower() in (result or "").lower()
    events = store.events_between(cal.now() - cal.timedelta(days=400),
                                  cal.now() + cal.timedelta(days=500))
    for a in check.get("absent", []):
        if any(a.lower() in e.title.lower() for e in events):
            return False
    for w in check.get("exists", []):
        found = False
        for e in events:
            if "title_contains" in w and w["title_contains"].lower() not in e.title.lower():
                continue
            if "start_date" in w and e.start.date().isoformat() != w["start_date"]:
                continue
            found = True
            break
        if not found:
            return False
    return True


def _run_case(path: str, case: dict) -> dict:
    store = CalendarStore(tempfile.mktemp(suffix=".db"))
    for step in case.get("setup") or []:
        execute_call(store, step["tool"], step["args"])
    t0 = time.time()
    if path == "controller":
        agent = Agent(store, mode="hybrid")
        final = None
        for trace in agent.handle(case["input"]):
            final = trace
        result = final.get("result") or ""
        calls = sum(1 for s in final["steps"] if s["name"].startswith("controller"))
    elif path == "canon+run":
        agent = Agent(store, mode="hybrid")
        canon = agent.gemma.canonicalize(case["input"])
        tools = agent.tools
        runner = needle.Needle(tools=list(tools.values()),
                               system=agent.system_facts())
        resp = runner.run(canon, strict=False)
        result = json.dumps(resp.get("results") or [], ensure_ascii=False)[:200]
        calls = 1 + (len(resp.get("results") or []))
    elif path == "needle-raw":
        agent = Agent(store, mode="needle")
        final = None
        for trace in agent.handle(case["input"]):
            final = trace
        result = final.get("result") or ""
        calls = 1
    ok = _final_ok(store, case["check"], result)
    return {"case": case["id"], "path": path, "final_ok": ok,
            "seconds": round(time.time() - t0, 1), "model_calls": calls,
            "result": result[:120]}


def main():
    out = []
    for case in CASES:
        for path in ("controller", "needle-raw"):
            try:
                out.append(_run_case(path, case))
            except Exception as exc:
                out.append({"case": case["id"], "path": path, "final_ok": False,
                            "error": str(exc)[:150]})
            print(json.dumps(out[-1], ensure_ascii=False))
    p = Path(__file__).parent / f"loop_compare_{int(time.time())}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved: {p}")


if __name__ == "__main__":
    main()
