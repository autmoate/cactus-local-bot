"""Deterministic tests for the Phase-1 native host. No model, no network."""

from __future__ import annotations

import io
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "native"))
sys.path.insert(0, str(HERE.parent))

import native_host  # noqa: E402
from models import EventCandidate  # noqa: E402


class FakeHost:
    model = "fake:n2-train.cact"


class FakeSpike:
    def __init__(self, result):
        self.host = FakeHost()
        self._result = result
        self.calls = []

    def extract(self, message, mode="message", selection=None):
        self.calls.append({"message": message, "mode": mode,
                           "selection": selection})
        return self._result


def _result():
    return {"status": "candidate", "error": "", "latency_ms": 42,
            "confidence": None,
            "candidates": [EventCandidate(
                title="Projekt Alpha", start=datetime(2026, 9, 29, 14, 0),
                end=datetime(2026, 9, 29, 15, 0), all_day=False, location="",
                source_message_id="7", source_text="Dienstag 14 Uhr passt mir.",
                extraction_mode="message", model="fake", status="candidate",
                needs_review=False, review_reasons=[])],
            "raw": {"raw": [{"name": "extract_event",
                             "arguments": {"when": "Dienstag 14 Uhr"}}]}}


def test_framing_roundtrip():
    buf = io.BytesIO()
    native_host.send_message(buf, {"status": "candidate", "n": 1})
    buf.seek(0)
    assert native_host.read_message(buf) == {"status": "candidate", "n": 1}
    assert native_host.read_message(io.BytesIO(b"")) is None


def test_payload_to_message_mapping():
    payload = {"message": {"id": 7, "subject": "Projekt Alpha",
                           "from": ["Lisa <lisa@example.org>"],
                           "to": ["me@example.org"], "cc": [],
                           "received_at": "2026-09-25T10:00:00",
                           "body_text": "Dienstag 14 Uhr passt mir."}}
    message = native_host.payload_to_message(payload)
    assert message.id == "7"
    assert message.sender == "Lisa <lisa@example.org>"
    assert message.received_at == datetime(2026, 9, 25, 10, 0)
    assert message.to == ["me@example.org"]


def test_payload_from_string_is_tolerated():
    message = native_host.payload_to_message({"message": {"from": "a@b.c"}})
    assert message.sender == "a@b.c"


def test_handle_returns_candidate_and_raw_when():
    response = native_host.handle({"message": {"id": "1", "body_text": "x"}},
                                  FakeSpike(_result()))
    assert response["status"] == "candidate"
    assert response["model"] == "fake:n2-train.cact"
    assert response["raw_when"] == "Dienstag 14 Uhr"
    candidate = response["candidates"][0]
    assert candidate["title"] == "Projekt Alpha"
    assert candidate["start"] == "2026-09-29T14:00:00"
    assert candidate["needs_review"] is False


def test_handle_passes_mode_and_selection():
    spike = FakeSpike(_result())
    native_host.handle({"message": {"id": "1"}, "mode": "selection",
                        "selection": "Dienstag 14 Uhr passt."}, spike)
    assert spike.calls[-1]["mode"] == "selection"
    assert spike.calls[-1]["selection"] == "Dienstag 14 Uhr passt."


def test_response_never_echoes_mail_body():
    response = native_host.handle(
        {"message": {"id": "1", "body_text": "streng vertraulicher Inhalt"}},
        FakeSpike(_result()))
    assert "streng vertraulicher Inhalt" not in json.dumps(response)


def test_manifest_is_valid_json_and_matches_host_name():
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["permissions"] == ["messagesRead", "nativeMessaging"]
    host_manifest = json.loads(
        (HERE / "native" / "de.example.tb_calendar_extract.json").read_text())
    assert "tb-calendar-spike@example.org" in \
        host_manifest["allowed_extensions"]
