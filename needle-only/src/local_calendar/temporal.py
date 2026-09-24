"""Deterministic read-window resolution (plan §12-§14).

For reads, the original user text has authority over the model (calendar
arithmetic is deterministic, not a model problem). Priority:

1. explicit date range in the text
2. explicit single date in the text
3. 'nächste/kommende Woche' (next week) / 'diese Woche' (this week)
4. an explicitly named weekday
5. heute / morgen / übermorgen
6. the model's date/until/horizon
7. default (next 7 days)

This module is pure calendar arithmetic — no tool routing, no semantics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from . import calendar as cal

_WEEK = re.compile(r"\b(nächste|naechste|kommende|next)\s+woche\b", re.I)
_THIS_WEEK = re.compile(r"\b(diese|aktuelle|this)\s+woche\b", re.I)
_DAY_TOKENS = {"heute": 0, "today": 0, "morgen": 1, "tomorrow": 1,
               "übermorgen": 2, "uebermorgen": 2}


@dataclass
class ReadWindow:
    first_day: date
    last_day: date
    source: str


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _next_week(today: date) -> ReadWindow:
    monday = _monday(today) + timedelta(days=7)
    return ReadWindow(monday, monday + timedelta(days=6), "next_week")


def _this_week(today: date) -> ReadWindow:
    monday = _monday(today)
    return ReadWindow(monday, monday + timedelta(days=6), "this_week")


def _offset_in_text(text: str, today: date) -> ReadWindow | None:
    low = (text or "").lower()
    for token, off in _DAY_TOKENS.items():
        if re.search(rf"\b{token}\b", low):
            d = today + timedelta(days=off)
            return ReadWindow(d, d, f"offset:{token}")
    return None


def _from_model(model_args: dict, today: date) -> ReadWindow | None:
    date_expr = str(model_args.get("date", "")).strip()
    until_expr = str(model_args.get("until", "")).strip()
    first = cal.resolve_date(date_expr, today, roll=False) if date_expr else None
    if date_expr and first is None:
        first = cal.extract_date_from_text(date_expr, today, roll=False)
    last = cal.resolve_date(until_expr, today, roll=False) if until_expr else None
    if first is not None:
        if last is None or last < first:
            return ReadWindow(first, first, "model:date")
        return ReadWindow(first, last, "model:range")
    horizon = str(model_args.get("horizon", "")).lower()
    if horizon == "today":
        return ReadWindow(today, today, "model:today")
    if horizon == "month":
        return ReadWindow(today, today + timedelta(days=29), "model:month")
    if horizon == "week":
        return ReadWindow(today, today + timedelta(days=6), "model:week")
    return None


def resolve_read_window(text: str, model_args: dict | None = None,
                        today: date | None = None) -> ReadWindow:
    """Deterministic window for calendar_list (plan §12/§13). Never raises."""
    today = today or cal.now().date()
    model_args = model_args or {}
    raw = text or ""
    dates = cal.extract_dates_from_text(raw, today, roll=False)
    if len(dates) >= 2:
        return ReadWindow(dates[0], dates[-1], "text:range")
    if len(dates) == 1:
        return ReadWindow(dates[0], dates[0], "text:date")
    if _WEEK.search(raw):
        return _next_week(today)
    if _THIS_WEEK.search(raw):
        return _this_week(today)
    wd = cal.extract_weekday_from_text(raw, today)
    if wd is not None:
        return ReadWindow(wd, wd, "text:weekday")
    off = _offset_in_text(raw, today)
    if off is not None:
        return off
    model = _from_model(model_args, today)
    if model is not None:
        return model
    return ReadWindow(today, today + timedelta(days=6), "default")


def parse_nav_date(arg: str, today: date | None = None) -> date | None:
    """Parse a /day or /week argument: a date, 'heute', 'morgen' or a weekday.
    'next'/'nächste' on its own resolves to next week's Monday (plan §14)."""
    today = today or cal.now().date()
    arg = (arg or "").strip()
    if not arg:
        return today
    if re.fullmatch(r"(next|nächste|naechste)", arg, re.I):
        return _monday(today) + timedelta(days=7)
    if arg.lower() in _DAY_TOKENS:
        return today + timedelta(days=_DAY_TOKENS[arg.lower()])
    d = cal.resolve_date(arg, today, roll=False)
    if d is None:
        d = cal.extract_date_from_text(arg, today, roll=False)
    return d
