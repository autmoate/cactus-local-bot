#!/usr/bin/env python3
"""Native Messaging host (Phase 1, thin adapter).

Thunderbird <-> this host speak the WebExtension Native Messaging protocol:
a 4-byte little-endian length prefix followed by UTF-8 JSON, on stdin/stdout.
No other output may go to stdout (logs go to stderr).

This file contains NO business logic: it maps the Thunderbird payload to the
existing spike `MailMessage`, calls the existing `CalendarSpike` (the same code
the Gradio app uses), and maps the result back to JSON. No calendar write, no
mail persistence, no network. Weights come from TB_NATIVE_WEIGHTS.
"""
from __future__ import annotations

import json
import os
import struct
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPIKE = HERE.parents[1]
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

from models import MailMessage  # noqa: E402
from workflow import CalendarSpike  # noqa: E402

MAX_BYTES = 8 * 1024 * 1024


def read_message(stream) -> dict | None:
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    (length,) = struct.unpack("<I", raw)
    if length > MAX_BYTES:
        raise ValueError(f"native message too large: {length}")
    payload = stream.read(length)
    if len(payload) < length:
        return None
    return json.loads(payload.decode("utf-8"))


def send_message(stream, obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("<I", len(data)))
    stream.write(data)
    stream.flush()


def payload_to_message(payload: dict) -> MailMessage:
    m = payload.get("message") or {}
    from_list = m.get("from") or []
    if isinstance(from_list, str):
        from_list = [from_list]
    raw_recv = m.get("received_at")
    return MailMessage(
        id=str(m.get("id") or ""),
        subject=m.get("subject", ""),
        sender=(from_list[0] if from_list else ""),
        to=list(m.get("to") or []),
        cc=list(m.get("cc") or []),
        received_at=datetime.fromisoformat(raw_recv) if raw_recv else None,
        body_text=m.get("body_text") or "",
    )


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def candidate_to_payload(c) -> dict:
    return {"title": c.title, "start": _iso(c.start), "end": _iso(c.end),
            "all_day": c.all_day, "location": c.location, "status": c.status,
            "needs_review": c.needs_review,
            "review_reasons": list(c.review_reasons),
            "extraction_mode": c.extraction_mode, "confidence": c.confidence}


def raw_when(result: dict) -> str:
    for call in (result.get("raw") or {}).get("raw") or []:
        if call.get("name") == "extract_event":
            return (call.get("arguments") or {}).get("when", "")
    return ""


def handle(payload: dict, spike) -> dict:
    message = payload_to_message(payload)
    mode = payload.get("mode", "message")
    selection = payload.get("selection")
    result = spike.extract(message, mode=mode, selection=selection)
    return {"status": result.get("status", "error"),
            "model": spike.host.model,
            "latency_ms": result.get("latency_ms", 0),
            "confidence": result.get("confidence"),
            "error": result.get("error", ""),
            "raw_when": raw_when(result),
            "candidates": [candidate_to_payload(c)
                           for c in result.get("candidates", [])]}


def build_spike() -> CalendarSpike:
    from extractor import LocalNeedleHost
    backend = os.environ.get("TB_NATIVE_BACKEND", "n2")
    weights = os.environ.get("TB_NATIVE_WEIGHTS") or None
    return CalendarSpike(LocalNeedleHost(backend, weights=weights))


def main() -> int:
    try:
        spike = build_spike()
    except Exception as exc:  # noqa: BLE001
        send_message(sys.stdout.buffer, {"status": "error",
                                         "error": f"host init failed: {exc}"})
        return 1
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            request = read_message(stdin)
        except Exception as exc:  # noqa: BLE001
            send_message(stdout, {"status": "error", "error": str(exc)})
            break
        if request is None:
            break
        try:
            response = handle(request, spike)
        except Exception as exc:  # noqa: BLE001
            response = {"status": "error", "candidates": [],
                        "error": f"{type(exc).__name__}: {exc}"}
        send_message(stdout, response)
    spike.host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
