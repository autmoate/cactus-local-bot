"""Deterministic temporal compiler for the Thunderbird spike (§6, §7).

The ONLY job here is: `when` span -> (start, end, all_day). No mail semantics,
no intent detection, no participant guessing, no second model call. Relative
phrases ("morgen", "nächsten Dienstag") are resolved against the mail's
`received_at`, never against wall-clock `now()` — that is what makes an email
workflow believable.

Date/time parsing is reused from the frozen production calendar module
(read-only import); the small amount of range logic that production does not
have ("7. und 8. Oktober", "von 14 bis 16 Uhr") lives here.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from local_calendar import calendar as cal  # noqa: E402

from models import Timing  # noqa: E402

DEFAULT_DURATION_MIN = 60

_DAY_TOKENS = {"heute": 0, "today": 0, "morgen": 1, "tomorrow": 1,
               "übermorgen": 2, "uebermorgen": 2}
_MONTHS = "|".join(sorted(cal._MONTHS, key=len, reverse=True))
_UND_RANGE = re.compile(
    rf"\b(\d{{1,2}})\.?\s*(?:und|and)\s*(\d{{1,2}})\.?\s+({_MONTHS})\b", re.I)
_TIME_RANGE = re.compile(
    r"(?:von\s+|ab\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:uhr)?\s*"
    r"(?:bis|-|–|—|to)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:uhr)?", re.I)
_PAST_MARKER = re.compile(r"\b(letzt|vergangen|last|gestern|yesterday)\w*", re.I)
# Temporal evidence the compiler deliberately does not hard-resolve: a timezone
# or a fuzzy time expression must route to human review, never to a guessed
# datetime (FT_PLAN.md §5/§9).
_TZ_RE = re.compile(
    r"\b(CET|CEST|ET|EST|EDT|BST|GMT|UTC|PT|PST|PDT|Europe/Berlin|Pacific)\b",
    re.I)
_FUZZY_RE = re.compile(
    r"\b(gegen|circa|ca\.|zwischen|vormittag|nachmittag|abend|"
    r"nach dem mittag\w*)\b", re.I)


def _relative_day(text: str, ref_date):
    low = (text or "").lower()
    for token, offset in _DAY_TOKENS.items():
        if re.search(rf"\b{token}\b", low):
            return ref_date + timedelta(days=offset)
    return None


def _all_dates(when: str, ref_date) -> list:
    """Explicit dates in text order, plus the '7. und 8. Oktober' pattern that
    the production range parser does not cover."""
    m = _UND_RANGE.search(when or "")
    if m:
        month = cal._MONTHS[m[3].lower()]
        out = []
        for raw_day in (m[1], m[2]):
            try:
                out.append(date(ref_date.year, month, int(raw_day)))
            except ValueError:
                return []
        return out
    return cal.extract_dates_from_text(when or "", ref_date, roll=True)


def _resolve_time(h, minute, meridiem):
    expr = f"{int(h)}:{int(minute):02d}" if minute else f"{int(h)}"
    if meridiem:
        expr += f" {meridiem.lower()}"
    return cal.resolve_time(expr)


def parse_time_range(text: str) -> tuple[time | None, time | None]:
    """First 'HH[:MM] (Uhr) bis/to HH[:MM] (Uhr)' range, or (None, None).

    A bare date range like '14-16.' is rejected: the end number is immediately
    followed by a '.' (day dot) rather than a time marker. A trailing sentence
    period is NOT a day dot: a range that carries a clock marker (':', 'Uhr' or
    am/pm) keeps its meaning even when the sentence ends there
    ('... von 15 bis 17 Uhr.' -> 15:00-17:00)."""
    if not text:
        return None, None
    for m in _TIME_RANGE.finditer(text):
        tail = text[m.start():m.end()].lower()
        is_clock = ":" in tail or "uhr" in tail or m[3] or m[6]
        if not is_clock and text[m.end():m.end() + 2].lstrip().startswith("."):
            continue
        start = _resolve_time(m[1], m[2], m[3])
        end = _resolve_time(m[4], m[5], m[6] or m[3])
        if start is not None and end is not None:
            return start, end
    return None, None


def compile_when(when: str, ref: datetime | None = None,
                 default_duration_min: int = DEFAULT_DURATION_MIN) -> Timing:
    """Compile a `when` span into a concrete timing. Never raises."""
    when = (when or "").strip()
    ref = ref or cal.now()
    if not when:
        return Timing(None, None, False, incomplete=True, note="empty when")

    if _TZ_RE.search(when) or _FUZZY_RE.search(when):
        return Timing(None, None, False, incomplete=True,
                      note="temporal not fully resolvable (timezone/fuzzy)")

    dates = _all_dates(when, ref.date())
    if not dates:
        wd = cal.extract_weekday_from_text(when, ref.date())
        if wd is not None:
            dates = [wd]
    if not dates:
        rel = _relative_day(when, ref.date())
        if rel is not None:
            dates = [rel]
    if not dates:
        single = cal.resolve_date(when, ref.date(), roll=True)
        if single is not None:
            dates = [single]
    if not dates:
        return Timing(None, None, False, incomplete=True, note="no resolvable date")

    day = dates[0]
    rolled = False
    if day < ref.date() and not _PAST_MARKER.search(when):
        # A create-event mail describes the future; recover model-computed past
        # dates (Base Needle emits e.g. 2025 instead of 2026) with create roll.
        dates = [d.replace(year=d.year + 1) if d < ref.date() else d
                 for d in dates]
        day, rolled = dates[0], True
    start_t, end_t = parse_time_range(when)
    if start_t is None:
        start_t = cal.extract_time_from_text(when)

    if start_t is None:  # all-day (optionally multi-day) entry
        end_day = dates[-1]
        start = datetime.combine(day, time(0, 0))
        end = datetime.combine(end_day + timedelta(days=1), time(0, 0))
        return Timing(start, end, True, note="all-day")

    start = datetime.combine(day, start_t)
    if end_t is not None:
        if end_t <= start_t:
            end_t = (datetime.combine(day, end_t) + timedelta(hours=12)).time()
        end = datetime.combine(day, end_t)
        return Timing(start, end, False, note="explicit range")
    end = start + timedelta(minutes=max(5, default_duration_min))
    return Timing(start, end, False, used_default_duration=True,
                  note=f"default duration {default_duration_min} min")
