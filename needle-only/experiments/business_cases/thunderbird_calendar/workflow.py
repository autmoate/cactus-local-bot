"""Business logic for the spike (§8-§15, §23, §24).

Human-in-the-loop is part of the product: `extract` only proposes candidates;
`commit` / `prepare_invitation` / `send_invitation` run exclusively from UI
buttons. Participants come ONLY from structured headers (From/To/Cc), never from
Needle and never from body regexes.
"""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parseaddr

from models import (EventCandidate, InvitationDraft, MailMessage, OutboxEntry,
                    ParticipantCandidate)
from temporal import compile_when

_SUBJECT_PREFIX = re.compile(
    r"^\s*(?:(?:re|aw|antw|antwort|fwd|fw|wg|fyi)\s*:\s*)+", re.IGNORECASE)


def clean_subject(subject: str) -> str:
    """Generic subject cleanup only (strip Re/AW/Fwd/WG prefixes). No semantic
    title generation — the model supplies the title when the text states one."""
    return _SUBJECT_PREFIX.sub("", subject or "").strip()


def parse_address(entry: str) -> tuple[str, str]:
    name, email = parseaddr(entry or "")
    email = (email or "").strip().lower()
    if not name:
        name = email.split("@")[0] if email else ""
    return name.strip(), email


def participants_from_message(message: MailMessage, my_addresses=()) -> list[ParticipantCandidate]:
    """Structured-header truth, deduplicated, own addresses excluded."""
    mine = {a.lower() for a in my_addresses}
    seen: set[str] = set()
    out: list[ParticipantCandidate] = []
    for source, header in (("from", [message.sender]), ("to", message.to),
                           ("cc", message.cc)):
        for entry in header or []:
            if not entry:
                continue
            name, email = parse_address(entry)
            if not email or email in mine or email in seen:
                continue
            seen.add(email)
            out.append(ParticipantCandidate(name=name or email, email=email,
                                            source=source))
    return out


class FakeCalendarStore:
    """In-memory calendar; nothing leaves the process (§14)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def add(self, candidate: EventCandidate,
            invitees: list[ParticipantCandidate]) -> dict:
        entry = {
            "title": candidate.title,
            "start": candidate.start,
            "end": candidate.end,
            "all_day": candidate.all_day,
            "location": candidate.location,
            "participants": [f"{p.name} <{p.email}>" for p in invitees],
        }
        self.events.append(entry)
        return entry

    def clear(self) -> None:
        self.events.clear()

    def rows(self) -> list[list]:
        rows = []
        for e in sorted(self.events, key=lambda x: x["start"] or datetime.max):
            if e["all_day"] and e["start"]:
                when = f"{e['start'].date().isoformat()} (ganztägig)"
                span = f"{e['start'].date().isoformat()} – {(e['end'].date()).isoformat()}"
            else:
                span = (f"{e['start']:%d.%m.%Y %H:%M} – {e['end']:%H:%M}"
                        if e["start"] and e["end"] else "")
                when = e["start"].strftime("%d.%m.%Y") if e["start"] else ""
            rows.append([when, span, e["title"], e["location"],
                         ", ".join(e["participants"])])
        return rows


class FakeOutbox:
    """No SMTP, no compose, no iTIP: only a local list (§15)."""

    def __init__(self) -> None:
        self.sent: list[OutboxEntry] = []

    def put(self, draft: InvitationDraft) -> OutboxEntry:
        entry = OutboxEntry(draft=draft.to_dict(), sent_at=datetime.now())
        self.sent.append(entry)
        return entry

    def clear(self) -> None:
        self.sent.clear()


class CalendarSpike:
    def __init__(self, host, my_addresses=("me@example.org",),
                 store: FakeCalendarStore | None = None,
                 outbox: FakeOutbox | None = None) -> None:
        self.host = host
        self.my_addresses = tuple(my_addresses)
        self.store = store or FakeCalendarStore()
        self.outbox = outbox or FakeOutbox()

    @property
    def organizer(self) -> str:
        return self.my_addresses[0] if self.my_addresses else "me@example.org"

    def participants(self, message: MailMessage) -> list[ParticipantCandidate]:
        return participants_from_message(message, self.my_addresses)

    def extract(self, message: MailMessage, mode: str = "message",
                selection: str | None = None) -> dict:
        if mode == "selection":
            source = (selection or "").strip()
            if not source:
                return {"status": "invalid", "error": "no selection text",
                        "candidates": [], "raw": {}, "latency_ms": 0}
        else:
            source = message.body_text or ""
        reference = message.received_at.isoformat() if message.received_at else ""
        response = self.host.extract(text=source, subject=message.subject,
                                     reference_time=reference, mode=mode)
        status = response.get("status", "error")
        candidates = self._compile(response.get("candidates") or [], message,
                                   source, mode, response)
        return {"status": status, "candidates": candidates,
                "error": response.get("error", ""),
                "latency_ms": response.get("latency_ms", 0),
                "confidence": response.get("confidence"),
                "raw": response}

    def _compile(self, raw_candidates: list[dict], message: MailMessage,
                 source: str, mode: str, response: dict) -> list[EventCandidate]:
        ref = message.received_at or datetime.now()
        multiple = len(raw_candidates) > 1
        subject_title = clean_subject(message.subject)
        out: list[EventCandidate] = []
        for raw in raw_candidates:
            timing = compile_when(raw.get("when", ""), ref)
            reasons = []
            if timing.incomplete:
                reasons.append("Zeitpunkt unvollständig")
            if timing.used_default_duration:
                reasons.append("Defaultdauer verwendet")
            if multiple:
                reasons.append("mehrere Termine erkannt")
            model_title = (raw.get("title") or "").strip()
            if not model_title and subject_title:
                reasons.append("Titel aus Betreff")
            out.append(EventCandidate(
                title=model_title or subject_title,
                start=timing.start,
                end=timing.end,
                all_day=timing.all_day,
                location=raw.get("location", ""),
                source_message_id=message.id,
                source_text=source,
                extraction_mode=mode,
                model=self.host.model,
                confidence=response.get("confidence"),
                status="incomplete" if timing.incomplete else "candidate",
                needs_review=bool(reasons),
                review_reasons=reasons,
                used_default_duration=timing.used_default_duration,
            ))
        return out

    # -- human approval boundary ------------------------------------------
    def commit(self, candidate: EventCandidate,
               invitees: list[ParticipantCandidate]) -> dict:
        return self.store.add(candidate, invitees)

    def prepare_invitation(self, candidate: EventCandidate,
                           invitees: list[ParticipantCandidate]) -> InvitationDraft:
        return InvitationDraft(event=candidate, invitees=invitees,
                               organizer=self.organizer)

    def send_invitation(self, draft: InvitationDraft) -> OutboxEntry:
        return self.outbox.put(draft)


def to_ics(draft: InvitationDraft) -> str:
    """Tiny optional .ics preview — no sending, no iTIP."""
    ev = draft.event
    if not ev.start or not ev.end:
        return ""
    fmt = "%Y%m%dT%H%M%S" if not ev.all_day else "%Y%m%d"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//tb-calendar-spike//DE",
             "BEGIN:VEVENT", f"SUMMARY:{ev.title}",
             f"DTSTART:{ev.start.strftime(fmt)}", f"DTEND:{ev.end.strftime(fmt)}",
             f"LOCATION:{ev.location}",
             f"ORGANIZER:mailto:{draft.organizer}"]
    lines += [f"ATTENDEE:mailto:{p.email}" for p in draft.invitees]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines)
