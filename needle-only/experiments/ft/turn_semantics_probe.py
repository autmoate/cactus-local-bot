#!/usr/bin/env python3
"""Turn-Semantik-Probe (Plan Phase 8/9).

Zwei Fragen, KEINE Produktivmigration:

A) Dependent chains (Phase 8): reset() genau EINMAL pro logischem User-Ziel,
   dann complete → execute → complete(result) → execute … (kein reset dazwischen).
   Verglichen: manueller Loop vs. `run()` — mit ECHTEN Tool-Callables (nicht nur
   JSON-Schemas), sonst kann run() nicht ausführen.

B) Kontext-Isolation (Phase 9): unabhängige User-Turns dürfen sich NICHT
   gegenseitig beeinflussen. Zwei Läufe: MIT reset() vor jedem Turn (korrekt)
   und OHNE (Colab-Gotcha) — der Vergleich belegt die Regel.

Lauf:  NEEDLE_WEIGHTS=<cact> PYTHONPATH=src ../.venv-ft3/bin/python \
           experiments/ft/turn_semantics_probe.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NO = HERE.parent
sys.path.insert(0, str(NO / "src"))

import needle  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import build_tools, system_facts  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402


def _new(tmp: Path):
    store = CalendarStore(tmp / "turn.db")
    tools = build_tools(store)
    agent = needle.Needle(tools=list(tools.values()), system=system_facts(),
                          weights=__import__("os").environ.get("NEEDLE_WEIGHTS") or None)
    return store, tools, agent


def _titles(store) -> list[str]:
    return sorted(e.title for e in store.events_between(
        cal.now() - cal.timedelta(days=2), cal.now() + cal.timedelta(days=60)))


def _exec(tools, calls) -> list[dict]:
    """Führt Needle-Calls über die echten Tool-Funktionen aus."""
    out = []
    for c in calls:
        name, args = c["name"], c.get("arguments") or {}
        try:
            res = tools[name](**args)
        except Exception as exc:  # noqa: BLE001
            res = f"{type(exc).__name__}: {exc}"
        out.append({"tool": name, "result": res})
    return out


def dependent_manual(goal: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        store, tools, agent = _new(Path(td))
        # Fixture für move/delete-Fälle schaffen (sonst gibt es nichts zu finden)
        tools["calendar_create"](title="Zahnarzt", date="morgen", time="9 Uhr")
        tools["calendar_create"](title="Meeting", date="morgen", time="15 Uhr")
        agent.reset()
        calls = agent.complete(goal).get("function_calls") or []
        steps = [{"calls": [c["name"] for c in calls]}]
        hop = 0
        while calls and hop < 3:            # KEIN reset innerhalb des Ziels
            results = _exec(tools, calls)
            steps[-1]["results"] = [r["result"][:60] for r in results]
            calls = agent.complete(json.dumps(results, ensure_ascii=False)).get("function_calls") or []
            if calls:
                steps.append({"calls": [c["name"] for c in calls]})
            hop += 1
        return {"mode": "manual", "goal": goal, "steps": steps, "db": _titles(store)}


def dependent_run(goal: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        store, tools, agent = _new(Path(td))
        tools["calendar_create"](title="Zahnarzt", date="morgen", time="9 Uhr")
        tools["calendar_create"](title="Meeting", date="morgen", time="15 Uhr")
        agent.reset()
        try:
            resp = agent.run(goal)
            out = {"results": resp.get("results"), "calls": [c["name"] for c in
                   (resp.get("function_calls") or [])]}
        except Exception as exc:  # noqa: BLE001
            out = {"error": f"{type(exc).__name__}: {exc}"}
        return {"mode": "run()", "goal": goal, **out, "db": _titles(store)}


def isolation(with_reset: bool) -> dict:
    """Drei unabhängige Turns (Chats A/B/C). Ohne reset darf NICHTS leaken —
    weder Tool-Wahl noch Argumente (Titel)."""
    with tempfile.TemporaryDirectory() as td:
        store, tools, agent = _new(Path(td))
        def turn(text: str):
            if with_reset:
                agent.reset()
            calls = agent.complete(text).get("function_calls") or []
            _exec(tools, calls)
            return [(c["name"], c.get("arguments") or {}) for c in calls]
        a = turn("Trag morgen 10 Uhr Zahnarzt ein")
        b = turn("Wie wird das Wetter morgen?")
        c = turn("Lösch Meeting")

        def titles(t): return {str(v).lower() for _, args in t for v in args.values()}
        def tools_of(t): return [n for n, _ in t]

        def foreign(title_set: set, own_text: str) -> set:
            # Titel aus A, die NICHT im eigenen Turn-Text vorkommen = echter Leak
            return {x for x in title_set if x not in own_text.lower()}
        leak_b = foreign(titles(a), "Wie wird das Wetter morgen?") & titles(b)
        leak_c = foreign(titles(a), "Lösch Meeting") & titles(c)
        return {"with_reset": with_reset, "A": tools_of(a), "B": tools_of(b),
                "C": tools_of(c), "titles_A": sorted(titles(a)),
                "titles_C": sorted(titles(c)),
                "foreign_leak_A_in_B_or_C": sorted(leak_b | leak_c),
                "db": _titles(store)}


def main() -> int:
    import os
    print(f"weights={os.environ.get('NEEDLE_WEIGHTS') or 'BASE'}")
    print("\n=== A) Dependent chains (reset 1x pro Goal, dann result-getrieben) ===")
    for goal in ("Finde morgen einen freien Slot und trag dort Meeting ein",
                 "Verschieb Zahnarzt auf Freitag",
                 "Lösch Meeting"):
        print("  --", goal)
        print("     manual:", json.dumps(dependent_manual(goal), ensure_ascii=False))
        print("     run() :", json.dumps(dependent_run(goal), ensure_ascii=False))
    print("\n=== B) Kontext-Isolation (unabhängige Turns) ===")
    print("  MIT reset() :", json.dumps(isolation(True), ensure_ascii=False))
    print("  OHNE reset():", json.dumps(isolation(False), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
