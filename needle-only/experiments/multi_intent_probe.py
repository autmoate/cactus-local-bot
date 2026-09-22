#!/usr/bin/env python3
"""Multi-Intent-Probe (Phase C): reale Nachrichten → Trace, KEINE Fixes.

Zeigt pro Fall: Needle-Calls, ausgeführte Calls und den finalen DB-Zustand
(was angelegt wurde vs. was der Nutzer verlangt hat). Läuft im needle-Mode
(kein Gemma nötig); mit NEEDLE_WEIGHTS wird das FT-Modell gemessen, ohne Base.

Usage:
  PYTHONPATH=src .venv-ft/bin/python -m experiments.multi_intent_probe
  NEEDLE_WEIGHTS=experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact \
    PYTHONPATH=src .venv-ft/bin/python -m experiments.multi_intent_probe
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEEDLE_ONLY = HERE.parent
sys.path.insert(0, str(NEEDLE_ONLY / "src"))
sys.path.insert(0, str(NEEDLE_ONLY))

from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import Agent  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402

CASES = [
    "Trag morgen Zahnarzt 10 Uhr und Meeting 14 Uhr ein.",
    "Mach am Freitag:\n- 9 Uhr Zahnarzt\n- 12 Uhr Mittagessen mit Lisa\n- 16 Uhr TÜV",
    "Montag Teammeeting 9, Dienstag Arzt 11, Freitag Urlaub",
    "Verschieb Zahnarzt auf Freitag und lösch danach Meeting.",
]


def run_case(text: str) -> None:
    store = CalendarStore(Path(tempfile.mkdtemp()) / "probe.db")
    agent = Agent(store, mode="needle")
    final = None
    for final in agent.handle(text, session_id="probe"):
        pass
    calls = []
    for step in final["steps"]:
        if step["name"].startswith("controller"):
            calls.append(f"{step['name']}: {json.dumps(step['output'], ensure_ascii=False)[:90]}")
    needle_out = next((s["output"] for s in final["steps"]
                       if s["name"] == "needle_complete"), {}) or {}
    fc = needle_out.get("function_calls") or []
    events = store.events_between(cal.now() - cal.timedelta(days=400),
                                  cal.now() + cal.timedelta(days=400))
    print(f"\nIN : {text!r}")
    print(f" needle_calls={len(fc)}: " +
          "; ".join(f"{c['name']}({','.join(c.get('arguments', {}))})" for c in fc))
    print(f" executed={final['executed']} | result={final['result'][:110]!r}")
    print(f" DB events ({len(events)}): " +
          "; ".join(f"{e.title}@{e.start.date()} {e.start.strftime('%H:%M')}" for e in events))


def main() -> int:
    w = os.environ.get("NEEDLE_WEIGHTS")
    print(f"mode=needle · weights={w or 'BASE'}", flush=True)
    for text in CASES:
        run_case(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
