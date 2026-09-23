#!/usr/bin/env python3
"""Native-Agent-Benchmark (Track B) — manual loop vs. run(), echte Tools.

Beantwortet NUR die Agentenfähigkeit: Kann Needle ein Ziel über Tool-RESULTATE
hinweg autonom fertigstellen? Ersetzt KEINEN bestehenden Benchmark (A1/A2/C
bleiben eingefroren).

- Echte Produktionstools: `build_tools(CalendarStore(temp_db))` (Callables, keine
  JSON-Schemas) → nur so kann `run()` überhaupt ausführen.
- Nur dependent chains (der 2. Call braucht das Resultat des 1.).
- Frische SQLite-Fixture pro Fall.
- Gemessen wird die AUSFÜHRUNG + der finale DB-Zustand, nicht das letzte
  `function_calls`-Feld.

Modi:
  --mode manual   reset → complete(goal) → execute(real) → complete(results) → …
  --mode run      reset → run(goal, strict=True)

Usage:
  NEEDLE_WEIGHTS=<cact> PYTHONPATH=src .venv-ft3/bin/python \
      experiments/ft/native_agent_bench.py --tag n3-v4-e5 --mode manual
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics as st
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # experiments/
sys.path.insert(0, str(HERE.parents[1] / "src"))

import needle  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import build_tools  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402

WEEKDAYS = ["montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"]


def _next_weekday(w: int, base: date | None = None) -> str:
    """Datum des nächsten passenden Wochentags als ISO (heute+7 bei gleichem Tag)."""
    d = base or cal.now().date()
    ahead = (w - d.weekday()) % 7 or 7
    return (d + timedelta(days=ahead)).isoformat()


# Fälle: (id, goal, fixture-Liste (title, weekday-offset, time), check)
# goal = dependent chain; fixture erzeugt bekannte Einträge; check(final_db, trace)
CASES = [
    {"id": "find->create", "goal": "Finde nächste Woche einen freien Slot mit Lisa und trag dort ein Meeting ein.",
     "fixture": [("Zahnarzt", 1, "10:00"), ("Morgenrunde", 2, "09:00")],
     "check": "created_at_slot", "want_title": "Meeting"},
    {"id": "list->move", "goal": "Zeig mir, was diese Woche ansteht, und verschieb den Zahnarzt auf Freitag.",
     "fixture": [("Zahnarzt", 1, "10:00")],
     "check": "moved_to_weekday", "want_title": "Zahnarzt", "weekday": 4},
    {"id": "list->delete", "goal": "Schau nach, was am Donnerstag ansteht, und lösch den Eintrag.",
     "fixture": [("Teammeeting", 3, "09:00")],
     "check": "absent", "want_title": "Teammeeting"},
    {"id": "lookup->time", "goal": "Prüf meine Termine am Mittwoch und verschieb den Zahnarzt auf 16 Uhr.",
     "fixture": [("Zahnarzt", 2, "10:00")],
     "check": "moved_to_time", "want_title": "Zahnarzt", "time": "16:00"},
    {"id": "person->delete", "goal": "Was hat Lisa diese Woche? Sag ihren Freitagstermin ab.",
     "fixture": [("Yoga mit Lisa", 4, "18:00")],
     "check": "absent", "want_title": "Yoga"},
    {"id": "find->create-2", "goal": "Finde einen freien Slot für mich am Montag und trag einen Termin 'Coaching' ein.",
     "fixture": [("Friseur", 0, "11:00")],
     "check": "created_at_slot", "want_title": "Coaching"},
    {"id": "list->move-2", "goal": "Zeig mir meine Erinnerungen und verschieb die Medikamente auf 20 Uhr.",
     "fixture": [("Medikamente", 1, "08:00")],
     "check": "moved_to_time", "want_title": "Medikamente", "time": "20:00"},
    {"id": "list->create", "goal": "Zeig mir Mittwoch und trag dann 15 Uhr Sport ein.",
     "fixture": [("Kino", 2, "19:00")],
     "check": "created_at_time", "want_title": "Sport", "time": "15:00"},
    {"id": "lookup->delete-2", "goal": "Prüf meine Termine am Dienstag und lösch den Arztbesuch.",
     "fixture": [("Arztbesuch", 1, "14:00")],
     "check": "absent", "want_title": "Arztbesuch"},
    {"id": "find->create-3", "goal": "Wann habe ich nächste Woche 90 Minuten frei? Leg dort ein Teammeeting an.",
     "fixture": [("Workshop", 2, "10:00")],
     "check": "created_at_slot", "want_title": "Teammeeting"},
]


def _db(store) -> dict:
    return {e.title: e.start.isoformat() for e in store.events_between(
        cal.now() - timedelta(days=3), cal.now() + timedelta(days=70))}


def _make(tag: str, weights: str | None):
    td = Path(tempfile.mkdtemp())
    store = CalendarStore(td / "native.db")
    tools = build_tools(store)
    agent = needle.Needle(tools=list(tools.values()), system=f"date: {cal.now().date()}",
                          weights=weights)
    return store, tools, agent


def _execute(tools, calls) -> list[dict]:
    out = []
    for c in calls:
        name, args = c["name"], c.get("arguments") or {}
        try:
            res = tools[name](**args)
        except Exception as exc:  # noqa: BLE001
            res = f"{type(exc).__name__}: {exc}"
        out.append({"tool": name, "args": args, "result": str(res)[:200]})
    return out


def _slot_times(result: str) -> set[str]:
    """HH:MM aus dem find_slot-Ergebnis ziehen (Slots sind 'HH:MM–HH:MM')."""
    return set(re.findall(r"(\d{2}:\d{2})", result or ""))


def _check(case: dict, before: dict, after: dict, trace: list[dict]) -> tuple[bool, str]:
    kind, title = case["check"], case["want_title"]
    new = {t: s for t, s in after.items() if t not in before}
    if kind == "absent":
        gone = [t for t in before if title.lower() in t.lower() and t not in after]
        return (bool(gone), f"absent? gone={gone}")
    if kind == "moved_to_weekday":
        hit = next((t for t in after if title.lower() in t.lower()), None)
        if not hit:
            return False, "Titel fehlt nach Move"
        wd = cal.datetime.fromisoformat(after[hit]).weekday()
        return (wd == case["weekday"], f"{after[hit]} wd={wd} want={case['weekday']}")
    if kind == "moved_to_time":
        hit = next((t for t in after if title.lower() in t.lower()), None)
        if not hit:
            return False, "Titel fehlt nach Move"
        hm = cal.datetime.fromisoformat(after[hit]).strftime("%H:%M")
        return (hm == case["time"], f"{hm} want={case['time']}")
    if kind == "created_at_time":
        hit = next((t for t in new if title.lower() in t.lower()), None)
        if not hit:
            return False, "neuer Termin fehlt"
        hm = cal.datetime.fromisoformat(new[hit]).strftime("%H:%M")
        return (hm == case["time"], f"{hm} want={case['time']}")
    if kind == "created_at_slot":
        hit = next((t for t in new if title.lower() in t.lower()), None)
        if not hit:
            return False, "neuer Termin fehlt"
        slots = set()
        for step in trace:
            slots |= _slot_times(step.get("result", ""))
        hm = cal.datetime.fromisoformat(new[hit]).strftime("%H:%M")
        return (hm in slots if slots else True, f"{hm} in slots={sorted(slots)[:6]}…")
    return False, f"unbekannter Check {kind}"


def run_case(case: dict, mode: str, weights: str | None) -> dict:
    store, tools, agent = _make(case["id"], weights)
    for title, off, hm in case["fixture"]:
        d = _next_weekday((cal.now().weekday() + off) % 7)
        tools["calendar_create"](title=title, date=d, time=hm)
    before = _db(store)
    agent.reset()
    trace: list[dict] = []
    t0 = time.perf_counter()
    error = None
    suppressed = 0
    if mode == "run":
        try:
            resp = agent.run(case["goal"], strict=True)
            # run() exponiert nur die Resultate (ein Eintrag pro ausgeführtem Call),
            # KEIN per-Call-Argument-Transkript; suppressed_calls zeigt Eingriffe.
            for i, res in enumerate(resp.get("results") or [], 1):
                trace.append({"step": i, "tool": "run()", "args": "",
                              "result": str(res)[:200]})
            suppressed = len(resp.get("suppressed_calls") or [])
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
    else:
        calls = agent.complete(case["goal"]).get("function_calls") or []
        step = 0
        while calls and step < 4:                 # kein reset innerhalb des Goals
            for entry in _execute(tools, calls):
                step += 1
                entry["step"] = step
                trace.append(entry)
            calls = agent.complete(json.dumps(
                [{"tool": t["tool"], "result": t["result"]} for t in trace[-len(calls):]],
                ensure_ascii=False)).get("function_calls") or []
    ms = round((time.perf_counter() - t0) * 1000)
    after = _db(store)
    ok, why = _check(case, before, after, trace)
    writes = [t for t in trace if t["tool"] in
              ("calendar_create", "calendar_move", "calendar_delete")]
    return {"id": case["id"], "mode": mode, "goal": case["goal"],
            "steps": len(trace), "calls": [f"{t['tool']}" for t in trace],
            "goal_completed": ok, "why": why, "ms": ms, "error": error,
            "executed": len(trace), "suppressed": suppressed,
            "extra_calls": max(0, len(writes) - 1),
            "wrong_writes": sum(1 for w in writes if w["tool"] == "calendar_create"
                                and case["check"] in ("absent", "moved_to_time",
                                                      "moved_to_weekday")),
            "db_before": before, "db_after": after, "trace": trace}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--mode", choices=["manual", "run"], default="manual")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    weights = os.environ.get("NEEDLE_WEIGHTS") or None
    cases = CASES[:args.limit] if args.limit else CASES
    rows = [run_case(c, args.mode, weights) for c in cases]
    n = len(rows)
    summary = {
        "tag": args.tag, "mode": args.mode, "weights": weights, "n": n,
        "goal_completed": round(sum(r["goal_completed"] for r in rows) / n, 3),
        "wrong_writes": sum(r["wrong_writes"] for r in rows),
        "extra_calls": sum(r["extra_calls"] for r in rows),
        "suppressed_calls": sum(r.get("suppressed", 0) for r in rows),
        "median_steps": st.median(r["steps"] for r in rows),
        "median_ms": round(st.median(r["ms"] for r in rows)),
        "rows": rows,
    }
    out = HERE / "reports" / f"native_agent_{args.tag}_{args.mode}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    for r in rows:
        print(f"  {'OK ' if r['goal_completed'] else 'FAIL'} {r['id']:<18} "
              f"steps={r['steps']} wrong_writes={r['wrong_writes']} "
              f"{r['ms']}ms {r['calls']} | {r['why']}"
              + (f" | err={r['error']}" if r["error"] else ""))
    print(f"[{args.tag}/{args.mode}] goal_completed {summary['goal_completed']} "
          f"· wrong_writes {summary['wrong_writes']} · median {summary['median_ms']}ms")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
