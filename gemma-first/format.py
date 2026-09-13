"""Deutscher Datums-/Zeit-Formatter für gemma-first.

Portiert aus v5.6 (orga.py): _fmt_day → "Mi 10.09.", _fmt_time → "14:00".
Regel: NIE ISO-Timestamps an den Nutzer, immer menschenlesbares Deutsch.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Berlin")
_WEEKDAY_ABBR = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
_WEEKDAY_FULL = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
                 "Freitag", "Samstag", "Sonntag"]


def localize(dt: datetime | str) -> datetime:
    """Konvertiert UTC/naive datetime nach Europe/Berlin."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ)


def fmt_day(dt: datetime | str) -> str:
    """'Mi 10.09.' — kurzer deutscher Tag."""
    dt = localize(dt)
    return f"{_WEEKDAY_ABBR[dt.weekday()]} {dt.strftime('%d.%m.')}"


def fmt_date(dt: datetime | str) -> str:
    """'10.09.2026' — volles Datum."""
    return localize(dt).strftime("%d.%m.%Y")


def fmt_time(dt: datetime | str) -> str:
    """'14:00' — lokale Uhrzeit."""
    return localize(dt).strftime("%H:%M")


def fmt_dt(dt: datetime | str) -> str:
    """'Mi 10.09. 14:00' — Tag + Uhrzeit."""
    dt = localize(dt)
    return f"{fmt_day(dt)} {fmt_time(dt)}"


def fmt_range(start: datetime | str, end: datetime | str) -> str:
    """'Mi 10.09. – Fr 12.09.' — Zeitraum (Mehrtage)."""
    return f"{fmt_day(start)} – {fmt_day(end)}"


def weekday_name(dt: datetime | str) -> str:
    """'Mittwoch' — voller Wochentagsname."""
    return _WEEKDAY_FULL[localize(dt).weekday()]
