"""Mail port simulation (§2, §3).

`MailPort` is the ONLY surface the business logic is allowed to see. Today it is
backed by `SimulatedThunderbird` (fixture or free Gradio input); later the same
interface can be implemented by a real `ThunderbirdMailExtensionAdapter` without
touching workflow/temporal/extractor.
"""

from __future__ import annotations

from typing import Protocol

from models import MailMessage


def synthetic_message(*, sender: str = "", to=None, cc=None, subject: str = "",
                      received_at=None, body: str = "", id: str = "manual") -> MailMessage:
    return MailMessage(
        id=id,
        subject=subject,
        sender=sender,
        to=list(to or []),
        cc=list(cc or []),
        received_at=received_at,
        body_text=body,
    )


class MailPort(Protocol):
    def displayed_message(self) -> MailMessage:
        ...

    def selected_text(self) -> str | None:
        ...


class SimulatedThunderbird:
    """In-memory stand-in for the Thunderbird current-message / selection pair."""

    def __init__(self, message: MailMessage | None = None,
                 selection: str | None = None) -> None:
        self._message = message
        self._selection = selection

    def load(self, message: MailMessage, selection: str | None = None) -> None:
        self._message = message
        self._selection = selection

    def clear(self) -> None:
        self._message = None
        self._selection = None

    def displayed_message(self) -> MailMessage:
        if self._message is None:
            raise RuntimeError("no message loaded into the simulated mailbox")
        return self._message

    def selected_text(self) -> str | None:
        text = (self._selection or "").strip()
        return text or None
