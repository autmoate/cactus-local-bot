#!/usr/bin/env python3
"""Native-Agent-Benchmark (Track B) — manual loop vs. run(), echte Tools.

Beantwortet NUR die Agentenfähigkeit: Kann Needle ein Ziel über Tool-RESULTATE
hinweg autonom fertigstellen? Ersetzt KEINEN bestehenden Benchmark.

Design-Regeln (Review-Fixes):
- **Echt abhängig:** das Goal enthält die Info (Titel/Datum/Zeit), die der 2.
  Call braucht, NICHT — sie muss aus dem ersten Toolresultat kommen.
- **Fixtures explizit** auf `morgen`/`übermorgen` (keine Wochentag-Offsets, die
  nicht zum Goal-Text passen).
- **Instrumentierung für BEIDE Modi:** die Tool-Callables werden gewrappt, damit
  auch `run()` seine echten Aufrufe (Name, Args, Result) logged → wrong_writes
  ist damit im run()-Modus messbar.

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
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import needle  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import build_tools  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402
import eval_mutations as mut  # noqa: E402

# check-Typen: created_in_slots | created_at_time | moved_to_time | moved_to_day | absent
# fixture: (title, "morgen"|"übermorgen", "HH:MM", participants|"")
CASES = [
    {"id": "find->create-slot",
     "goal": "Finde morgen einen freien Slot mit Lisa und trag dort ein Meeting ein.",
     "fixture": [("Blockzeit", "morgen", "09:00", "Lisa")],
     "check": "created_in_slots", "want_title": "Meeting"},
    {"id": "list->delete-title",
     "goal": "Schau nach, was morgen ansteht, und lösch den Termin.",
     "fixture": [("Kegelabend", "morgen", "19:00", "")],
     "check": "absent", "want_title": "Kegelabend"},
    {"id": "list-person->delete",
     "goal": "Was hat Lisa morgen? Sag ihren Termin ab.",
     "fixture": [("Yoga", "morgen", "18:00", "Lisa")],
     "check": "absent", "want_title": "Yoga"},
    {"id": "list->move-time",
     "goal": "Zeig mir morgen und verschieb den Termin auf 16 Uhr.",
     "fixture": [("Zahnarzt", "morgen", "10:00", "")],
     "check": "moved_to_time", "want_title": "Zahnarzt", "time": "16:00"},
    {"id": "list->move-day",
     "goal": "Schau nach, was morgen ansteht, und verschieb den Eintrag auf übermorgen.",
     "fixture": [("Teammeeting", "morgen", "09:00", "")],
     "check": "moved_to_day", "want_title": "Teammeeting", "plus_days": 2},
    {"id": "find->create-90",
     "goal": "Wann habe ich übermorgen 90 Minuten frei? Leg dort ein Teammeeting an.",
     "fixture": [("Workshop", "übermorgen", "10:00", "")],
     "check": "created_in_slots", "want_title": "Teammeeting"},
    {"id": "list->delete-2",
     "goal": "Prüf, was übermorgen ansteht, und lösch den Eintrag.",
     "fixture": [("Arztbesuch", "übermorgen", "14:00", "")],
     "check": "absent", "want_title": "Arztbesuch"},
    {"id": "find-person->create",
     "goal": "Finde nächste Woche einen freien Slot mit Max und trag dort einen Termin 'Coaching' ein.",
     "fixture": [("Blockzeit", "übermorgen", "13:00", "Max")],
     "check": "created_in_slots", "want_title": "Coaching"},
    {"id": "list->move-2",
     "goal": "Was steht morgen an? Verschieb den Termin auf 15 Uhr.",
     "fixture": [("Friseur", "morgen", "11:00", "")],
     "check": "moved_to_time", "want_title": "Friseur", "time": "15:00"},
    {"id": "list->delete-3",
     "goal": "Zeig mir, was morgen ansteht, und sag den Eintrag ab.",
     "fixture": [("Chorprobe", "morgen", "20:00", "")],
     "check": "absent", "want_title": "Chorprobe"},
]


def _wrap_tools(tools: dict, trace: list[dict]) -> dict:
    """Tool-Callables loggen beim Ausführen (gilt für manual UND run())."""
    wrapped = {}
    for name, fn in tools.items():
        def make(fn, name):
            def call(**args):
                try:
                    res = fn(**args)
                except Exception as exc:  # noqa: BLE001
                    res = f"{type(exc).__name__}: {exc}"
                trace.append({"tool": name, "args": args, "result": str(res)[:200]})
                return res
            return call
        w = make(fn, name)
        if hasattr(fn, "_needle_tool"):
            w._needle_tool = fn._needle_tool
        wrapped[name] = w
    return wrapped


def _db(store) -> dict:
    return {e.title: e.start.isoformat() for e in store.events_between(
        cal.now() - timedelta(days=3), cal.now() + timedelta(days=70))}


def _slot_times(trace: list[dict]) -> set[str]:
    out = set()
    for t in trace:
        if t["tool"] == "calendar_find_slot":
            out |= set(re.findall(r"(\d{2}:\d{2})", t.get("result", "") or ""))
    return out


def _check(case, before: dict, after: dict, trace: list[dict]):
    kind, title = case["check"], case["want_title"]
    new = {t: s for t, s in after.items() if t not in before}
    if kind == "absent":
        gone = [t for t in before if title.lower() in t.lower() and t not in after]
        return bool(gone), f"gone={gone}"
    hit_after = next((t for t in after if title.lower() in t.lower()), None)
    if kind == "moved_to_time":
        if not hit_after:
            return False, "Titel fehlt"
        hm = cal.datetime.fromisoformat(after[hit_after]).strftime("%H:%M")
        return hm == case["time"], f"{hm} want={case['time']}"
    if kind == "moved_to_day":
        if not hit_after:
            return False, "Titel fehlt"
        want_day = (cal.now().date() + timedelta(days=case["plus_days"]))
        got = cal.datetime.fromisoformat(after[hit_after]).date()
        return got == want_day, f"{got} want={want_day}"
    if kind == "created_at_time":
        if not hit_after or hit_after not in new:
            return False, "neuer Termin fehlt"
        hm = cal.datetime.fromisoformat(new[hit_after]).strftime("%H:%M")
        return hm == case["time"], f"{hm} want={case['time']}"
    if kind == "created_in_slots":
        if not hit_after or hit_after not in new:
            return False, "neuer Termin fehlt"
        slots = _slot_times(trace)
        hm = cal.datetime.fromisoformat(new[hit_after]).strftime("%H:%M")
        return (hm in slots if slots else True), f"{hm} in {sorted(slots)[:6]}"
    return False, f"unbekannter Check {kind}"


def run_case(case, mode: str, weights: str | None) -> dict:
    td = Path(tempfile.mkdtemp())
    store = CalendarStore(td / "native.db")
    raw = build_tools(store)
    trace: list[dict] = []
    tools = _wrap_tools(raw, trace)
    for title, day, hm, persons in case["fixture"]:
        raw["calendar_create"](title=title, date=day, time=hm, participants=persons)
    before = _db(store)
    before_snap = mut.snapshot(store)
    agent = needle.Needle(tools=list(tools.values()), system=f"date: {cal.now().date()}",
                          weights=weights)
    agent.reset()
    error = None
    t0 = time.perf_counter()
    if mode == "run":
        try:
            agent.run(case["goal"], strict=True)     # führt die gewrappten Tools aus
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
    else:
        calls = agent.complete(case["goal"]).get("function_calls") or []
        hop = 0
        while calls and hop < 4:                     # kein reset innerhalb des Goals
            results = []
            for c in calls:
                name, args = c["name"], c.get("arguments") or {}
                res = tools[name](**args) if name in tools else "unknown tool"
                results.append({"tool": name, "result": res})
            hop += 1
            calls = agent.complete(json.dumps(results, ensure_ascii=False)).get("function_calls") or []
    ms = round((time.perf_counter() - t0) * 1000)
    after = _db(store)
    after_snap = mut.snapshot(store)
    ok, why = _check(case, before, after, trace)
    writes = [t for t in trace if t["tool"] in
              ("calendar_create", "calendar_move", "calendar_delete")]
    failed = [t for t in writes if ("❌" in t["result"] or "Error" in t["result"]
                                     or "Fehler" in t["result"])]
    # real DB mutations (ground truth), not write-call attempts: a rejected
    # delete leaves the DB untouched and must NOT count as a wrong write;
    # an expected delete IS a wanted mutation (user review Sep 24).
    diff = mut.db_diff(before_snap, after_snap)
    if case["check"] == "absent":
        present_specs, absent = [], [case["want_title"]]
    else:
        present_specs, absent = [(case["want_title"], "", "", "")], []
    correct_mut, wrong_mut = mut.classify(diff, present_specs, absent)
    return {"id": case["id"], "mode": mode, "goal": case["goal"],
            "steps": len(trace), "calls": [t["tool"] for t in trace],
            "args": [t["args"] for t in trace],
            "goal_completed": ok, "why": why, "ms": ms, "error": error,
            "write_attempts": len(writes),
            "failed_write_attempts": len(failed),
            "successful_mutations": mut.mutation_count(diff),
            "correct_mutations": correct_mut, "wrong_mutations": wrong_mut,
            "db_before": before, "db_after": after,
            "db_before_snap": before_snap, "db_after_snap": after_snap,
            "trace": trace}


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
    summary = {"tag": args.tag, "mode": args.mode, "weights": weights, "n": n,
               "goal_completed": round(sum(r["goal_completed"] for r in rows) / n, 3),
               "write_attempts": sum(r["write_attempts"] for r in rows),
               "failed_write_attempts": sum(r["failed_write_attempts"] for r in rows),
               "successful_mutations": sum(r["successful_mutations"] for r in rows),
               "wrong_mutations": sum(r["wrong_mutations"] for r in rows),
               "median_steps": st.median(r["steps"] for r in rows),
               "median_ms": round(st.median(r["ms"] for r in rows)), "rows": rows}
    out = HERE / "reports" / f"native_agent_{args.tag}_{args.mode}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    for r in rows:
        print(f"  {'OK ' if r['goal_completed'] else 'FAIL'} {r['id']:<20} "
              f"steps={r['steps']} wr_att={r['write_attempts']} "
              f"succ_mut={r['successful_mutations']} wrong_mut={r['wrong_mutations']} "
              f"{r['ms']}ms {r['calls']} | {r['why']}"
              + (f" | err={r['error']}" if r["error"] else ""))
    print(f"[{args.tag}/{args.mode}] goal_completed {summary['goal_completed']} "
          f"· write_attempts {summary['write_attempts']} "
          f"· successful_mutations {summary['successful_mutations']} "
          f"· wrong_mutations {summary['wrong_mutations']} · median {summary['median_ms']}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
