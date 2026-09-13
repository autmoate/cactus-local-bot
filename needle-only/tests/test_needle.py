"""Needle 2 integration tests: base model, English + German (plan §45).

Base needle2 is variance-prone across trivial prompt perturbations (measured;
see README), so this suite asserts the plan §45 metrics at suite level:
tool accuracy >= 70% in needle-only mode, with per-case details printed.
Run: uv run pytest -m needle
"""

import pytest

from local_calendar.agent import Agent
from local_calendar.calendar import CalendarStore

pytestmark = pytest.mark.needle

CASES = [
    # (input, expected tool or None, must-execute) — distinct slots, no mutual collisions
    ("Create a dentist appointment tomorrow at 14:00", "calendar_create", True),
    ("Termin Friseur morgen um 10 Uhr", "calendar_create", True),
    ("Create a dentist appointment next tuesday afternoon", "calendar_create", True),
    ("Show my appointments next week", "calendar_list", True),
    ("Zeig meine Termine nächste Woche", "calendar_list", True),
    ("I am on vacation from August 3 to August 18", "calendar_create", True),
    ("When are Lisa and Max free tomorrow afternoon?", "calendar_find_slot", True),
    ("Wie wird das Wetter morgen?", None, False),
]


def _run(agent: Agent, text: str) -> dict:
    final = None
    for trace in agent.handle(text):
        final = trace
    assert final is not None
    return final


def test_needle_integration(tmp_path, capsys):
    store = CalendarStore(tmp_path / "cal.db")
    agent = Agent(store, mode="needle")
    rows = []
    for text, want_tool, should_exec in CASES:
        final = _run(agent, text)
        call = next((s for s in final["steps"] if s["name"] == "needle_complete"), None)
        got = ((call or {}).get("output") or {}).get("function_calls") or []
        tool = (got[0].get("name") if got else None)
        ok = (tool == want_tool if want_tool else tool is None) \
            and final.get("executed", False) == should_exec
        rows.append((text, tool, ok))
        print(f"{'OK ' if ok else 'FAIL'} {text!r:52} -> {tool} "
              f"conf={final.get('confidence')} executed={final.get('executed')}")
    hits = sum(1 for *_, ok in rows if ok)
    print(f"tool accuracy: {hits}/{len(rows)}")
    assert hits >= 0.7 * len(rows), rows
