"""Orga v6 — Query Engine: Deterministische World Queries.

Beantwortet ReadFrames ohne generative Elemente:
- Commitment-Query: „Wer bringt X?" → Actor-Name
- Fact-Query: „Wie lautet X?" → note/body
- Event-Query: „Was steht an?" / „Wann ist X?" → Event-Liste
- Participant-Query: „Wer kommt zum X?" → Participant-Namen
- Historische Queries: „... ursprünglich ...?" → History-Lookup
"""
from __future__ import annotations

import re
from datetime import timedelta

from modules.timesync import now as tz_now, format_local

from .model import ReadFrame
from .resolver import Resolver, normalize_relation
from .store import WorldStore

_HISTORIC_MARKERS = ("ursprünglich", "original", "urspruenglich",
                     "vorher gebracht", "zuerst")
_QUESTION_WORDS = ("wer", "was", "wann", "wie", "welche", "welcher",
                   "wo", "wieso", "warum")


def _is_historic(frame: ReadFrame) -> bool:
    blob = " ".join([frame.when or "", frame.relation or "",
                     frame.context or ""]).lower()
    return any(m in blob for m in _HISTORIC_MARKERS)


class QueryEngine:
    """Deterministische Beantwortung von ReadFrames."""

    def __init__(self, store: WorldStore, resolver: Resolver):
        self.store = store
        self.resolver = resolver

    # ==================================================================
    # Dispatch
    # ==================================================================

    def answer(self, frame: ReadFrame) -> str:
        relation = normalize_relation(frame.relation)
        historic = _is_historic(frame)

        # „Wer bringt X?" — Commitment
        if relation == "bring" or self._asks_bring(frame):
            return self._who_brings(frame, historic)

        # „Wer kommt zum X?" / „Wer ist beim X dabei?"
        if relation == "attend" or self._asks_attend(frame):
            return self._who_attends(frame)

        # „Was steht an?" — Event-Liste
        if self._asks_schedule(frame):
            return self._list_events(frame)

        # „Wie lautet X?" / „Was ist X?" — Fact-Lookup
        if frame.subject or frame.object:
            return self._lookup_fact(frame)

        return "Das weiß ich leider nicht."

    # -- Heuristiken (Frage-Erkennung) --------------------------------

    @staticmethod
    def _asks_bring(frame: ReadFrame) -> bool:
        rel = (frame.relation or "").lower()
        return "bring" in rel or "bringt" in rel

    @staticmethod
    def _asks_attend(frame: ReadFrame) -> bool:
        rel = (frame.relation or "").lower()
        return "komm" in rel or "dabei" in rel or "teilnehm" in rel

    @staticmethod
    def _asks_schedule(frame: ReadFrame) -> bool:
        rel = (frame.relation or "").lower()
        blob = f"{rel} {(frame.when or '').lower()}"
        if "steht an" in blob or "steht" in rel and frame.when:
            return True
        if re.search(r"\b(was|welche)\b.*\b(steht|an|termine|geplant)\b",
                     blob):
            return True
        return False

    # ==================================================================
    # Commitment: „Wer bringt den Beamer?"
    # ==================================================================

    def _who_brings(self, frame: ReadFrame, historic: bool) -> str:
        # Objekt-Entity auflösen (z. B. „Beamer")
        label = (frame.object or frame.subject or "").strip()
        if not label:
            return "Welches Objekt meinst du?"

        resource_id = self.resolver.resolve_entity(label)
        if resource_id is None:
            return f"Ich kenne '{label}' leider nicht."

        # Commitments mit action=bring und object=resource suchen
        commitments = self.store.find_entities([
            ("org/action", "value", "bring"),
            ("org/object", "ref", resource_id),
        ])
        if not commitments:
            return f"Niemand bringt '{label}' — es gibt kein Commitment."

        if len(commitments) > 1:
            return (f"Es gibt {len(commitments)} Commitments "
                    f"für '{label}' — bitte präzisieren.")

        commitment_id = commitments[0]

        if historic:
            # Historische Query: ursprünglicher Actor
            hist = self.store.history(commitment_id)
            actor_datoms = [d for d in hist
                            if d.attribute == "org/actor"]
            if not actor_datoms:
                return "Kein Actor gefunden."
            first_added = None
            for d in sorted(actor_datoms, key=lambda x: x.tx_id):
                if d.added:
                    first_added = d
                    break
            if first_added is None:
                return "Kein Actor gefunden."
            actor_ent = self.store.get_entity(first_added.ref_entity)
            name = actor_ent["attributes"].get("sys/name", ["?"])[0]
            return f"Ursprünglich: {name}"

        # Aktuelle Query
        actor_id = self.store.current_value(commitment_id, "org/actor")
        if actor_id is None:
            return f"Niemand bringt '{label}' derzeit."
        actor_ent = self.store.get_entity(actor_id)
        name = actor_ent["attributes"].get("sys/name", ["?"])[0]
        return f"{name} bringt '{label}'."

    # ==================================================================
    # Attend: „Wer kommt zum Treffen?"
    # ==================================================================

    def _who_attends(self, frame: ReadFrame) -> str:
        label = (frame.context or frame.subject or "").strip()
        if not label:
            return "Zu welchem Event meinst du?"

        event_id = self.resolver.resolve_entity(label)
        if event_id is None:
            return f"Ich kenne '{label}' leider nicht."

        participants = self.store.current_values(event_id,
                                                  "org/participant")
        if not participants:
            return f"Niemand nimmt an '{label}' teil."

        names = []
        for p_id in participants:
            p_ent = self.store.get_entity(p_id)
            name = p_ent["attributes"].get("sys/name", ["?"])[0]
            names.append(name)
        return f"An '{label}' nehmen teil: {', '.join(names)}"

    # ==================================================================
    # Events: „Was steht morgen an?" / „Wann ist Zahnarzt?"
    # ==================================================================

    def _list_events(self, frame: ReadFrame) -> str:
        """Listet Events für einen Zeitraum (heute/morgen/diese Woche)."""
        when = (frame.when or "").lower()
        now = tz_now()

        if "morgen" in when:
            start = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            label = "morgen"
        elif "heute" in when or not when:
            start = now.replace(hour=0, minute=0, second=0,
                                microsecond=0)
            end = start + timedelta(days=1)
            label = "heute"
        elif "woche" in when:
            start = now.replace(hour=0, minute=0, second=0,
                                microsecond=0)
            end = start + timedelta(days=7)
            label = "diese Woche"
        else:
            start = now.replace(hour=0, minute=0, second=0,
                                microsecond=0)
            end = start + timedelta(days=1)
            label = "heute"

        # Alle aktiven Events mit time/start im Zeitraum
        events = self.store.find_entities([
            ("sys/type", "value", "event"),
        ])

        results = []
        for event_id in events:
            start_val = self.store.current_value(event_id, "time/start")
            if start_val is None:
                continue
            try:
                from datetime import datetime
                start_dt = datetime.fromisoformat(str(start_val))
            except (ValueError, TypeError):
                continue
            if start <= start_dt < end:
                name = self.store.current_value(event_id, "sys/name")
                results.append((start_dt, name or "?"))

        if not results:
            return f"Keine Termine {label}."

        results.sort(key=lambda x: x[0])
        lines = [f"Termine {label}:"]
        for dt, name in results:
            lines.append(f"  • {format_local(dt.isoformat())} {name}")
        return "\n".join(lines)

    # ==================================================================
    # Facts: „Wie lautet der WLAN-Code?"
    # ==================================================================

    def _lookup_fact(self, frame: ReadFrame) -> str:
        label = (frame.subject or frame.object or "").strip()
        if not label:
            return "Was möchtest du wissen?"

        # Zuerst: Fact-Entity mit diesem Namen
        fact_id = self.resolver.resolve_entity(label)
        if fact_id is not None:
            body = self.store.current_value(fact_id, "note/body")
            if body is not None:
                return f"{label}: {body}"

        # Fallback: Event mit diesem Namen („Wann ist Zahnarzt?")
        if fact_id is not None:
            start = self.store.current_value(fact_id, "time/start")
            if start is not None:
                return f"{label} ist um {format_local(str(start))}."

        return f"Ich kenne '{label}' leider nicht."
