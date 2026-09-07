"""Orga v6 — Write Planner: WriteFrame → TransactionPlan.

Das Herzstück der Deterministik: Der Planner kennt die
Datenmodell-Semantik und entscheidet AUSSCHLIESSLICH aus
Frame + aktuellem World State:

- Welche Entity-Art erzeugt das Frame? (event, commitment,
  reminder, fact)
- Existiert eine passende Ziel-Entity? → Replace statt Neu
- Welche Datoms müssen addiert/retractet werden?

Needle wird hier NICHT aufgerufen. Bei Mehrdeutigkeit wird
eine warning ins Plan-Objekt geschrieben — nicht geraten.
"""
from __future__ import annotations

import re
import uuid as _uuid
from datetime import datetime, timedelta
from uuid import UUID

from .model import DatomChange, TransactionPlan, WriteFrame
from .resolver import Resolver, normalize_relation, resolve_time
from .store import WorldStore


def _iso(dt: datetime) -> str:
    """ISO-String mit Sekunden-Genauigkeit (kanonisches Datom-Format)."""
    return dt.replace(microsecond=0).isoformat()


class WritePlanner:
    """Plannt atomare Datom-Transaktionen aus sprachlichen Frames."""

    def __init__(self, store: WorldStore, resolver: Resolver):
        self.store = store
        self.resolver = resolver

    # ==================================================================
    # Dispatch
    # ==================================================================

    def plan(self, frame: WriteFrame, session_ctx=None) -> TransactionPlan:
        action = normalize_relation(frame.relation)

        if action == "bring":
            return self._plan_commitment(frame, session_ctx)
        if action == "attend":
            return self._plan_attend(frame, session_ctx)
        if action == "retract_attend":
            return self._plan_retract_attend(frame, session_ctx)
        if action == "takeover":
            return self._plan_commitment(frame, session_ctx)
        if action == "remind":
            return self._plan_reminder(frame, session_ctx)
        if action == "reschedule":
            return self._plan_reschedule(frame, session_ctx)
        if action == "cancel":
            return self._plan_cancel(frame, session_ctx)

        # Default-Fälle ohne spezifische Relation
        if frame.subject and frame.when:
            return self._plan_event(frame, session_ctx)
        if frame.subject and (frame.object or frame.note):
            return self._plan_fact(frame, session_ctx)

        return TransactionPlan(
            changes=[], summary="Unklar", confidence=0.3,
            warnings=[f"Frame konnte nicht geplant werden: {frame!r}"])

    # ==================================================================
    # Event: "Zahnarzt morgen um 14 Uhr"
    # ==================================================================

    def _plan_event(self, frame: WriteFrame,
                    session_ctx=None) -> TransactionPlan:
        when_dt = resolve_time(frame.when)
        if when_dt is None:
            return TransactionPlan(
                summary=f"Zeit nicht erkannt: {frame.when!r}",
                confidence=0.3,
                warnings=["Zeitangabe konnte nicht aufgelöst werden"])

        event_id, create_changes = self.resolver.resolve_or_create(
            frame.subject, entity_type="event")
        changes = list(create_changes)

        existing_start = self.store.current_value(event_id, "time/start")
        new_iso = _iso(when_dt)

        if existing_start is None:
            changes.append(DatomChange(
                entity_id=event_id, attribute="time/start", value=new_iso))
            summary = f"Event '{frame.subject}' angelegt ({new_iso})"
        elif _iso(existing_start) == new_iso:
            summary = (f"Event '{frame.subject}' startet bereits um "
                       f"{new_iso} — keine Änderung")
        else:
            changes.append(DatomChange(
                entity_id=event_id, attribute="time/start",
                value=_iso(existing_start), added=False))
            changes.append(DatomChange(
                entity_id=event_id, attribute="time/start", value=new_iso))
            summary = (f"Event '{frame.subject}': "
                       f"{_iso(existing_start)} → {new_iso}")

        return TransactionPlan(changes=changes, summary=summary,
                               confidence=0.9)

    # ==================================================================
    # Commitment: "Julia bringt den Beamer" / "Jana übernimmt ..."
    # ==================================================================

    def _plan_commitment(self, frame: WriteFrame,
                         session_ctx=None) -> TransactionPlan:
        person_id, person_changes = self.resolver.resolve_or_create(
            frame.subject, entity_type="person")
        resource_id, resource_changes = self.resolver.resolve_or_create(
            frame.object, entity_type="resource")

        # Context (Event) auflösen, falls angegeben
        context_id = None
        context_changes = []
        if frame.context:
            context_id, context_changes = self.resolver.resolve_or_create(
                frame.context, entity_type="event")

        changes = (list(person_changes) + list(resource_changes)
                   + list(context_changes))

        # Existierendes Commitment mit gleichem Objekt finden
        existing = self.store.find_entities([
            ("org/action", "value", "bring"),
            ("org/object", "ref", resource_id),
        ])

        if len(existing) > 1:
            return TransactionPlan(
                summary="Mehrere Commitments gefunden",
                confidence=0.5,
                warnings=[f"{len(existing)} Commitments für "
                          f"'{frame.object}' — bitte präzisieren"])

        if not existing:
            # Neues Commitment anlegen
            commitment_id = _uuid.uuid4()
            changes.extend([
                DatomChange(entity_id=commitment_id,
                            attribute="sys/type", value="commitment"),
                DatomChange(entity_id=commitment_id,
                            attribute="org/actor", ref_entity=person_id),
                DatomChange(entity_id=commitment_id,
                            attribute="org/action", value="bring"),
                DatomChange(entity_id=commitment_id,
                            attribute="org/object", ref_entity=resource_id),
            ])
            if context_id:
                changes.append(DatomChange(
                    entity_id=commitment_id,
                    attribute="org/context", ref_entity=context_id))
            return TransactionPlan(
                changes=changes,
                summary=f"'{frame.subject}' bringt '{frame.object}'",
                confidence=0.9)

        commitment_id = existing[0]
        current_actor = self.store.current_value(commitment_id, "org/actor")

        if current_actor == person_id:
            return TransactionPlan(
                changes=[],
                summary=(f"'{frame.subject}' bringt bereits "
                         f"'{frame.object}' — keine Änderung"),
                confidence=0.9)

        # Replace: alten Actor retracten, neuen setzen
        changes.extend([
            DatomChange(entity_id=commitment_id, attribute="org/actor",
                        ref_entity=current_actor, added=False),
            DatomChange(entity_id=commitment_id, attribute="org/actor",
                        ref_entity=person_id),
        ])
        return TransactionPlan(
            changes=changes,
            summary=f"'{frame.object}': Actor → '{frame.subject}'",
            confidence=0.9)

    # ==================================================================
    # Attend: "Lisa kommt zum Treffen" → Participant am Event
    # ==================================================================

    def _plan_attend(self, frame: WriteFrame,
                     session_ctx=None) -> TransactionPlan:
        person_id, person_changes = self.resolver.resolve_or_create(
            frame.subject, entity_type="person")

        context_id = self.resolver.resolve_context_entity(
            frame.context, session_ctx)
        if context_id is None:
            return TransactionPlan(
                summary="Kein Kontext-Event gefunden",
                confidence=0.4,
                warnings=["Kontext konnte nicht aufgelöst werden"])

        participants = self.store.current_values(context_id,
                                                 "org/participant")
        if person_id in participants:
            return TransactionPlan(
                changes=[],
                summary=(f"'{frame.subject}' nimmt bereits teil — "
                         f"keine Änderung"),
                confidence=0.9)

        changes = list(person_changes) + [
            DatomChange(entity_id=context_id, attribute="org/participant",
                        ref_entity=person_id),
        ]
        return TransactionPlan(
            changes=changes,
            summary=f"'{frame.subject}' nimmt teil",
            confidence=0.9)

    # ==================================================================
    # Retract Attend: „Lisa kommt doch nicht"
    # ==================================================================

    def _plan_retract_attend(self, frame: WriteFrame,
                             session_ctx=None) -> TransactionPlan:
        person_id = self.resolver.resolve_entity(frame.subject)
        if person_id is None:
            return TransactionPlan(
                changes=[],
                summary=f"Person '{frame.subject}' nicht gefunden",
                confidence=0.4,
                warnings=["Person konnte nicht aufgelöst werden"])

        context_id = self.resolver.resolve_context_entity(
            frame.context, session_ctx)

        if context_id is None:
            # Kein expliziter Kontext: Event finden, wo die Person
            # aktuell Participant ist
            candidates = self.store.find_entities([
                ("org/participant", "ref", person_id),
            ])
            # Nur Event-Entities berücksichtigen
            event_entities = []
            for e in candidates:
                e_type = self.store.current_value(e, "sys/type")
                if e_type == "event":
                    event_entities.append(e)

            if len(event_entities) == 1:
                context_id = event_entities[0]
            elif len(event_entities) == 0:
                return TransactionPlan(
                    changes=[],
                    summary=(f"'{frame.subject}' ist bei keinem Event "
                             f"Participant"),
                    confidence=0.4,
                    warnings=["Kein Event mit dieser Person gefunden"])
            else:
                return TransactionPlan(
                    changes=[],
                    summary=(f"'{frame.subject}' ist bei "
                             f"{len(event_entities)} Events Participant"),
                    confidence=0.5,
                    warnings=["Mehrere Events — bitte präzisieren"])

        if context_id is None:
            return TransactionPlan(
                changes=[],
                summary="Kein Kontext-Event gefunden",
                confidence=0.4,
                warnings=["Event-Kontext konnte nicht aufgelöst werden"])

        changes = [
            DatomChange(entity_id=context_id,
                        attribute="org/participant",
                        ref_entity=person_id, added=False),
        ]
        return TransactionPlan(
            changes=changes,
            summary=f"'{frame.subject}' nimmt nicht mehr teil",
            confidence=0.9)

    # ==================================================================
    # Remind: "Erinnere mich 3 Stunden vorher" → Reminder am Kontext-Event
    # ==================================================================

    def _plan_reminder(self, frame: WriteFrame,
                       session_ctx=None) -> TransactionPlan:
        context_id = self.resolver.resolve_context_entity(
            frame.context, session_ctx)

        if context_id is None:
            # Fallback: Event mit Startzeit finden (nächstes bevorstehendes)
            events = self.store.find_entities([
                ("sys/type", "value", "event"),
            ])
            timed_events = []
            for e in events:
                start = self.store.current_value(e, "time/start")
                if start:
                    timed_events.append((e, start))

            if len(timed_events) == 1:
                context_id = timed_events[0][0]
            elif len(timed_events) > 1:
                # Sortiere nach Startzeit, nimm das nächste
                from modules.timesync import now as tz_now
                now = tz_now()
                timed_events.sort(
                    key=lambda x: abs((x[1] - now).total_seconds()))
                context_id = timed_events[0][0]
            else:
                return TransactionPlan(
                    summary="Kein Kontext-Event gefunden",
                    confidence=0.4,
                    warnings=["Erinnerung braucht ein Kontext-Event"])

        event_start = self.store.current_value(context_id, "time/start")
        if event_start is None:
            return TransactionPlan(
                summary="Kontext-Event hat keine Startzeit",
                confidence=0.4,
                warnings=["Erinnerung braucht eine Event-Zeit"])

        when_dt = self._resolve_relative_time(frame.when, event_start)
        if when_dt is None:
            return TransactionPlan(
                summary=f"Zeit nicht erkannt: {frame.when!r}",
                confidence=0.3,
                warnings=["Zeitangabe konnte nicht aufgelöst werden"])

        reminder_id = _uuid.uuid4()
        changes = [
            DatomChange(entity_id=reminder_id, attribute="sys/type",
                        value="reminder"),
            DatomChange(entity_id=reminder_id, attribute="org/object",
                        ref_entity=context_id),
            DatomChange(entity_id=reminder_id, attribute="time/due",
                        value=_iso(when_dt)),
            DatomChange(entity_id=reminder_id, attribute="org/status",
                        value="active"),
        ]
        return TransactionPlan(
            changes=changes,
            summary=f"Erinnerung um {_iso(when_dt)}",
            confidence=0.9)

    _GERMAN_NUMBERS = {
        "eine": "1", "ein": "1", "eins": "1",
        "zwei": "2", "drei": "3", "vier": "4", "fünf": "5",
        "fuenf": "5", "sechs": "6", "sieben": "7", "acht": "8",
        "neun": "9", "zehn": "10", "elf": "11", "zwölf": "12",
        "zwoelf": "12", "fünfzehn": "15", "fuenfzehn": "15",
        "zwanzig": "20", "dreißig": "30", "dreissig": "30",
    }

    def _resolve_relative_time(self, when: str,
                               event_start) -> datetime | None:
        """Löst 'N Stunden vorher' relativ zur Event-Startzeit auf."""
        if not when:
            return None
        low = when.lower().strip()

        # Deutsche Zahlwörter in Ziffern umwandeln
        for word, digit in self._GERMAN_NUMBERS.items():
            low = re.sub(rf"\b{word}\b", digit, low)

        # event_start kann ein ISO-String (aus DB) oder datetime sein
        if isinstance(event_start, str):
            try:
                event_start = datetime.fromisoformat(event_start)
            except ValueError:
                return None
        elif not isinstance(event_start, datetime):
            return None

        m = re.search(r"(\d+)\s*(stunden?|h)\s*(vorher|davor|vor)", low)
        if m:
            return event_start - timedelta(hours=int(m.group(1)))

        m = re.search(r"(\d+)\s*(min(uten?)?|m)\s*(vorher|davor|vor)", low)
        if m:
            return event_start - timedelta(minutes=int(m.group(1)))

        return resolve_time(when)

    # ==================================================================
    # Reschedule: "Verschieb Zahnarzt auf 15 Uhr"
    # ==================================================================

    def _plan_reschedule(self, frame: WriteFrame,
                         session_ctx=None) -> TransactionPlan:
        event_id = self.resolver.resolve_entity(frame.subject)
        if event_id is None:
            return TransactionPlan(
                summary=f"Event '{frame.subject}' nicht gefunden",
                confidence=0.4,
                warnings=["Event konnte nicht aufgelöst werden"])

        when_dt = resolve_time(frame.when)
        if when_dt is None:
            return TransactionPlan(
                summary=f"Zeit nicht erkannt: {frame.when!r}",
                confidence=0.3,
                warnings=["Zeitangabe konnte nicht aufgelöst werden"])

        existing_start = self.store.current_value(event_id, "time/start")
        new_iso = _iso(when_dt)

        changes = []
        if existing_start is not None and existing_start != new_iso:
            changes.append(DatomChange(
                entity_id=event_id, attribute="time/start",
                value=existing_start, added=False))
        changes.append(DatomChange(
            entity_id=event_id, attribute="time/start", value=new_iso))
        return TransactionPlan(
            changes=changes,
            summary=f"Event '{frame.subject}' → {new_iso} verschoben",
            confidence=0.9)

    # ==================================================================
    # Cancel: "Sage Zahnarzt ab"
    # ==================================================================

    def _plan_cancel(self, frame: WriteFrame,
                     session_ctx=None) -> TransactionPlan:
        event_id = self.resolver.resolve_entity(frame.subject)
        if event_id is None:
            return TransactionPlan(
                summary=f"Event '{frame.subject}' nicht gefunden",
                confidence=0.4,
                warnings=["Event konnte nicht aufgelöst werden"])

        existing_status = self.store.current_value(event_id, "org/status")
        changes = []
        if existing_status is not None and existing_status != "cancelled":
            changes.append(DatomChange(
                entity_id=event_id, attribute="org/status",
                value=existing_status, added=False))
        if existing_status != "cancelled":
            changes.append(DatomChange(
                entity_id=event_id, attribute="org/status",
                value="cancelled"))
        return TransactionPlan(
            changes=changes,
            summary=f"Event '{frame.subject}' abgesagt",
            confidence=0.9)

    # ==================================================================
    # Fact: "Der WLAN-Code ist foo123" → Fact-Entity mit note/body
    # ==================================================================

    def _plan_fact(self, frame: WriteFrame,
                   session_ctx=None) -> TransactionPlan:
        body = (frame.note or frame.object or "").strip()
        if not body:
            return TransactionPlan(
                changes=[], summary="Leerer Fakt", confidence=0.3,
                warnings=["Fakt ohne Inhalt"])

        fact_id, create_changes = self.resolver.resolve_or_create(
            frame.subject, entity_type="fact")
        changes = list(create_changes)

        existing_body = self.store.current_value(fact_id, "note/body")
        if existing_body is None:
            changes.append(DatomChange(
                entity_id=fact_id, attribute="note/body", value=body))
            summary = f"Fakt '{frame.subject}' angelegt"
        elif existing_body == body:
            summary = (f"Fakt '{frame.subject}' bereits vorhanden — "
                       f"keine Änderung")
        else:
            changes.append(DatomChange(
                entity_id=fact_id, attribute="note/body",
                value=existing_body, added=False))
            changes.append(DatomChange(
                entity_id=fact_id, attribute="note/body", value=body))
            summary = (f"Fakt '{frame.subject}': "
                       f"'{existing_body}' → '{body}'")

        return TransactionPlan(changes=changes, summary=summary,
                               confidence=0.9)
