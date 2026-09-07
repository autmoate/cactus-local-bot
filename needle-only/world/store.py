"""Orga v6 — Postgres World Store.

Append-only: Datoms werden NIE geupdatet oder gelöscht.
Der aktuelle Zustand entsteht aus der world_current_datoms-View.

Kern-Invarianten (werden hier durchgesetzt):
- Append-only: kein UPDATE/DELETE auf world_datom
- Cardinality-one: ein neuer aktiver Wert erfordert,
  dass alle bisher aktiven Werte desselben Attributes
  in derselben Transaction retractet werden
- Undo ist immer eine NEUE Transaction (nie ein Löschvorgang)
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from .model import (
    DatomChange,
    DatomRecord,
    SourceContext,
    TransactionPlan,
    TxRecord,
)

_SCHEMA = Path(__file__).resolve().parent / "schema.sql"


class WorldStoreError(Exception):
    """Fehler im World Store."""


class CardinalityViolation(WorldStoreError):
    """cardinality=one verletzt: alter aktiver Wert nicht retractet."""


class UndoConflict(WorldStoreError):
    """Undo nicht möglich: spätere Transactions berühren dieselben
    (entity, attribute)-Paare."""


def _record(row: dict) -> DatomRecord:
    value = row.get("value")
    if value is not None and not isinstance(value, (str, int, float, bool)):
        # jsonb kann auch Objekte/Arrays sein → JSON-Serialisierbarkeit sicherstellen
        value = json.loads(json.dumps(value, default=str))
    return DatomRecord(
        id=row["id"],
        entity_id=row["entity_id"],
        attribute=row["attribute"],
        value=value,
        ref_entity=row.get("ref_entity"),
        tx_id=row["tx_id"],
        added=row["added"],
    )


class WorldStore:
    """Postgres-Store für das World Model (append-only)."""

    def __init__(self, url: str):
        self.url = url
        self._attribute_cache: dict[str, dict] | None = None
        self._init_schema()

    # ------------------------------------------------------------------
    # Infrastruktur
    # ------------------------------------------------------------------

    def _connect(self):
        return psycopg.connect(self.url, row_factory=dict_row)

    def _init_schema(self) -> None:
        sql = _SCHEMA.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.execute(sql)
            conn.commit()

    # ------------------------------------------------------------------
    # Attribut-Vokabular (world_attribute)
    # ------------------------------------------------------------------

    def _attribute_specs(self) -> dict[str, dict]:
        if self._attribute_cache is None:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT name, value_kind, cardinality FROM world_attribute"
                ).fetchall()
            self._attribute_cache = {
                r["name"]: {
                    "value_kind": r["value_kind"],
                    "cardinality": r["cardinality"],
                }
                for r in rows
            }
        return self._attribute_cache

    def refresh_attribute_cache(self) -> None:
        self._attribute_cache = None
        self._attribute_specs()

    # ------------------------------------------------------------------
    # Transaktionen (world_tx)
    # ------------------------------------------------------------------

    def create_tx(self, source: SourceContext) -> int:
        """Legt eine neue (leere) Transaction an. Liefert die tx_id."""
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO world_tx
                    (actor, device_id, scope_id, source_type, source_id,
                     raw_text, metadata)
                VALUES (%s, %s, NULL, %s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (
                    source.actor or "system",
                    source.device_id or None,
                    source.source_type or None,
                    source.source_id or None,
                    source.raw_text or None,
                    json.dumps({}),
                ),
            ).fetchone()
            conn.commit()
            return row["id"]

    def tx_by_id(self, tx_id: int) -> TxRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM world_tx WHERE id = %s", (tx_id,)
            ).fetchone()
        if not row:
            raise WorldStoreError(f"tx {tx_id} nicht gefunden")
        return TxRecord(
            id=row["id"],
            created_at=row["created_at"],
            actor=row["actor"],
            source_type=row.get("source_type"),
            source_id=row.get("source_id"),
            raw_text=row.get("raw_text"),
        )

    def last_tx_id(self) -> int | None:
        with self._connect() as conn:
            row = conn.execute("SELECT max(id) AS m FROM world_tx").fetchone()
            return row["m"] if row else None

    # ------------------------------------------------------------------
    # Datoms schreiben (append-only) + Validierung
    # ------------------------------------------------------------------

    def _validate_changes(self, changes: list[DatomChange]) -> None:
        specs = self._attribute_specs()
        for ch in changes:
            # 1) XOR: genau eines von value/ref_entity
            has_value = ch.value is not None
            has_ref = ch.ref_entity is not None
            if has_value == has_ref:
                raise WorldStoreError(
                    f"{ch.attribute}: genau EINES von value/ref_entity "
                    f"muss gesetzt sein (value={ch.value!r}, "
                    f"ref={ch.ref_entity!r})"
                )
            # 2) Attribut muss im Vokabular sein
            if ch.attribute not in specs:
                raise WorldStoreError(
                    f"Attribut {ch.attribute!r} nicht in world_attribute. "
                    f"Vokabular erweitern statt ungültige Datoms schreiben."
                )
            # 3) value_kind muss passen
            spec = specs[ch.attribute]
            if spec["value_kind"] == "ref" and not has_ref:
                raise WorldStoreError(
                    f"{ch.attribute} ist eine ref-Beziehung: "
                    f"ref_entity muss gesetzt sein, nicht value"
                )
            if spec["value_kind"] == "scalar" and not has_value:
                raise WorldStoreError(
                    f"{ch.attribute} ist ein scalar-Attribut: "
                    f"value muss gesetzt sein, nicht ref_entity"
                )

        # 4) Cardinality-one: neuer aktiver Wert erfordert Retract
        #    aller aktiven Werte desselben Attributes.
        for ch in changes:
            if not ch.added:
                continue
            spec = specs[ch.attribute]
            if spec["cardinality"] != "one":
                continue  # many: mehrere aktive Werte erlaubt
            actives = self.current_datoms(
                entity_id=ch.entity_id, attribute=ch.attribute
            )
            for d in actives:
                if self._is_retracted_by(d, changes):
                    continue  # wird in derselben TX retractet ✓
                raise CardinalityViolation(
                    f"cardinality=one verletzt für "
                    f"({ch.entity_id}, {ch.attribute}): aktiver Wert "
                    f"'{self._datom_payload(d)}' wurde nicht retractet, "
                    f"aber '{self._datom_payload(ch)}' soll hinzugefügt "
                    f"werden. Erst retracten oder den Planner korrigieren."
                )

    @staticmethod
    def _datom_payload(d) -> str:
        if getattr(d, "ref_entity", None):
            return f"ref:{d.ref_entity}"
        return f"val:{d.value}"

    def _is_retracted_by(self, active: DatomRecord,
                         changes: list[DatomChange]) -> bool:
        for ch in changes:
            if ch.added or ch.attribute != active.attribute:
                continue
            if ch.entity_id != active.entity_id:
                continue
            same_ref = (
                active.ref_entity is not None
                and ch.ref_entity == active.ref_entity
            )
            same_val = (
                active.value is not None
                and json.dumps(ch.value, default=str)
                == json.dumps(active.value, default=str)
            )
            if same_ref or same_val:
                return True
        return False

    def append_datoms(self, tx_id: int,
                      changes: list[DatomChange]) -> None:
        """Hängt Datoms an eine bestehende Transaction an.

        Vorab-Validierung (siehe _validate_changes) stellt sicher,
        dass keine Invarianten verletzt werden. Schlägt die Validierung
        fehl, wird NICHTS geschrieben.
        """
        if not changes:
            return
        self._validate_changes(changes)
        rows = []
        for ch in changes:
            value_json = Json(ch.value) if ch.value is not None else None
            rows.append((
                ch.entity_id,
                ch.attribute,
                value_json,
                ch.ref_entity,
                tx_id,
                ch.added,
            ))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO world_datom
                        (entity_id, attribute, value, ref_entity,
                         tx_id, added)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            conn.commit()

    def apply_plan(self, plan: TransactionPlan,
                   source: SourceContext) -> int:
        """Führt einen TransactionPlan atomar aus. Liefert tx_id."""
        if plan.is_empty:
            raise WorldStoreError("TransactionPlan ist leer")
        tx_id = self.create_tx(source)
        self.append_datoms(tx_id, plan.changes)
        return tx_id

    # ------------------------------------------------------------------
    # Current State (View)
    # ------------------------------------------------------------------

    def current_datoms(self, entity_id: UUID | None = None,
                       attribute: str | None = None,
                       ref_entity: UUID | None = None,
                       value: Any = None) -> list[DatomRecord]:
        """Liefert alle aktiven Datoms (ggf. gefiltert).

        Ein Datom ist aktiv, wenn seine letzte Operation added=true
        ist (siehe world_current_datoms-View).
        """
        clauses, params = [], []
        if entity_id is not None:
            clauses.append("entity_id = %s")
            params.append(entity_id)
        if attribute is not None:
            clauses.append("attribute = %s")
            params.append(attribute)
        if ref_entity is not None:
            clauses.append("ref_entity = %s")
            params.append(ref_entity)
        if value is not None:
            clauses.append("value = %s::jsonb")
            params.append(json.dumps(value, default=str))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT * FROM world_current_datoms" + where +
            " ORDER BY entity_id, attribute, id"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [_record(r) for r in rows]

    def get_entity(self, entity_id: UUID) -> dict:
        """Liefert die aktiven Datoms einer Entity als strukturiertes
        dict: {entity_id, attributes: {attr: [wert|ref, ...]}}.

        Die Reihenfolge der Entity-Attribute folgt einer stabilen,
        für Menschen sinnvollen Reihenfolge.
        """
        dats = self.current_datoms(entity_id=entity_id)
        attrs: dict[str, list] = {}
        for d in dats:
            payload = d.ref_entity if d.ref_entity is not None else d.value
            attrs.setdefault(d.attribute, []).append(payload)
        return {"entity_id": entity_id, "attributes": attrs}

    def find_entities_by_name(self, name: str) -> list[dict]:
        """Findet Entities per exaktem Namen oder Alias (case-insensitive).

        Liefert [{entity_id, name, matched_alias}].
        """
        if not name or not name.strip():
            return []
        needle = name.strip()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entity_id, attribute, value
                FROM world_current_datoms
                WHERE attribute IN ('sys/name', 'sys/alias')
                  AND lower(value #>> '{}') = lower(%s)
                """,
                (needle,),
            ).fetchall()
        out = []
        seen = set()
        for r in rows:
            if r["entity_id"] in seen:
                continue
            seen.add(r["entity_id"])
            out.append({
                "entity_id": r["entity_id"],
                "name": (r["value"] if isinstance(r["value"], str)
                         else str(r["value"])),
                "matched_alias": r["attribute"] == "sys/alias",
            })
        return out

    # ------------------------------------------------------------------
    # Historie
    # ------------------------------------------------------------------

    def history(self, entity_id: UUID) -> list[DatomRecord]:
        """Alle Datoms einer Entity, chronologisch nach tx_id."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_datom WHERE entity_id = %s"
                " ORDER BY tx_id, id",
                (entity_id,),
            ).fetchall()
            return [_record(r) for r in rows]

    def datoms_of_tx(self, tx_id: int) -> list[DatomRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM world_datom WHERE tx_id = %s ORDER BY id",
                (tx_id,),
            ).fetchall()
            return [_record(r) for r in rows]

    def as_of(self, entity_id: UUID, attribute: str,
              tx_id: int) -> Any:
        """Liefert den Wert von (entity, attribute) zum Zeitpunkt tx_id.

        Konkret: die letzte Operation mit tx_id <= gegebener tx_id,
        und nur wenn sie added=true ist.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value, ref_entity, added
                FROM world_datom
                WHERE entity_id = %s AND attribute = %s AND tx_id <= %s
                ORDER BY tx_id DESC, id DESC
                LIMIT 1
                """,
                (entity_id, attribute, tx_id),
            ).fetchone()
        if not row or not row["added"]:
            return None
        return row["ref_entity"] if row["ref_entity"] else row["value"]

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def undo_last(self, source: SourceContext | None = None) -> int:
        """Macht die letzte Transaction rückgängig (neue TX).

        undo_last ist immer sicher: es gibt keine spätere TX,
        die dieselben Attribute berühren könnte.
        """
        last = self.last_tx_id()
        if last is None:
            raise WorldStoreError("Keine Transactions vorhanden")
        src = source or SourceContext(
            source_type="system", raw_text=f"undo_last (tx {last})"
        )
        return self._undo_tx(last, src)

    def undo_tx(self, tx_id: int,
                source: SourceContext | None = None) -> int:
        """Macht eine historische Transaction rückgängig (neue TX).

        Schutzregel (aus der v6-Diskussion): Eine historische TX darf
        nicht automatisch geundot werden, wenn spätere Transactions
        dieselben (entity, attribute)-Paare berührt haben. In diesem
        Fall wird UndoConflict geworfen und der Nutzer muss entscheiden
        (bzw. die aufrufende Schicht muss einen sauberen Plan bauen).
        """
        src = source or SourceContext(
            source_type="system", raw_text=f"undo tx {tx_id}"
        )
        self._check_undo_conflict(tx_id)
        return self._undo_tx(tx_id, src)

    def _check_undo_conflict(self, tx_id: int) -> None:
        """Wirft UndoConflict, wenn spätere Transactions dieselben
        (entity, attribute)-Paare berührt haben wie die Ziel-TX."""
        with self._connect() as conn:
            # Alle (entity, attribute)-Paare, die NACH der Ziel-TX
            # verändert wurden
            later = conn.execute(
                """
                SELECT DISTINCT entity_id, attribute
                FROM world_datom
                WHERE tx_id > %s
                  AND entity_id IN (
                      SELECT DISTINCT entity_id FROM world_datom
                      WHERE tx_id = %s
                  )
                """,
                (tx_id, tx_id),
            ).fetchall()
            target = conn.execute(
                """
                SELECT DISTINCT entity_id, attribute
                FROM world_datom WHERE tx_id = %s
                """,
                (tx_id,),
            ).fetchall()
        later_pairs = {(r["entity_id"], r["attribute"]) for r in later}
        target_pairs = {(r["entity_id"], r["attribute"]) for r in target}
        overlap = target_pairs & later_pairs
        if overlap:
            raise UndoConflict(
                f"Undo von tx {tx_id} blockiert: spätere Transactions "
                f"haben {len(overlap)} dieselben (entity, attribute)-"
                f"Paare berührt. Bitte prüfen und ggf. manuell planen."
            )

    def _undo_tx(self, tx_id: int, source: SourceContext) -> int:
        datoms = self.datoms_of_tx(tx_id)
        if not datoms:
            raise WorldStoreError(
                f"tx {tx_id} hat keine Datoms — nichts zu undoen"
            )
        new_tx = self.create_tx(source)
        # Inverse Datoms in umgekehrter Reihenfolge anlegen
        inverse = [
            DatomChange(
                entity_id=d.entity_id,
                attribute=d.attribute,
                value=d.value,
                ref_entity=d.ref_entity,
                added=not d.added,
            )
            for d in reversed(datoms)
        ]
        self.append_datoms(new_tx, inverse)
        return new_tx
