#!/usr/bin/env python3
"""Architektur-Bakeoff: wie viel Gemma-Compute sparen wir bei gleicher Qualität?

Pipelines (unverändert daneben, nichts Bestehendes umgebaut):
  P0 hybrid    Gemma → N2-FT → Python            (Referenz; braucht laufendes Gemma)
  P1 direct    N3 → Python                        (maximale Rationalisierung)
  P2 fallback  N3 → Python → (nur bei Bedarf) Gemma → N2-FT → Python

Gemessen werden Business-Metriken, keine Submetriken:
  final_goal_ok · autonomous_ok · escalated (Gemma-Bedarf) · wrong_writes ·
  missed_actions · Latenz p50/p95 — plus Familien-Breakdown.

Deterministische Eskalationssignale (P2, KEIN semantischer Router): leerer Call,
unbekanntes Tool, leerer Titel bei Write, nicht eindeutig auflösbare Entity,
Create ohne Datum/Zeit, Ausführungsfehler.

Usage:
  NEEDLE_WEIGHTS=<n3.cact> PYTHONPATH=src .venv-ft3/bin/python \
      experiments/ft/arch_bench.py --pipeline direct --tag n3-v4-e5
  NEEDLE_WEIGHTS=<n3.cact> PYTHONPATH=src .venv-ft3/bin/python \
      experiments/ft/arch_bench.py --pipeline fallback --tag n3-v4-e5
"""
from __future__ import annotations

import argparse
import json
import os
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
from arch_cases import build_cases, _iso  # noqa: E402

WRITES = ("calendar_create", "calendar_move", "calendar_delete")
READS = ("calendar_list", "calendar_find_slot")


def _wrap(tools, trace):
    out = {}
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
        out[name] = w
    return out


def _events(store):
    return store.events_between(cal.now() - timedelta(days=3), cal.now() + timedelta(days=70))


def _match(ev, title, day, hm, persons):
    if title.lower() not in ev.title.lower():
        return False
    if day and ev.start.date().isoformat() != _iso(day):
        return False
    if hm and ev.start.strftime("%H:%M") != hm:
        return False
    if persons and not any(p.lower() in [x.lower() for x in ev.participants] for p in persons.split(",")):
        return False
    return True


def _check(expect, before, after, trace):
    writes = [t for t in trace if t["tool"] in WRITES]
    if expect.get("ambiguous") or expect.get("no_writes"):
        why = "Rückfrage nötig" if expect.get("ambiguous") else "kein Write"
        return (not writes), 0, len(writes), why
    missed = 0
    for (title, day, hm, p) in expect.get("present", []):
        if not any(_match(e, title, day, hm, p) for e in after):
            missed += 1
    for title in expect.get("absent", []):
        was_there = any(title.lower() in e.title.lower() for e in before)
        now_gone = not any(title.lower() in e.title.lower() for e in after)
        if not (was_there and now_gone):
            missed += 1
    expected_writes = len(expect.get("present", [])) + len(expect.get("absent", []))
    wrong = max(0, len(writes) - expected_writes)
    return (missed == 0), missed, wrong, ("ok" if missed == 0 else f"missed={missed}")


def _inspect(calls, tools, store):
    """P2: deterministisch prüfen, ob die vorgeschlagenen Calls selbst ausführbar sind."""
    if not calls:
        # Refusal/leer = SAFE, kein Write, kein Gemma nötig. (Der Fall wird nur
        # dann zum "missed action", wenn der Goal-Typ einen Write verlangte —
        # genau die Silent-Omission-Klasse, die P2 nicht detektieren kann.)
        return True, "no_calls_refusal"
    for c in calls:
        name = c["name"]
        args = c.get("arguments") or {}
        if name not in tools:
            return False, f"unknown_tool:{name}"
        title = str(args.get("title", "")).strip()
        if name in ("calendar_create", "calendar_move", "calendar_delete") and not title:
            return False, "empty_title"
        if name in ("calendar_move", "calendar_delete"):
            hits = [e for e in _events(store) if title.lower() in e.title.lower()]
            if len(hits) != 1:
                return False, f"unresolved_entity({len(hits)})"
        if name == "calendar_create" and not args.get("date") and not args.get("time"):
            return False, "no_date_time"
    return True, "executable"


