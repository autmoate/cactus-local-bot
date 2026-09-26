"""Deterministic tests for the Thunderbird calendar spike (§27).

No model, no network: `FakeHost` returns scripted extraction responses. All
business rules (headers, received_at reference, approval boundary, fake store /
outbox) are checked here. Model behaviour lives in eval.py, not in these tests.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CASE_FILE = Path(__file__).resolve().parents[1] / "cases.jsonl"

from extractor import _extract  # noqa: E402
from models import MailMessage, ParticipantCandidate  # noqa: E402
from temporal import compile_when  # noqa: E402
from workflow import (CalendarSpike, clean_subject,  # noqa: E402
                      participants_from_message, parse_address)

MY = ("me@example.org",)


class FakeHost:
    model = "fake-base"

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []
        self.closed = False

    def extract(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.response

    def reset(self) -> dict:
        return {"status": "ok"}

    def close(self) -> None:
        self.closed = True


def _candidate(title="Review", when="am 13.10. um 14 Uhr", location=""):
    return {"status": "candidate",
            "candidates": [{"title": title, "when": when, "location": location}],
            "confidence": 0.5, "latency_ms": 12, "raw": {"raw": []}}


def _message(body="Das Review ist am 13.10. um 14 Uhr.",
             selection=None) -> MailMessage:
    return MailMessage(id="m1", subject="Review",
                       sender="Lisa <lisa@example.org>",
                       to=["Ich <me@example.org>", "Max <max@example.org>"],
                       cc=["Max <max@example.org>", "Team <team@example.org>"],
                       received_at=datetime(2026, 9, 25, 10, 0),
                       body_text=body)


def test_parse_address_normalizes():
    assert parse_address("Lisa <lisa@example.org>") == ("Lisa", "lisa@example.org")
    assert parse_address("MAX@Example.ORG") == ("max", "max@example.org")


def test_own_address_excluded_and_duplicates_deduped():
    participants = participants_from_message(_message(), MY)
    emails = [p.email for p in participants]
    assert "me@example.org" not in emails
    assert emails.count("max@example.org") == 1
    assert set(emails) == {"lisa@example.org", "max@example.org", "team@example.org"}
    assert next(p for p in participants if p.email == "max@example.org").source == "to"


def test_selection_wins_over_full_body():
    host = FakeHost(_candidate())
    spike = CalendarSpike(host, my_addresses=MY)
    message = _message(body="Lang und unübersichtlich.", selection=None)
    spike.extract(message, mode="selection", selection="Review am 13.10. um 14 Uhr")
    assert host.calls[-1]["text"] == "Review am 13.10. um 14 Uhr"
    assert host.calls[-1]["mode"] == "selection"


def test_message_mode_sends_body():
    host = FakeHost(_candidate())
    spike = CalendarSpike(host, my_addresses=MY)
    spike.extract(_message(body="Nur das Review am 13.10."), mode="message")
    assert host.calls[-1]["text"] == "Nur das Review am 13.10."


def test_received_at_is_temporal_reference():
    host = FakeHost(_candidate(when="morgen um 14 Uhr"))
    spike = CalendarSpike(host, my_addresses=MY)
    result = spike.extract(_message(), mode="message")
    cand = result["candidates"][0]
    assert cand.start == datetime(2026, 9, 26, 14, 0)
    assert cand.end == datetime(2026, 9, 26, 15, 0)
    assert host.calls[-1]["reference_time"] == "2026-09-25T10:00:00"


def test_no_event_creates_no_write():
    host = FakeHost({"status": "none", "candidates": [], "confidence": 0.1,
                     "latency_ms": 5, "raw": {"raw": []}})
    spike = CalendarSpike(host, my_addresses=MY)
    result = spike.extract(_message(body="Kannst du das Dokument schicken?"),
                           mode="message")
    assert result["candidates"] == []
    assert spike.store.events == []
    assert spike.outbox.sent == []


def test_calendar_write_only_after_button():
    host = FakeHost(_candidate())
    spike = CalendarSpike(host, my_addresses=MY)
    result = spike.extract(_message(), mode="message")
    assert spike.store.events == []  # extraction alone must not write
    spike.commit(result["candidates"][0], [])
    assert len(spike.store.events) == 1


def test_invitation_only_after_button_and_fake_outbox():
    host = FakeHost(_candidate())
    spike = CalendarSpike(host, my_addresses=MY)
    result = spike.extract(_message(), mode="message")
    invitees = [ParticipantCandidate("Lisa", "lisa@example.org", "from")]
    draft = spike.prepare_invitation(result["candidates"][0], invitees)
    assert spike.outbox.sent == []  # preparing must not send
    assert spike.store.events == []  # and must not write
    entry = spike.send_invitation(draft)
    assert len(spike.outbox.sent) == 1
    assert entry.draft["event"]["title"] == "Review"


def test_candidate_edit_changes_preview_only():
    host = FakeHost(_candidate())
    spike = CalendarSpike(host, my_addresses=MY)
    cand = spike.extract(_message(), mode="message")["candidates"][0]
    edited = cand
    edited.title = "Review (korrigiert)"
    draft = spike.prepare_invitation(edited, [])
    assert draft.event.title == "Review (korrigiert)"
    assert spike.store.events == []


def test_compiler_all_day_span_and_default_duration():
    ref = datetime(2026, 9, 25, 10, 0)
    span = compile_when("Die Tagung findet am 7. und 8. Oktober statt", ref)
    assert span.all_day and span.start == datetime(2026, 10, 7)
    assert span.end == datetime(2026, 10, 9)
    timed = compile_when("am 12.10. um 14 Uhr", ref)
    assert not timed.all_day and timed.used_default_duration
    assert timed.end == datetime(2026, 10, 12, 15, 0)
    incomplete = compile_when("14:00", ref)
    assert incomplete.incomplete and incomplete.start is None


class FakeEngine:
    """Captures the exact prompt the worker would hand to Needle (§3)."""

    def __init__(self, args=None):
        self.prompts = []
        self._args = args or {"title": "Review", "when": "13.10. um 14 Uhr",
                              "location": ""}

    def reset(self):
        return None

    def complete(self, prompt):
        self.prompts.append(prompt)
        return {"function_calls": [{"name": "extract_event",
                                    "arguments": self._args}],
                "confidence": None}


def test_selection_mode_sends_selected_text_only():
    engine = FakeEngine()
    _extract(engine, {"text": "Dienstag 14 Uhr passt.",
                      "subject": "Re: Projekt Alpha", "mode": "selection"})
    assert engine.prompts[-1] == "Dienstag 14 Uhr passt."
    assert "Betreff" not in engine.prompts[-1]


def test_message_mode_includes_subject():
    engine = FakeEngine()
    _extract(engine, {"text": "Dienstag 14 Uhr passt.",
                      "subject": "Projekt Alpha", "mode": "message"})
    assert engine.prompts[-1] == "Betreff: Projekt Alpha\n\nDienstag 14 Uhr passt."


def test_clean_subject_strips_reply_prefixes():
    assert clean_subject("Re: Projekt Alpha") == "Projekt Alpha"
    assert clean_subject("AW:  Fwd: Workshop") == "Workshop"
    assert clean_subject("WG: Termin") == "Termin"
    assert clean_subject("") == ""


def test_empty_model_title_falls_back_to_subject():
    host = FakeHost(_candidate(title=""))
    spike = CalendarSpike(host, my_addresses=MY)
    message = MailMessage(id="m2", subject="Re: Projekt Alpha",
                          sender="Lisa <lisa@example.org>",
                          to=["me@example.org"],
                          received_at=datetime(2026, 9, 25, 10, 0),
                          body_text="Dienstag 14 Uhr passt.")
    cand = spike.extract(message, mode="message")["candidates"][0]
    assert cand.title == "Projekt Alpha"
    assert "Titel aus Betreff" in cand.review_reasons


def test_explicit_model_title_wins_over_subject():
    host = FakeHost(_candidate(title="Projektgespräch"))
    spike = CalendarSpike(host, my_addresses=MY)
    message = MailMessage(id="m3", subject="Re: Projekt Alpha",
                          sender="Lisa <lisa@example.org>",
                          to=["me@example.org"],
                          received_at=datetime(2026, 9, 25, 10, 0),
                          body_text="Projektgespräch Dienstag 14 Uhr.")
    cand = spike.extract(message, mode="message")["candidates"][0]
    assert cand.title == "Projektgespräch"


def test_trailing_period_keeps_time_range():
    """Regression: a sentence-final '.' must not be mistaken for a day dot."""
    ref = datetime(2026, 9, 25, 10, 0)
    with_dot = compile_when("am 21.10. von 15 bis 17 Uhr.", ref)
    assert with_dot.start == datetime(2026, 10, 21, 15, 0)
    assert with_dot.end == datetime(2026, 10, 21, 17, 0)
    assert not with_dot.used_default_duration


def test_compiler_matches_gold_spans():
    """Locks the 'compiler parses the authored spans correctly' claim: for every
    single-event authored mail, the deterministic compiler must reproduce the
    fixture's gold start/end from the mail body."""
    cases = [json.loads(ln) for ln in
             CASE_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    single = [c for c in cases
              if c["category"] in {"explicit_single", "relative", "location",
                                   "all_day"}
              and c["expected"]["event_count"] == 1]
    assert len(single) == 43
    for case in single:
        expected = case["expected"]
        timing = compile_when(case["message"]["body"],
                              datetime.fromisoformat(case["message"]["received_at"]))
        got = (timing.start.strftime("%Y-%m-%dT%H:%M:%S") if timing.start else "",
               timing.end.strftime("%Y-%m-%dT%H:%M:%S") if timing.end else "")
        assert got == (expected["start"], expected["end"]), case["id"]
