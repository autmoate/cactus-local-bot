"""Shared test fixtures. The Needle engine's decode decisions are sensitive to
the system facts string (it contains the live time), so tests freeze it."""

import pytest

from local_calendar import agent as agent_mod

FIXED_FACTS = "date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi"


@pytest.fixture(autouse=True)
def _fixed_facts(monkeypatch):
    monkeypatch.setattr("local_calendar.agent.system_facts", lambda: FIXED_FACTS)
    yield