def run_case(case, pipeline, weights, gemma=None) -> dict:
    td = Path(tempfile.mkdtemp())
    store = CalendarStore(td / "arch.db")
    raw = build_tools(store)
    trace: list[dict] = []
    tools = _wrap(raw, trace)
    for (title, day, hm, p) in case["fixture"]:
        raw["calendar_create"](title=title, date=day, time=hm, participants=p)
    before = _events(store)
    t0 = time.perf_counter()
    escalated, esc_reason = False, ""
    agent = needle.Needle(tools=list(tools.values()),
                          system=f"date: {cal.now().date()}",
                          weights=weights)
    agent.reset()
    try:
        calls = agent.complete(case["goal"]).get("function_calls") or []
    except Exception as exc:  # noqa: BLE001
        calls, escalated, esc_reason = [], True, f"inference_error:{type(exc).__name__}"

    if pipeline == "hybrid":
        raise SystemExit("P0 (hybrid) braucht den Agent/Gemma-Pfad — hier nicht implementiert; "
                         "siehe tests/test_e2e.py --hybrid")
    if pipeline == "fallback" and not escalated:
        ok_exec, esc_reason = _inspect(calls, tools, store)
        escalated = not ok_exec
    if escalated:
        # Kein Write ohne Klärung; im lokalen Setup ohne Gemma bleibt der Fall offen.
        after = _events(store)
        ok, missed, wrong, why = _check(case["expect"], before, after, trace)
        ok = False  # nicht autonom abgeschlossen
        gemma_note = "gemma_n/a" if gemma is None else "gemma"
        why = f"escalated({esc_reason}) · {gemma_note}"
    else:
        for c in calls[:6]:
            tools[c["name"]](**(c.get("arguments") or {})) if c["name"] in tools else None
        after = _events(store)
        ok, missed, wrong, why = _check(case["expect"], before, after, trace)
    ms = round((time.perf_counter() - t0) * 1000)
    return {"id": case["id"], "family": case["family"], "goal": case["goal"],
            "calls": [c["name"] for c in calls], "escalated": escalated,
            "esc_reason": esc_reason, "goal_ok": ok, "missed": missed,
            "wrong_writes": wrong, "ms": ms, "why": why,
            "writes": [t["tool"] for t in trace if t["tool"] in WRITES]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", choices=["direct", "fallback", "hybrid"], required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    weights = os.environ.get("NEEDLE_WEIGHTS") or None
    cases = build_cases()
    if args.limit:
        cases = cases[:args.limit]
    rows = [run_case(c, args.pipeline, weights) for c in cases]
    n = len(rows)

    def pct(sel):
        return round(sum(1 for r in rows if sel(r)) / n, 3)

    lats = [r["ms"] for r in rows]
    fams = {}
    for r in rows:
        f = fams.setdefault(r["family"], {"n": 0, "goal_ok": 0, "esc": 0, "wrong": 0, "missed": 0})
        f["n"] += 1
        f["goal_ok"] += r["goal_ok"]
        f["esc"] += r["escalated"]
        f["wrong"] += r["wrong_writes"]
        f["missed"] += r["missed"]
    summary = {"tag": args.tag, "pipeline": args.pipeline, "weights": weights, "n": n,
               "final_goal_ok": pct(lambda r: r["goal_ok"]),
               "autonomous_ok": pct(lambda r: r["goal_ok"] and not r["escalated"]),
               "gemma_invocation_rate": pct(lambda r: r["escalated"]),
               "wrong_writes": sum(r["wrong_writes"] for r in rows),
               "missed_actions": sum(r["missed"] for r in rows),
               "latency_p50_ms": round(st.median(lats)),
               "latency_p95_ms": round(sorted(lats)[int(n * 0.95) - 1]),
               "families": fams, "rows": rows}
    out = HERE / "reports" / f"arch_{args.tag}_{args.pipeline}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"[{args.tag}/{args.pipeline}] n={n} · goal_ok {summary['final_goal_ok']} "
          f"· autonomous {summary['autonomous_ok']} · gemma_rate {summary['gemma_invocation_rate']}"
          f" · wrong_writes {summary['wrong_writes']} · missed {summary['missed_actions']}"
          f" · p50 {summary['latency_p50_ms']}ms p95 {summary['latency_p95_ms']}ms")
    print("  Familien:")
    for fam, f in sorted(fams.items()):
        print(f"    {fam:<14} n={f['n']:<3} goal_ok {f['goal_ok']}/{f['n']} · esc {f['esc']}"
              f" · wrong {f['wrong']} · missed {f['missed']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
