"""Orga v6 — World Model Dataclasses.

Kern-Prinzip: Needle erzeugt nur Frames (reine Sprache),
der Planner übersetzt Frames in DatomChanges,
der Store schreibt append-only nach Postgres.

Needle erzeugt niemals: UUIDs, SQL, Tabellen, Postgres-IDs,
direkte Datoms oder CRUD-Befehle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


# =====================================================================
# Datoms (atomare Fakten)
# =====================================================================


@dataclass
class DatomChange:
    """Eine geplante atomare Zustandsänderung.

    Semantik: [+/-] [entity attribute (value | ref_entity)]
    Genau EINES von value / ref_entity muss gesetzt sein
    (bei added=True; bei added=False muss das retractete
    value/ref_entity mit dem aktiven Datom übereinstimmen).
    """

    entity_id: UUID
    attribute: str
    value: Any = None
    ref_entity: UUID | None = None
    added: bool = True


@dataclass
class DatomRecord:
    """Ein gespeichertes Datom (Zeile aus world_datom)."""

    id: int
    entity_id: UUID
    attribute: str
    value: Any
    ref_entity: UUID | None
    tx_id: int
    added: bool


# =====================================================================
# Frames (Sprache, von Needle oder Regeln erzeugt)
# =====================================================================


@dataclass
class WriteFrame:
    """Sprachliches Write-Frame.

    subject:  Wer handelt ("Julia")
    relation: Was passiert ("bringt")
    object:   Worauf ("Beamer")
    context:  Wo/wann ("beim Treffen")
    when:     Roh-Zeitangabe ("morgen um 14 Uhr")
    note:     Freitext ("Der WLAN-Code ist foo123")
    intent_hint: Needle-Rat ("replace" bei "doch/übernimmt",
                  "retract" bei "doch nicht") — nur Hinweis,
                  der Planner entscheidet final aus World State.
    """

    subject: str = ""
    relation: str = ""
    object: str = ""
    context: str = ""
    when: str = ""
    note: str = ""
    intent_hint: str = ""


@dataclass
class ReadFrame:
    """Sprachliches Read-Frame (Query-Intention)."""

    subject: str = ""
    relation: str = ""
    object: str = ""
    context: str = ""
    when: str = ""


# =====================================================================
# Planner / Transaktionen
# =====================================================================


@dataclass
class TransactionPlan:
    """Ergebnis des Write-Planners.

    changes: Atomare Datom-Änderungen (add/retract)
    summary: Menschliche Zusammenfassung für Approval-UX
    confidence: 0.0-1.0 (bestimmt Approval-Flow)
    warnings: Ambiguitäten, die der Nutzer klären sollte
    """

    changes: list[DatomChange] = field(default_factory=list)
    summary: str = ""
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.changes


@dataclass
class TxRecord:
    """Eine abgeschlossene Transaction (aus world_tx)."""

    id: int
    created_at: datetime
    actor: str
    source_type: str | None
    source_id: str | None
    raw_text: str | None


# =====================================================================
# Source / Response (Service-Schnittstelle)
# =====================================================================


@dataclass
class SourceContext:
    """Woher kommt die Nutzeraktion? (Provenance je Transaction)"""

    source_type: str = "tui"  # tui | telegram | matrix | import | system
    source_id: str = ""       # z.B. "<chat_id>:<message_id>"
    actor: str = "ich"        # Wer hat den Text geschrieben
    device_id: str = ""
    raw_text: str = ""        # Ursprüngliche Nachricht (bleibt erhalten)


@dataclass
class Response:
    """Antwort des WorldService an den Adapter (TUI/Telegram)."""

    text: str = ""
    plan: TransactionPlan | None = None
    requires_approval: bool = False
    applied_tx_id: int | None = None
