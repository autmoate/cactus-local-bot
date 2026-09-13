"""Gemma-first Pipeline: NLU → Tool-Call → Frame → Plan → Response.

Orchestrierung ohne Agent-Loop:
    Text → GemmaNLU (Tool-Calls) → Mapping auf v6-Frames
         → v6-Planner (Write) / QueryEngine (Read) → Response

Schreib-Operationen erzeugen immer einen TransactionPlan,
der erst nach Approval via store.apply_plan() ausgeführt wird.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# needle-only/ zum sys.path hinzufügen, damit world.* importierbar ist
_NEEDLE_ONLY = Path(__file__).resolve().parent.parent / "needle-only"
if str(_NEEDLE_ONLY) not in sys.path:
    sys.path.insert(0, str(_NEEDLE_ONLY))

from format import fmt_dt
from nlu import GemmaNLU

from world.model import (
    ReadFrame,
    Response,
    SourceContext,
    TransactionPlan,
    WriteFrame,
)
from world.planner import WritePlanner
from world.query import QueryEngine
from world.resolver import Resolver
from world.store import WorldStore


def _stats(store: WorldStore) -> dict[str, int]:
    """Zählt Entities, Datoms und Transaktionen."""
    from world.model import DatomRecord
    datoms = store.current_datoms()
    entities = {d.entity_id for d in datoms}
    return {
        "entities": len(entities),
        "datoms": len(datoms),
        "txs": store.last_tx_id() or 0,
    }


class GemmaPipeline:
    """Zentrale Pipeline: Text → NLU → Plan/Query → Response(s)."""

    def __init__(self, store: WorldStore, nlu: GemmaNLU | None = None):
        self.store = store
        self.nlu = nlu or GemmaNLU()
        self.resolver = Resolver(store)
        self.planner = WritePlanner(store, self.resolver)
        self.queries = QueryEngine(store, self.resolver)

    # ------------------------------------------------------------------
    # Haupteinstieg
    # ------------------------------------------------------------------

    def handle(self, text: str, source: SourceContext) -> list[Response]:
        """Text → Liste von Responses (eine pro Tool-Call)."""
        calls = self.nlu.interpret(text)

        if not calls:
            return [Response(text="Ich verstehe das leider nicht. "
                                  "Versuch: 'Zahnarzt morgen 14 Uhr' "
                                  "oder 'Was habe ich morgen?'")]

        responses: list[Response] = []
        for call in calls:
            resp = self._dispatch(call, source)
            if resp is not None:
                responses.append(resp)

        if not responses:
            responses.append(Response(text="Keine Aktion erkannt."))
        return responses

    # ------------------------------------------------------------------
    # Apply (nach Approval)
    # ------------------------------------------------------------------

    def apply(self, plan: TransactionPlan, source: SourceContext) -> int:
        """Führt einen approved Plan aus. Gibt tx_id zurück."""
        return self.store.apply_plan(plan, source)

    # ------------------------------------------------------------------
    # Intern: Dispatch auf Tool-Name
    # ------------------------------------------------------------------

    def _dispatch(self, call: dict[str, Any],
                  source: SourceContext) -> Response | None:
        name = call.get("name", "")
        args = call.get("arguments", {}) or {}

        if name == "calendar_write":
            return self._calendar_write(args)
        if name == "calendar_edit":
            return self._calendar_edit(args)
        if name == "calendar_delete":
            return self._calendar_delete(args)
        if name == "calendar_read":
            return self._calendar_read(args)
        if name == "commitment_write":
            return self._commitment_write(args)
        if name == "fact_write":
            return self._fact_write(args)
        if name == "query_answer":
            return self._query_answer(args)
        if name == "control_command":
            return self._control(args, source)
        if name == "error":
            return Response(text=f"❌ NLU-Fehler: "
                                 f"{args.get('message', '')}")
        return None

    # ------------------------------------------------------------------
    # Tool-Handler
    # ------------------------------------------------------------------

    def _calendar_write(self, args: dict) -> Response:
        subject = (args.get("subject") or "").strip()
        when = (args.get("when") or "").strip()
        kind = (args.get("kind") or "appointment").strip()

        if not subject or not when:
            return Response(text="❌ Titel und Zeit erforderlich.")

        frame = WriteFrame(
            subject=subject,
            relation="remind" if kind == "reminder" else "",
            when=when,
            note=(args.get("note") or "").strip(),
        )
        plan = self.planner.plan(frame)
        if plan.is_empty:
            return Response(text=plan.summary or "Nichts zu tun.")

        return Response(
            text=f"📋 {plan.summary}\n\nAusführen?",
            plan=plan,
            requires_approval=True,
        )

    def _calendar_edit(self, args: dict) -> Response:
        subject = (args.get("subject") or "").strip()
        when = (args.get("when") or "").strip()

        if not subject or not when:
            return Response(text="❌ Titel und neue Zeit erforderlich.")

        frame = WriteFrame(
            subject=subject,
            relation="reschedule",
            when=when,
        )
        plan = self.planner.plan(frame)
        if plan.is_empty:
            return Response(text=plan.summary or "Nichts zu tun.")

        return Response(
            text=f"📋 {plan.summary}\n\nAusführen?",
            plan=plan,
            requires_approval=True,
        )

    def _calendar_delete(self, args: dict) -> Response:
        subject = (args.get("subject") or "").strip()
        if not subject:
            return Response(text="❌ Titel erforderlich.")

        frame = WriteFrame(subject=subject, relation="cancel")
        plan = self.planner.plan(frame)
        if plan.is_empty:
            return Response(text=plan.summary or "Nichts zu tun.")

        return Response(
            text=f"📋 {plan.summary}\n\nAusführen?",
            plan=plan,
            requires_approval=True,
        )

    def _calendar_read(self, args: dict) -> Response:
        when = (args.get("when") or "").strip()
        subject = (args.get("subject") or "").strip()

        if subject:
            frame = ReadFrame(subject=subject, when=when)
        else:
            frame = ReadFrame(relation="steht an", when=when)
        answer = self.queries.answer(frame)
        return Response(text=answer)

    def _commitment_write(self, args: dict) -> Response:
        person = (args.get("person") or "").strip()
        action = (args.get("action") or "bring").strip()
        item = (args.get("item") or "").strip()

        # Post-Korrektur: "Wer bringt X?" → Read-Query, kein Write
        # (Gemma-Quirk: person='?' bei Fragewort 'wer')
        if person in ("?", "", "wer", "unbekannt", "unknown"):
            if item:
                frame = ReadFrame(relation="bring", object=item)
                answer = self.queries.answer(frame)
                return Response(text=answer)
            return Response(text="❌ Person und Gegenstand erforderlich.")

        if not item:
            return Response(text="❌ Person und Gegenstand erforderlich.")

        frame = WriteFrame(
            subject=person,
            relation=action,
            object=item,
        )
        plan = self.planner.plan(frame)
        if plan.is_empty:
            return Response(text=plan.summary or "Nichts zu tun.")

        return Response(
            text=f"📋 {plan.summary}\n\nAusführen?",
            plan=plan,
            requires_approval=True,
        )

    def _fact_write(self, args: dict) -> Response:
        subject = (args.get("subject") or "").strip()
        value = (args.get("value") or "").strip()

        if not subject or not value:
            return Response(text="❌ Name und Wert erforderlich.")

        frame = WriteFrame(
            subject=subject,
            relation="",
            note=value,
        )
        plan = self.planner.plan(frame)
        if plan.is_empty:
            return Response(text=plan.summary or "Nichts zu tun.")

        return Response(
            text=f"📋 {plan.summary}\n\nAusführen?",
            plan=plan,
            requires_approval=True,
        )

    def _query_answer(self, args: dict) -> Response:
        query_type = (args.get("query_type") or "").strip()
        subject = (args.get("subject") or "").strip()
        when = (args.get("when") or "").strip()

        # Schedule-Queries: Zeit kann in 'subject' stecken
        # (Gemma-Quirk: 'Termine morgen' statt when='morgen')
        if query_type == "schedule" and not when:
            if subject:
                low = subject.lower()
                for w in ("morgen", "heute", "übermorgen", "montag",
                          "dienstag", "mittwoch", "donnerstag", "freitag",
                          "samstag", "sonntag", "woche"):
                    if w in low:
                        when = subject
                        subject = ""
                        break
            if not when:
                when = "heute"  # Default: heute

        if query_type == "fact":
            frame = ReadFrame(subject=subject)
            return Response(text=self.queries.answer(frame))

        if query_type == "commitment":
            frame = ReadFrame(relation="bring", object=subject)
            return Response(text=self.queries.answer(frame))

        if query_type == "history":
            frame = ReadFrame(subject=subject, when="ursprünglich")
            return Response(text=self.queries.answer(frame))

        if query_type == "schedule":
            frame = ReadFrame(relation="steht an", when=when)
            return Response(text=self.queries.answer(frame))

        return Response(text="Unbekannter Query-Typ.")

    def _control(self, args: dict, source: SourceContext) -> Response:
        command = (args.get("command") or "").strip()

        if command == "undo":
            try:
                tx_id = self.store.undo_last(source)
                return Response(
                    text=f"↩️ Letzte Aktion rückgängig gemacht (tx {tx_id}).")
            except Exception as exc:
                return Response(text=f"Undo fehlgeschlagen: {exc}")

        if command == "status":
            stats = _stats(self.store)
            return Response(
                text=(f"📊 World State:\n"
                      f"  Entities: {stats['entities']}\n"
                      f"  Datoms:   {stats['datoms']}\n"
                      f"  Tx:       {stats['txs']}")
            )

        if command == "help":
            return Response(
                text=("ℹ️ Ich bin needle, dein Orga-Assistent.\n\n"
                      "Termine:\n"
                      "  • 'Zahnarzt morgen um 14 Uhr'\n"
                      "  • 'Verschiebe Zahnarzt auf 16 Uhr'\n"
                      "  • 'Sage Zahnarzt ab'\n"
                      "Erinnerungen:\n"
                      "  • 'Erinnere mich in 10 Minuten an Wasser'\n"
                      "Fragen:\n"
                      "  • 'Was habe ich am Mittwoch?'\n"
                      "  • 'Habe ich Termine?'\n"
                      "Fakten:\n"
                      "  • 'Der WLAN-Code ist geheim123'\n"
                      "  • 'Wie lautet der WLAN-Code?'\n"
                      "System:\n"
                      "  • 'Status', 'Undo'")
            )

        if command == "unclear":
            return Response(
                text="Das verstehe ich nicht. "
                     "Kannst du das anders formulieren?")

        return Response(text=f"Unbekannter Befehl: {command}")
