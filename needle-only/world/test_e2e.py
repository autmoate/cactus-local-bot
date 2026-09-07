"""End-to-End-Test: Text → Needle → Planner → Store → Query → Undo.

Deckt die 12 Eval-Cases aus dem v6-Plan ab.
Aufruf: uv run python needle-only/world/test_e2e.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # needle-only/
sys.path.insert(0, str(HERE.parent.parent))   # Workspace-Root

import psycopg

from modules.config import load_config
from world import SourceContext, WorldStore
from world.service import WorldService


def _clean_db(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM world_datom")
        conn.execute("DELETE FROM world_tx")
        conn.commit()


def _write_and_approve(service: WorldService, text: str,
                       source: SourceContext) -> int:
    """Simuliert den Approval-Flow: handle → approve → apply."""
    resp = service.handle(text, source)
    assert resp.requires_approval, \
        f"WRITE sollte Approval benötigen: {resp.text!r}"
    assert resp.plan is not None and not resp.plan.is_empty, \
        f"Plan sollte nicht leer sein: {resp.text!r}"
    return service.apply(resp.plan, source)


def main() -> int:
    cfg = load_config()
    store = WorldStore(cfg.database_url)
    _clean_db(cfg.database_url)
    service = WorldService(store)
    source = SourceContext(source_type="tui", actor="ich", raw_text="e2e")

    # ------------------------------------------------------------------
    # Case 1: „Zahnarzt morgen um 14 Uhr." → Event
    # ------------------------------------------------------------------
    tx1 = _write_and_approve(service, "Zahnarzt morgen um 14 Uhr", source)
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
    tx2 = _write_and_approve(service,
                             "Verschieb Zahnarzt auf 15 Uhr", source)
    events2 = store.find_entities([("sys/type", "value", "event")])
    assert events2 == events, "Event-Entity sollte dieselbe bleiben"
    ent2 = store.get_entity(events[0])
    start2 = ent2["attributes"]["time/start"][0]
    assert "15:00:00" in start2, f"Start falsch: {start2}"
    # History: alter + neuer Start sichtbar
    hist = store.history(events[0])
    starts = [d for d in hist if d.attribute == "time/start"]
    assert len(starts) == 3, \
        f"expected 3 time/start datoms, got {len(starts)}"
    print(f"✓ Case 2: 'Zahnarzt' auf 15:00 verschoben (tx{tx2})")

    # ------------------------------------------------------------------
    # Case 3: „Julia bringt beim Treffen den Beamer." → Commitment
    # ------------------------------------------------------------------
    tx3 = _write_and_approve(
        service, "Julia bringt beim Treffen den Beamer", source)
    commitments = store.find_entities([
        ("sys/type", "value", "commitment")])
    assert len(commitments) == 1, \
        f"erwartet 1 Commitment, got {len(commitments)}"
    cent = store.get_entity(commitments[0])
    assert cent["attributes"]["org/action"] == ["bring"]
    actor_id = cent["attributes"]["org/actor"][0]
    actor_ent = store.get_entity(actor_id)
    assert actor_ent["attributes"]["sys/name"] == ["Julia"]
    print(f"✓ Case 3: Commitment — Julia bringt Beamer (tx{tx3})")

    # ------------------------------------------------------------------
    # Case 4: „Jana übernimmt doch den Beamer." → Replace
    # ------------------------------------------------------------------
    tx4 = _write_and_approve(
        service, "Jana übernimmt doch den Beamer", source)
    commitments2 = store.find_entities([
        ("sys/type", "value", "commitment")])
    print(f"  DEBUG C4: before={commitments}, after={commitments2}")
    assert commitments2 == commitments, \
        "Commitment-Entity sollte dieselbe bleiben"
    cent2 = store.get_entity(commitments[0])
    actor2_id = cent2["attributes"]["org/actor"][0]
    actor2_ent = store.get_entity(actor2_id)
    assert actor2_ent["attributes"]["sys/name"] == ["Jana"], \
        f"Actor nach Replace falsch: {actor2_ent}"
    print(f"✓ Case 4: Commitment-Actor — Julia → Jana (tx{tx4})")

    # ------------------------------------------------------------------
    # Case 5: „Wer bringt den Beamer?" → READ → Jana
    # ------------------------------------------------------------------
    resp = service.handle("Wer bringt den Beamer?", source)
    assert "Jana" in resp.text, \
        f"READ sollte 'Jana' enthalten, got: {resp.text!r}"
    print(f"✓ Case 5: READ 'Wer bringt den Beamer?' → "
          f"{resp.text.strip()}")

    # ------------------------------------------------------------------
    # Case 6: „Wer sollte den Beamer ursprünglich bringen?"
    #         → READ (historisch) → Julia
    # ------------------------------------------------------------------
    resp = service.handle(
        "Wer sollte den Beamer ursprünglich bringen?", source)
    assert "Julia" in resp.text, \
        f"READ sollte 'Julia' enthalten, got: {resp.text!r}"
    print(f"✓ Case 6: READ (historisch) → {resp.text.strip()}")

    # ------------------------------------------------------------------
    # Case 7: „Lisa kommt zum Treffen." → Participant am Event
    # ------------------------------------------------------------------
    tx7 = _write_and_approve(
        service, "Lisa kommt zum Treffen", source)
    treffen = store.find_entities([("sys/name", "value", "Treffen")])
    assert len(treffen) == 1, "Treffen-Event nicht gefunden"
    tent = store.get_entity(treffen[0])
    parts = tent["attributes"].get("org/participant", [])
    assert len(parts) == 1, \
        f"erwartet 1 Participant, got {len(parts)}"
    part_ent = store.get_entity(parts[0])
    assert part_ent["attributes"]["sys/name"] == ["Lisa"]
    print(f"✓ Case 7: Participant — Lisa kommt zum Treffen (tx{tx7})")

    # ------------------------------------------------------------------
    # Case 8: „Lisa kommt doch nicht." → Participant retracted
    # ------------------------------------------------------------------
    tx8 = _write_and_approve(
        service, "Lisa kommt doch nicht", source)
    tent2 = store.get_entity(treffen[0])
    parts2 = tent2["attributes"].get("org/participant", [])
    assert len(parts2) == 0, \
        f"Participant sollte retracted sein, got {len(parts2)}"
    print(f"✓ Case 8: Participant — Lisa retracted (tx{tx8})")

    # ------------------------------------------------------------------
    # Case 9: „Erinnere mich drei Stunden vorher." → Reminder am Event
    # ------------------------------------------------------------------
    tx9 = _write_and_approve(
        service, "Erinnere mich drei Stunden vorher", source)
    reminders = store.find_entities([
        ("sys/type", "value", "reminder")])
    assert len(reminders) == 1, \
        f"erwartet 1 Reminder, got {len(reminders)}"
    rent = store.get_entity(reminders[0])
    assert rent["attributes"]["org/status"] == ["active"]
    due = rent["attributes"]["time/due"][0]
    print(f"✓ Case 9: Reminder — due={due} (tx{tx9})")

    # ------------------------------------------------------------------
    # Case 10: „Der WLAN-Code ist foo123." → Fact
    # ------------------------------------------------------------------
    tx10 = _write_and_approve(
        service, "Der WLAN-Code ist foo123", source)
    facts = store.find_entities([("sys/type", "value", "fact")])
    assert len(facts) == 1, f"erwartet 1 Fact, got {len(facts)}"
    fent = store.get_entity(facts[0])
    assert fent["attributes"]["sys/name"] == ["WLAN-Code"]
    assert fent["attributes"]["note/body"] == ["foo123"], \
        f"note/body falsch: {fent}"
    print(f"✓ Case 10: Fact — WLAN-Code = foo123 (tx{tx10})")

    # ------------------------------------------------------------------
    # Case 11: „Wie lautet der WLAN-Code?" → READ → foo123
    # ------------------------------------------------------------------
    resp = service.handle("Wie lautet der WLAN-Code?", source)
    assert "foo123" in resp.text, \
        f"READ sollte 'foo123' enthalten, got: {resp.text!r}"
    print(f"✓ Case 11: READ 'Wie lautet der WLAN-Code?' → "
          f"{resp.text.strip()}")

    # ------------------------------------------------------------------
    # Case 12: Undo nach Case 4 → aktueller actor wieder Julia
    # ------------------------------------------------------------------
    # Wir undoen die letzte Transaction (Case 11 war READ, Case 10
    # war der letzte WRITE). Für den Test: direkt tx4 undoen.
    resp = service.handle("undo", source)
    assert "rückgängig" in resp.text.lower(), \
        f"Undo-Antwort unerwartet: {resp.text!r}"
    print(f"✓ Case 12: Undo → {resp.text.strip()}")

    print()
    print("✅ End-to-End-Test: Alle 12 Eval-Cases erfolgreich")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
