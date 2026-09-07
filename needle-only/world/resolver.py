"""Orga v6 — Resolver: Entity-Matching + Kontext + Zeit + Relationen.

Der Resolver ist rein deterministisch:
1. Exact sys/name Match (case-insensitive)
2. Exact sys/alias Match
3. Wenn kein sicherer Treffer: neue Entity

Regel: Lieber zwei getrennte Entities als ein falscher Merge.

Zeit-Auflösung verwendet modules/timesync.py (bestehender
generalistischer deutscher Kalender-Auflöser).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from modules.timesync import _TZ, now as tz_now, parse_calendar, resolve_dt

from .model import DatomChange
from .store import WorldStore

# =====================================================================
# Relation-Normalisierung (kleine statische Aliasmap, keine Ontologie)
# =====================================================================

RELATION_ALIASES = {
    # bring
    "bringen": "bring", "bringst": "bring", "bringt": "bring",
    "gebracht": "bring", "mitbringen": "bring", "mitgebracht": "bring",
    "mitnehmen": "bring", "besorgen": "bring", "besorge": "bring",
    "kaufen": "bring", "organisieren": "bring",
    # attend
    "kommen": "attend", "kommt": "bringt", "teilnehmen": "attend",
    "teilgenommen": "attend", "dabei": "attend", "erscheinen": "attend",
    "zusagen": "attend", "nimmt teil": "attend",
    # own
    "besitzen": "own", "gehören": "own", "gehört": "own",
    "besitzer": "own", "verantwortlich": "own",
    # remind
    "erinnern": "remind", "erinnerung": "remind", "erinnermich": "remind",
    "wecken": "remind", "alarm": "remind",
    # schedule / reschedule / cancel
    "einplanen": "schedule", "terminieren": "schedule",
    "verschieben": "reschedule", "verschiebe": "reschedule",
    "ändern": "reschedule", "ändere": "reschedule",
    "absagen": "cancel", "stornieren": "cancel", "sageab": "cancel",
    # update
    "übernehmen": "takeover", "übernimmt": "takeover",
}


def normalize_relation(relation: str) -> str:
    """Normalisiert eine Relation (Needle-Rohstring) zur kanonischen Form."""
    if not relation:
        return ""
    low = relation.lower().strip().strip(".,!?")
    return RELATION_ALIASES.get(low, low)


# =====================================================================
# Zeit-Normalisierung
# =====================================================================

_TIME_PREFIXES = (
    "morgen", "heute", "übermorgen", "nächste woche", "kommende woche",
    "montag", "dienstag", "mittwoch", "donnerstag", "freitag",
    "samstag", "sonntag",
)


def resolve_time(when: str, ref: datetime | None = None) -> datetime | None:
    """Löst einen Roh-Zeitstring zu einem datetime auf.

    Nutzt parse_calendar (generalistisch) mit resolve_dt als Fallback.
    """
    if not when or not when.strip():
        return None
    ref = ref or tz_now()
    low = when.lower().strip()

    # Relative Offsets: "in 3 stunden", "in 10 minuten"
    m = re.search(r"in\s+(\d+)\s*(min(uten?|s)?|stunden?|h|s)", low)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("min"):
            delta = timedelta(minutes=amount)
        elif unit.startswith("stund") or unit == "h":
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(seconds=amount)
        return (ref + delta).replace(second=0, microsecond=0)

    # "N stunden vorher/minuten vorher" → relativ, kein absoluter Wert
    # (wird vom Planner über org/object aufgelöst)
    if re.search(r"\b(vorher|davor|zuvor)\b", low):
        return None  # relative Zeit, Planner muss Kontext-Event nutzen

    # ISO-String direkt
    m = re.search(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})", low)
    if m:
        try:
            return datetime.fromisoformat(m.group(1))
        except ValueError:
            pass

    # Generalistischer Kalender: "kommende woche sa. ab 12:30Uhr"
    # parse_calendar liefert UTC-ISO — wir normalisieren nach Europe/Berlin,
    # damit alle Datom-Zeiten lokal und konsistent sind.
    cal = parse_calendar(low, ref)
    if cal.get("found") and cal.get("iso"):
        try:
            dt = datetime.fromisoformat(
                str(cal["iso"]).replace("Z", "+00:00"))
            return dt.astimezone(_TZ)
        except ValueError:
            pass

    # Fallback: resolve_dt
    dt = resolve_dt(low, ref)
    if dt:
        return dt

    # Zeit ohne Datum: "14 uhr", "9:30"
    m = re.search(r"(\d{1,2})[:.](\d{2})\b", low) or \
        re.search(r"\b(\d{1,2})\s*uhr\b", low)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.lastindex == 2 else 0
        if 0 <= hour <= 23:
            candidate = ref.replace(hour=hour, minute=minute,
                                    second=0, microsecond=0)
            if candidate < ref:
                candidate += timedelta(days=1)
            return candidate

    return None


# =====================================================================
# Entity-Resolution
# =====================================================================


class Resolver:
    """Deterministische Entity- und Kontext-Auflösung."""

    def __init__(self, store: WorldStore):
        self.store = store

    # -- Lookup -------------------------------------------------------

    def resolve_entity(self, label: str) -> UUID | None:
        """Findet EINE Entity per Name/Alias. None bei 0 oder >1 Treffern."""
        if not label or not label.strip():
            return None
        hits = self.store.find_entities_by_name(label.strip())
        if len(hits) == 1:
            return hits[0]["entity_id"]
        return None

    def resolve_or_create(self, label: str, entity_type: str,
                          create: bool = True
                          ) -> tuple[UUID | None, list[DatomChange]]:
        """Findet oder erstellt eine Entity.

        Liefert (entity_id, create_changes). Bei create=False und keinem
        Treffer: (None, []). Neue Entities werden mit sys/type und
        sys/name angelegt.
        """
        label = (label or "").strip()
        if not label:
            return None, []
        existing = self.resolve_entity(label)
        if existing:
            return existing, []
        if not create:
            return None, []
        new_id = uuid4()
        changes = [
            DatomChange(entity_id=new_id, attribute="sys/type",
                        value=entity_type),
            DatomChange(entity_id=new_id, attribute="sys/name",
                        value=label),
        ]
        return new_id, changes

    # -- Context Resolution -------------------------------------------

    def resolve_context_entity(self, context_label: str,
                               session_context) -> UUID | None:
        """Löst eine Kontext-Referenz auf: 'beim Treffen' → Event-Entity.

        Reihenfolge:
        1. Explizites Label im World State (exakt/alias)
        2. Session Context: aktuelles Event
        3. Session Context: letzte relevante Entity
        """
        # 1) Explizites Label
        label = (context_label or "").strip()
        if label:
            # "beim Treffen" → "Treffen", "zum Vorstandssitzung" → "Vorstandssitzung"
            cleaned = re.sub(
                r"^(beim|zum|zur|in der|im|am|auf der|auf dem)\s+",
                "", label, flags=re.I).strip()
            hits = self.store.find_entities_by_name(cleaned)
            if len(hits) == 1:
                return hits[0]["entity_id"]

        # 2) Session Context: aktuelles Event
        if session_context and session_context.current_event:
            return session_context.current_event

        # 3) Session Context: letzte relevante Entity
        if session_context and session_context.last_entities:
            return session_context.last_entities[-1]

        return None

    # -- Zeit-Utils ---------------------------------------------------

    def normalize_time(self, when: str) -> datetime | None:
        return resolve_time(when)
