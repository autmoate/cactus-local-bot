"""Plain data models for the Thunderbird -> Calendar feasibility spike (§2, §8).

No business logic here: only the shapes that the mail port, the extractor, the
temporal compiler, the workflow and the UI exchange. `to_dict` keeps the shapes
JSON-serializable for the subprocess protocol, the Gradio state and the eval.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime

PARTICIPANT_SOURCES = ("from", "to", "cc")
EXTRACTION_MODES = ("message", "selection")


@dataclass
class MailMessage:
    id: str
    subject: str
    sender: str
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    received_at: datetime | None = None
    body_text: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["received_at"] = self.received_at.isoformat() if self.received_at else None
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "MailMessage":
        raw = data.get("received_at")
        return cls(
            id=data.get("id", ""),
            subject=data.get("subject", ""),
            sender=data.get("sender", ""),
            to=list(data.get("to") or []),
            cc=list(data.get("cc") or []),
            received_at=datetime.fromisoformat(raw) if raw else None,
            body_text=data.get("body_text", ""),
        )


@dataclass
class MailSelection:
    message_id: str
    selected_text: str


@dataclass
class ParticipantCandidate:
    name: str
    email: str
    source: str  # from|to|cc


@dataclass
class Timing:
    """Result of the deterministic temporal compiler (§7)."""

    start: datetime | None
    end: datetime | None
    all_day: bool
    incomplete: bool = False
    used_default_duration: bool = False
    note: str = ""


@dataclass
class EventCandidate:
    title: str
    start: datetime | None
    end: datetime | None
    all_day: bool
    location: str
    source_message_id: str
    source_text: str
    extraction_mode: str
    model: str
    confidence: float | None = None
    status: str = "candidate"
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)
    used_default_duration: bool = False
    rsvp: bool = False  # @rsvp marker in the response cards (§14)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("start", "end"):
            d[k] = self.start.isoformat() if k == "start" and self.start else (
                self.end.isoformat() if k == "end" and self.end else d[k])
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "EventCandidate":
        def _dt(v):
            return datetime.fromisoformat(v) if v else None
        return cls(
            title=data.get("title", ""),
            start=_dt(data.get("start")),
            end=_dt(data.get("end")),
            all_day=bool(data.get("all_day")),
            location=data.get("location", ""),
            source_message_id=data.get("source_message_id", ""),
            source_text=data.get("source_text", ""),
            extraction_mode=data.get("extraction_mode", "message"),
            model=data.get("model", ""),
            confidence=data.get("confidence"),
            status=data.get("status", "candidate"),
            needs_review=bool(data.get("needs_review")),
            review_reasons=list(data.get("review_reasons") or []),
            used_default_duration=bool(data.get("used_default_duration")),
            rsvp=bool(data.get("rsvp")),
        )


@dataclass
class InvitationDraft:
    event: EventCandidate
    invitees: list[ParticipantCandidate]
    organizer: str

    def to_dict(self) -> dict:
        return {
            "event": self.event.to_dict(),
            "invitees": [asdict(p) for p in self.invitees],
            "organizer": self.organizer,
        }


@dataclass
class OutboxEntry:
    draft: dict
    sent_at: datetime

    def to_dict(self) -> dict:
        return {"draft": self.draft, "sent_at": self.sent_at.isoformat()}
