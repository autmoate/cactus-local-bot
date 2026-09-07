"""Milestone 2+3 — Planner-Test: Frames → World State (ohne Needle).

Aufruf: uv run python needle-only/world/test_planner.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # needle-only/
sys.path.insert(0, str(HERE.parent.parent))   # Workspace-Root

import psycopg

from modules.config import load_config
from world import (
    SourceContext,
    WorldStore,
    WriteFrame,
)
from world.planner import WritePlanner
from world.resolver import Resolver


def _clean_db(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM world_datom")
        conn.execute("DELETE FROM world_tx")
        conn.commit()


def _apply(planner, frame, source=None):
    plan = planner.plan(frame)
    assert not plan.is_empty, f"Leerer Plan für {frame!r}: {plan.summary}"
    tx_id = planner.store.apply_plan(plan, source or SourceContext(
        source_type="tui", actor="ich", raw_text=str(frame)))
    return plan, tx_id


def main() -> int:
    cfg = load_config()
    store = WorldStore(cfg.database_url)
    _clean_db(cfg.database_url)
    resolver = Resolver(store)
    planner = WritePlanner(store, resolver)

    # ------------------------------------------------------------------
    # Case 1: „Morgen um 14 Uhr Zahnarzt." → Event
    # ------------------------------------------------------------------
    plan, tx1 = _apply(planner, WriteFrame(
        subject="Zahnarzt", when="morgen um 14 Uhr"))
    events = store.find_entities([("sys/type", "value", "event")])
    assert len(events) == 1, f"erwartet 1 Event, got {len(events)}"
    ent = store.get_entity(events[0])
    assert ent["attributes"]["sys/name"] == ["Zahnarzt"]
    start = ent["attributes"]["time/start"][0]
    assert "14:00:00" in start, f"Start falsch: {start}"
    print(f"✓ Case 1: Event 'Zahnarzt' morgen 14:00 (tx{tx1})")

    # ------------------------------------------------------------------
    # Case 2: „Verschieb Zahnarzt auf 15 Uhr." → gleiches Event, neuer Start
    # ------------------------------------------------------------------
    plan, tx2 = _apply(planner, WriteFrame(
        subject="Zahnarzt", relation="verschieben", when="morgen um 15 Uhr"))
    events2 = store.find_entities([("sys/type", "value", "event")])
    assert events2 == events, "Event-Entity sollte dieselbe bleiben"
    ent2 = store.get_entity(events[0])
    start2 = ent2["attributes"]["time/start"][0]
    assert "15:00:00" in start2, f"Start falsch: {start2}"
    # History: alter + neuer Start sichtbar
    hist = store.history(events[0])
    starts = [d for d in hist if d.attribute == "time/start"]
    assert len(starts) == 3, \
        f"expected 3 time/start datoms (1 add + 1 retract + 1 add), got {len(starts)}"
    print(f"✓ Case 2: 'Zahnarzt' auf 15:00 verschoben (tx{tx2})")

    # ------------------------------------------------------------------
    # Case 3: „Julia bringt beim Treffen den Beamer." → Commitment
    # ------------------------------------------------------------------
    plan, tx3 = _apply(planner, WriteFrame(
        subject="Julia", relation="bringen", object="Beamer",
        context="Treffen"))
    commitments = store.find_entities([
        ("sys/type", "value", "commitment")])
    assert len(commitments) == 1, \
        f"erwartet 1 Commitment, got {len(commitments)}"
    cent = store.get_entity(commitments[0])
    assert cent["attributes"]["org/action"] == ["bring"]
    actor_id = cent["attributes"]["org/actor"][0]
    actor_ent = store.get_entity(actor_id)
    assert actor_ent["attributes"]["sys/name"] == ["Julia"], \
        f"Actor falsch: {actor_ent}"
    print(f"✓ Case 3: Commitment — Julia bringt Beamer (tx{tx3})")

    # ------------------------------------------------------------------
    # Case 4: „Jana übernimmt doch den Beamer." → Replace
    # ------------------------------------------------------------------
    plan, tx4 = _apply(planner, WriteFrame(
        subject="Jana", relation="übernehmen", object="Beamer"))
    commitments2 = store.find_entities([
        ("sys/type", "value", "commitment")])
    assert commitments2 == commitments, \
        "Commitment-Entity sollte dieselbe bleiben"
    cent2 = store.get_entity(commitments[0])
    actor2_id = cent2["attributes"]["org/actor"][0]
    actor2_ent = store.get_entity(actor2_id)
    assert actor2_ent["attributes"]["sys/name"] == ["Jana"], \
        f"Actor nach Replace falsch: {actor2_ent}"
    print(f"✓ Case 4: Commitment-Actor — Julia → Jana (tx{tx4})")

    # ------------------------------------------------------------------
    # Case 5 (Undo nach Case 4): Actor wieder Julia
    # ------------------------------------------------------------------
    tx5 = store.undo_last()
    cent3 = store.get_entity(commitments[0])
    actor3_id = cent3["attributes"]["org/actor"][0]
    actor3_ent = store.get_entity(actor3_id)
    assert actor3_ent["attributes"]["sys/name"] == ["Julia"], \
        f"nach undo sollte Julia actor sein, got {actor3_ent}"
    print(f"✓ Case 5: Undo — Actor wieder Julia (tx{tx5})")

    # ------------------------------------------------------------------
    # Case 6: „Lisa kommt zum Treffen." → Participant am Event
    # ------------------------------------------------------------------
    # Treffen-Event anlegen
    _apply(planner, WriteFrame(subject="Treffen", when="übermorgen um 10 Uhr"))
    plan, tx6 = _apply(planner, WriteFrame(
        subject="Lisa", relation="kommen", context="Treffen"))
    treffen = store.find_entities([
        ("sys/name", "value", "Treffen")])
    assert len(treffen) == 1, "Treffen-Event nicht gefunden"
    tent = store.get_entity(treffen[0])
    parts = tent["attributes"].get("org/participant", [])
    assert len(parts) == 1, f"erwartet 1 Participant, got {len(parts)}"
    part_ent = store.get_entity(parts[0])
    assert part_ent["attributes"]["sys/name"] == ["Lisa"], \
        f"Participant falsch: {part_ent}"
    print(f"✓ Case 6: Participant — Lisa kommt zum Treffen (tx{tx6})")

    # ------------------------------------------------------------------
    # Case 7: „Der WLAN-Code ist foo123." → Fact
    # ------------------------------------------------------------------
    plan, tx7 = _apply(planner, WriteFrame(
        subject="WLAN-Code", relation="ist", object="foo123"))
    facts = store.find_entities([("sys/type", "value", "fact")])
    assert len(facts) == 1, f"erwartet 1 Fact, got {len(facts)}"
    fent = store.get_entity(facts[0])
    assert fent["attributes"]["sys/name"] == ["WLAN-Code"]
    assert fent["attributes"]["note/body"] == ["foo123"], \
        f"note/body falsch: {fent}"
    print(f"✓ Case 7: Fact — WLAN-Code = foo123 (tx{tx7})")

    # ------------------------------------------------------------------
    # Case 8: „Erinnere mich 3 Stunden vorher." → Reminder am Event
    # ------------------------------------------------------------------
    plan, tx8 = _apply(planner, WriteFrame(
        relation="erinnern", when="3 Stunden vorher",
        context="Treffen"))
    reminders = store.find_entities([("sys/type", "value", "reminder")])
    assert len(reminders) == 1, f"erwartet 1 Reminder, got {len(reminders)}"
    rent = store.get_entity(reminders[0])
    assert rent["attributes"]["org/status"] == ["active"]
    due = rent["attributes"]["time/due"][0]
    # Event ist übermorgen 10:00 → due = 07:00
    assert "07:00:00" in due, f"due falsch: {due} (Event 10:00, 3h vorher)"
    print(f"✓ Case 8: Reminder — 3h vor Treffen, due={due} (tx{tx8})")

    print()
    print("✅ Milestone 2+3: Planner-Test erfolgreich")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
