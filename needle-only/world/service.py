"""Orga v6 — WorldService: Zentrale Orchestrierung.

Fester Pipeline-Ablauf (kein Agent Loop):

    Text
      ↓ Routing (deterministisch: CONTROL > READ > WRITE)
      ↓
    WRITE:  interpret → plan → preview → [approval] → apply → render
    READ:   interpret → query → render
    CONTROL: deterministischer Handler (undo, status, help)

Telegram/TUI sprechen NUR mit diesem Service. Needle wird
ausschließlich im Interpreter aufgerufen.
"""
from __future__ import annotations

import re

from .interpreter import NeedleInterpreter
from .model import (
    ReadFrame,
    Response,
    SourceContext,
    TransactionPlan,
    WriteFrame,
)
from .planner import WritePlanner
from .query import QueryEngine
from .resolver import Resolver
from .store import WorldStore

# =====================================================================
# Routing (deterministisch)
# =====================================================================

_READ_MARKERS = (
    "wer ", "was ", "wann ", "wie ", "welche ", "welcher ", "wo ",
    "zeigt", "liste", "steht an", "steht", "gibt es", "habe ich",
    "hab ich", "was steht", "was kommt", "was weißt", "was weisst",
    "wer ist", "wer sind", "wer bringt", "wer kommt",
)
_CONTROL_COMMANDS = (
    "/undo", "undo", "/status", "status", "/help", "help",
    "/cancel", "cancel", "abbrechen",
)


def route(text: str) -> str:
    """Deterministisches Routing: CONTROL > READ > WRITE.

    Syntax/Frageform schlägt Semantik:
    - Fragewörter/Read-Muster → READ
    - Alles eindeutig Deklarative → WRITE (Default)
    - Kontroll-Commands → CONTROL
    """
    low = text.lower().strip()

    # CONTROL zuerst
    if low in _CONTROL_COMMANDS or low.startswith("/"):
        return "CONTROL"

    # READ: Frage-/Read-Muster
    if low.endswith("?") or low.endswith("? "):
        return "READ"
    for marker in _READ_MARKERS:
        if low.startswith(marker) or f" {marker}" in f" {low}":
            return "READ"

    # WRITE: alles Deklarative (Default)
    return "WRITE"


# =====================================================================
# WorldService
# =====================================================================


class WorldService:
    """Zentrale API für alle Adapter (TUI, Telegram, Matrix)."""

    def __init__(self, store: WorldStore,
                 interpreter: NeedleInterpreter | None = None):
        self.store = store
        self.resolver = Resolver(store)
        self.planner = WritePlanner(store, self.resolver)
        self.queries = QueryEngine(store, self.resolver)
        self.interpreter = interpreter or NeedleInterpreter()

    # ------------------------------------------------------------------
    # Haupteinstieg
    # ------------------------------------------------------------------

    def handle(self, text: str, source: SourceContext) -> Response:
        """Text → Route → Response."""
        target = route(text)

        if target == "WRITE":
            return self._handle_write(text, source)
        if target == "READ":
            return self._handle_read(text)
        return self._handle_control(text, source)

    # ------------------------------------------------------------------
    # WRITE
    # ------------------------------------------------------------------

    def _handle_write(self, text: str, source: SourceContext) -> Response:
        frame = self.interpreter.write_frame(text)
        plan = self.planner.plan(frame)

        if plan.is_empty:
            return Response(text=plan.summary or "Nichts zu tun.")

        if plan.warnings:
            # Ambiguität → keine automatische Ausführung
            return Response(
                text=(f"⚠️ {plan.summary}\n"
                      f"Warnungen: {'; '.join(plan.warnings)}\n"
                      f"Trotzdem ausführen?"),
                plan=plan,
                requires_approval=True,
            )

        # Preview / Approval-Flow
        return Response(
            text=f"📋 {plan.summary}\n\nAusführen?",
            plan=plan,
            requires_approval=True,
        )

    def apply(self, plan: TransactionPlan, source: SourceContext) -> int:
        """Führt einen TransactionPlan aus (nach Approval)."""
        return self.store.apply_plan(plan, source)

    # ------------------------------------------------------------------
    # READ
    # ------------------------------------------------------------------

    def _handle_read(self, text: str) -> Response:
        frame = self.interpreter.read_frame(text)
        answer = self.queries.answer(frame)
        return Response(text=answer)

    # ------------------------------------------------------------------
    # CONTROL
    # ------------------------------------------------------------------

    def _handle_control(self, text: str,
                        source: SourceContext) -> Response:
        low = text.lower().strip()

        if low in ("/undo", "undo"):
            return self.undo(source)

        if low in ("/status", "status"):
            return self._status()

        if low in ("/help", "help"):
            return Response(text=self._help_text())

        if low in ("/cancel", "cancel", "abbrechen"):
            return Response(text="Abgebrochen.")

        return Response(text="Unbekanntes Kommando.")

    def undo(self, source: SourceContext) -> Response:
        """Undo der letzten Transaction."""
        try:
            tx_id = self.store.undo_last(source)
            return Response(
                text=f"↩️ Letzte Aktion rückgängig gemacht (tx {tx_id}).",
                applied_tx_id=tx_id,
            )
        except Exception as exc:
            return Response(text=f"Undo fehlgeschlagen: {exc}")

    def _status(self) -> Response:
        """Status-Übersicht (Anzahl Entities, Datoms, Transactions)."""
        with self.store._connect() as conn:
            entity_count = conn.execute(
                "SELECT count(DISTINCT entity_id) AS c "
                "FROM world_datom").fetchone()["c"]
            datom_count = conn.execute(
                "SELECT count(*) AS c FROM world_datom").fetchone()["c"]
            tx_count = conn.execute(
                "SELECT count(*) AS c FROM world_tx").fetchone()["c"]

        return Response(
            text=(f"📊 World State:\n"
                  f"  Entities: {entity_count}\n"
                  f"  Datoms:   {datom_count}\n"
                  f"  Tx:       {tx_count}")
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "Orga v6 — World Model Bot\n\n"
            "Schreiben:\n"
            "  „Zahnarzt morgen um 14 Uhr\"\n"
            "  „Julia bringt den Beamer\"\n"
            "  „Der WLAN-Code ist foo123\"\n\n"
            "Fragen:\n"
            "  „Was steht morgen an?\"\n"
            "  „Wer bringt den Beamer?\"\n"
            "  „Wie lautet der WLAN-Code?\"\n\n"
            "Kommandos: /undo /status /help /cancel"
        )
