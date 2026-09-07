"""Milestone 1 — Julia→Jana-Beamer-Zyklus (hartkodiert, ohne Needle).

Aufruf: uv run python needle-only/world/test_store.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # needle-only/ (für world)
sys.path.insert(0, str(HERE.parent.parent))   # Workspace-Root (für modules)

import psycopg

from modules.config import load_config
from world import (
    CardinalityViolation,
    DatomChange,
    SourceContext,
    UndoConflict,
    WorldStore,
)


def _clean_db(url: str) -> None:
    with psycopg.connect(url) as conn:
        conn.execute("DELETE FROM world_datom")
        conn.execute("DELETE FROM world_tx")
        conn.commit()


def main() -> int:
    cfg = load_config()
    store = WorldStore(cfg.database_url)  # legt Schema an, falls neu
    _clean_db(cfg.database_url)

    julia = uuid4()
    beamer = uuid4()
    commitment = uuid4()

    # ------------------------------------------------------------------
    # Schritt 1: „Julia bringt den Beamer."
    # ------------------------------------------------------------------
    tx1 = store.create_tx(SourceContext(
        source_type="tui", actor="ich",
        raw_text="Julia bringt den Beamer.",
    ))
    store.append_datoms(tx1, [
        DatomChange(entity_id=julia, attribute="sys/type", value="person"),
        DatomChange(entity_id=julia, attribute="sys/name", value="Julia"),
        DatomChange(entity_id=beamer, attribute="sys/type", value="resource"),
        DatomChange(entity_id=beamer, attribute="sys/name", value="Beamer"),
        DatomChange(entity_id=commitment, attribute="sys/type",
                    value="commitment"),
        DatomChange(entity_id=commitment, attribute="org/actor",
                    ref_entity=julia),
        DatomChange(entity_id=commitment, attribute="org/action",
                    value="bring"),
        DatomChange(entity_id=commitment, attribute="org/object",
                    ref_entity=beamer),
    ])
    print(f"✓ Schritt 1: tx{tx1} — Julia bringt den Beamer")

    # ------------------------------------------------------------------
    # Schritt 2: Current State abfragen
    # ------------------------------------------------------------------
    entity = store.get_entity(commitment)
    assert entity["attributes"]["org/action"] == ["bring"], "action fehlt"
    assert entity["attributes"]["org/actor"] == [julia], "actor != Julia"
    assert entity["attributes"]["org/object"] == [beamer], "object != Beamer"
    print("✓ Schritt 2: Current State — actor=Julia, action=bring")

    # ------------------------------------------------------------------
    # Schritt 3: „Jana übernimmt doch den Beamer."
    # ------------------------------------------------------------------
    jana = uuid4()
    tx2 = store.create_tx(SourceContext(
        source_type="tui", actor="ich",
        raw_text="Jana übernimmt doch den Beamer.",
    ))
    store.append_datoms(tx2, [
        DatomChange(entity_id=jana, attribute="sys/type", value="person"),
        DatomChange(entity_id=jana, attribute="sys/name", value="Jana"),
        # Replace: Julia retracten, Jana hinzufügen
        DatomChange(entity_id=commitment, attribute="org/actor",
                    ref_entity=julia, added=False),
        DatomChange(entity_id=commitment, attribute="org/actor",
                    ref_entity=jana, added=True),
    ])
    print(f"✓ Schritt 3: tx{tx2} — Jana übernimmt")

    # ------------------------------------------------------------------
    # Schritt 4: Current State
    # ------------------------------------------------------------------
    entity = store.get_entity(commitment)
    assert entity["attributes"]["org/actor"] == [jana], "actor != Jana"
    print("✓ Schritt 4: Current State — actor=Jana")

    # ------------------------------------------------------------------
    # Schritt 5: History + historische Query
    # ------------------------------------------------------------------
    hist = store.history(commitment)
    assert len(hist) == 6, f"erwartet 6 Datoms (4+2), got {len(hist)}"
    original = store.as_of(commitment, "org/actor", tx_id=tx1)
    assert original == julia, "as_of(tx1) sollte Julia sein"
    current = store.as_of(commitment, "org/actor", tx_id=tx2)
    assert current == jana, "as_of(tx2) sollte Jana sein"
    print("✓ Schritt 5: History — 6 Datoms, as_of(tx1)=Julia, "
          "as_of(tx2)=Jana")

    # ------------------------------------------------------------------
    # Schritt 6: Undo (letzte Transaction)
    # ------------------------------------------------------------------
    tx3 = store.undo_last()
    assert tx3 > tx2, "undo sollte neue tx erzeugen"
    entity = store.get_entity(commitment)
    assert entity["attributes"]["org/actor"] == [julia], \
        "nach undo sollte Julia actor sein"
    hist = store.history(commitment)
    assert len(hist) == 8, f"nach undo: 8 Datoms erwartet, got {len(hist)}"
    print(f"✓ Schritt 6: Undo tx{tx3} — actor=Julia, History auf 8 gewachsen")

    # ------------------------------------------------------------------
    # Schritt 7: Undo-Konflikt (historische tx1)
    # ------------------------------------------------------------------
    try:
        store.undo_tx(tx1)
        raise AssertionError("undo_tx(tx1) sollte UndoConflict werfen")
    except UndoConflict:
        pass
    print("✓ Schritt 7: undo_tx(tx1) wirft korrekt UndoConflict")

    # ------------------------------------------------------------------
    # Bonus 1: Cardinality-Verletzung wird abgefangen
    # ------------------------------------------------------------------
    lisa = uuid4()
    tx4 = store.create_tx(SourceContext(raw_text="Lisa übernimmt"))
    try:
        store.append_datoms(tx4, [
            DatomChange(entity_id=commitment, attribute="org/actor",
                        ref_entity=lisa, added=True),
        ])
        raise AssertionError("sollte CardinalityViolation werfen")
    except CardinalityViolation:
        pass
    print("✓ Bonus 1: Cardinality-Verletzung korrekt abgefangen")

    # ------------------------------------------------------------------
    # Bonus 2: Entity per Alias finden (cardinality=many)
    # ------------------------------------------------------------------
    tx5 = store.create_tx(SourceContext(raw_text="Alias-Test"))
    store.append_datoms(tx5, [
        DatomChange(entity_id=beamer, attribute="sys/alias",
                    value="Projektor", added=True),
        DatomChange(entity_id=beamer, attribute="sys/alias",
                    value="Präsentator", added=True),
    ])
    found = store.find_entities_by_name("projektor")
    assert len(found) == 1 and found[0]["entity_id"] == beamer, \
        "Alias 'Projektor' nicht gefunden"
    found = store.find_entities_by_name("präsentator")
    assert len(found) == 1 and found[0]["entity_id"] == beamer, \
        "Alias 'Präsentator' nicht gefunden"
    print("✓ Bonus 2: Beamer via Aliase 'Projektor'/'Präsentator' gefunden")

    # ------------------------------------------------------------------
    # Bonus 3: Finden per Name (Julia, case-insensitive)
    # ------------------------------------------------------------------
    found = store.find_entities_by_name("julia")
    assert len(found) == 1 and found[0]["entity_id"] == julia, \
        "Julia nicht per Name gefunden"
    print("✓ Bonus 3: Julia per Name (case-insensitive) gefunden")

    print()
    print("✅ Milestone 1: Julia→Jana-Beamer-Zyklus erfolgreich")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
