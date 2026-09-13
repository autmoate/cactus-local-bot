"""Calendar domain: Pydantic models, SQLite store, deterministic solver, time resolution.

Storage model (uniform, half-open intervals [start, end)):
- timed event:   start = local wall-clock datetime, end = start + duration
- all-day event: start = inclusive start date at 00:00,
                 end   = exclusive end date at 00:00 (day AFTER the last covered day)

Decision (documented per plan §15): exactly two kinds exist.
- 'appointment' = timed entry (meeting, dentist, ...)
- 'absence'     = all-day entry (vacation, sick day, travel); multi-day supported.
Vacation is modelled as an absence. Absences never collide with appointments.

No ORM, no repository layer: direct sqlite3 statements where they belong.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

EventKind = Literal["appointment", "absence"]
WEEKDAYS_DE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

# Day periods -> deterministic representative start times (plan §16: Python computes).
PERIOD_TIMES: dict[str, time] = {
    "morning": time(9, 0), "vormittag": time(9, 0), "früh": time(9, 0), "early": time(9, 0),
    "noon": time(12, 0), "mittag": time(12, 0),
    "afternoon": time(14, 0), "nachmittag": time(14, 0),
    "evening": time(18, 0), "abend": time(18, 0),
    "night": time(20, 0), "nacht": time(20, 0),
}
# Day periods -> search windows for free-slot queries.
PERIOD_WINDOWS: dict[str, tuple[time, time]] = {
    "morning": (time(8, 0), time(12, 0)),
    "noon": (time(11, 0), time(14, 0)),
    "afternoon": (time(12, 0), time(17, 0)),
    "evening": (time(17, 0), time(21, 0)),
    "night": (time(20, 0), time(23, 0)),
}
_PERIOD_ALIASES = {"vormittag": "morning", "nachmittag": "afternoon",
                   "mittag": "noon", "abend": "evening", "nacht": "night"}
_PERIOD_NAMES = ("morning", "afternoon", "evening", "night", "noon",
                 "vormittag", "nachmittag", "mittag", "abend", "nacht")
DEFAULT_DURATION_MIN = 60
WORK_START, WORK_END = time(9, 0), time(17, 0)
COLLISION_WINDOW = timedelta(minutes=30)


def tzinfo() -> ZoneInfo:
    return ZoneInfo(os.environ.get("TZ_LOCAL", "Europe/Berlin"))


def now() -> datetime:
    """Current local wall-clock time (naive, stored/displayed as-is)."""
    return datetime.now(tzinfo()).replace(tzinfo=None)


class CalendarEvent(BaseModel):
    id: int | None = None
    title: str
    kind: EventKind = "appointment"
    start: datetime
    end: datetime
    all_day: bool = False
    participants: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- SQLite store

class CalendarStore:
    """SQLite persistence. One connection per operation; no shared state."""

    def __init__(self, path: str | Path = "data/calendar.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    title   TEXT NOT NULL,
                    kind    TEXT NOT NULL DEFAULT 'appointment'
                            CHECK (kind IN ('appointment', 'absence')),
                    start   TEXT NOT NULL,
                    end     TEXT NOT NULL,
                    all_day INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS event_participants (
                    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    name     TEXT NOT NULL,
                    PRIMARY KEY (event_id, name)
                );
                CREATE TABLE IF NOT EXISTS participants (
                    name TEXT PRIMARY KEY
                );
                CREATE INDEX IF NOT EXISTS events_start_idx ON events(start);
            """)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.execute("PRAGMA foreign_keys=ON")
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _dt(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def add(self, ev: CalendarEvent) -> CalendarEvent:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (title, kind, start, end, all_day) VALUES (?,?,?,?,?)",
                (ev.title, ev.kind, self._iso(ev.start), self._iso(ev.end), int(ev.all_day)))
            ev = ev.model_copy(update={"id": cur.lastrowid})
            names = {_canonical_person(p) for p in ev.participants} - {""}
            c.executemany("INSERT OR IGNORE INTO event_participants VALUES (?,?)",
                          [(ev.id, n) for n in names])
            c.executemany("INSERT OR IGNORE INTO participants VALUES (?)",
                          [(n,) for n in names])
        return ev

    def update(self, ev: CalendarEvent) -> None:
        with self._conn() as c:
            c.execute("UPDATE events SET title=?, kind=?, start=?, end=?, all_day=? WHERE id=?",
                      (ev.title, ev.kind, self._iso(ev.start), self._iso(ev.end),
                       int(ev.all_day), ev.id))
            names = {_canonical_person(p) for p in ev.participants} - {""}
            c.execute("DELETE FROM event_participants WHERE event_id=?", (ev.id,))
            c.executemany("INSERT OR IGNORE INTO event_participants VALUES (?,?)",
                          [(ev.id, n) for n in names])
            c.executemany("INSERT OR IGNORE INTO participants VALUES (?)",
                          [(n,) for n in names])

    def delete(self, event_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM events WHERE id=?", (event_id,))
            return cur.rowcount > 0

    def get(self, event_id: int) -> CalendarEvent | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if row is None:
                return None
            people = self._load_people(c, [event_id])
        return self._event(row, people.get(event_id, []))

    def _event(self, row: sqlite3.Row, people: list[str]) -> CalendarEvent:
        return CalendarEvent(
            id=row["id"], title=row["title"], kind=row["kind"],
            start=self._dt(row["start"]), end=self._dt(row["end"]),
            all_day=bool(row["all_day"]), participants=people)

    def _load_people(self, c: sqlite3.Connection, ids: list[int]) -> dict[int, list[str]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = c.execute(
            f"SELECT event_id, name FROM event_participants WHERE event_id IN ({marks}) "
            "ORDER BY event_id, name", ids).fetchall()
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(r["event_id"], []).append(r["name"])
        return out

    def events_between(self, start: datetime, end: datetime,
                       person: str | None = None) -> list[CalendarEvent]:
        """All events overlapping [start, end), optionally only those of one person."""
        sql = "SELECT * FROM events WHERE start < ? AND end > ?"
        params: list = [self._iso(end), self._iso(start)]
        if person:
            p = _canonical_person(person)
            sql += (" AND id IN (SELECT event_id FROM event_participants WHERE name = ? "
                    "COLLATE NOCASE)")
            params.append(p)
        with self._conn() as c:
            rows = c.execute(sql + " ORDER BY start", params).fetchall()
            people = self._load_people(c, [r["id"] for r in rows])
        return [self._event(r, people.get(r["id"], [])) for r in rows]

    def find_by_title(self, title: str, near: datetime | date | None = None) -> CalendarEvent | None:
        """Case-insensitive substring match in both directions; prefers the next
        upcoming match relative to `near` (default: now), else the latest past one."""
        if not title.strip():
            return None
        t = title.strip()
        if near is not None and not isinstance(near, datetime):
            near = datetime.combine(near, time(0, 0))
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE title LIKE ? OR ? LIKE ('%' || title || '%') "
                "ORDER BY start", (f"%{t}%", t)).fetchall()
            people = self._load_people(c, [r["id"] for r in rows])
        events = [self._event(r, people.get(r["id"], [])) for r in rows]
        if not events:
            return None
        ref = near or now()
        upcoming = [e for e in events if e.end >= ref]
        return upcoming[0] if upcoming else events[-1]

    def participant_names(self) -> list[str]:
        with self._conn() as c:
            return [r["name"] for r in
                    c.execute("SELECT name FROM participants ORDER BY name")]

    def collision(self, ev: CalendarEvent) -> CalendarEvent | None:
        """First conflicting event sharing a participant with ev.
        - appointment vs appointment (±30 min overlap): collision
        - appointment vs absence (shared person): collision — a vacation blocks
          that person's appointments, consistent with find_free_slots
        - creating an absence never collides (absences coexist by design)."""
        if ev.kind != "appointment":
            return None
        lo, hi = ev.start - COLLISION_WINDOW, ev.end + COLLISION_WINDOW
        mine = {_canonical_person(p) for p in ev.participants}
        for other in self.events_between(lo, hi):
            if other.id == ev.id or other.kind not in ("appointment", "absence"):
                continue
            if not (mine & {_canonical_person(p) for p in other.participants}):
                continue
            if event_overlaps(ev, other):
                return other
        return None


# ------------------------------------------------------------ pure solver part

def event_overlaps(a: CalendarEvent, b: CalendarEvent) -> bool:
    """Half-open interval intersection: [start, end)."""
    return a.start < b.end and b.start < a.end


def create_event(store: CalendarStore, title: str, start: datetime, end: datetime,
                 all_day: bool, participants: list[str],
                 kind: EventKind = "appointment") -> CalendarEvent:
    return store.add(CalendarEvent(title=title, kind=kind, start=start, end=end,
                                   all_day=all_day, participants=participants))


def move_event(store: CalendarStore, ev: CalendarEvent, new_start: datetime,
               new_end: datetime | None = None) -> CalendarEvent:
    if new_end is None:
        if ev.all_day:
            days = (ev.end.date() - ev.start.date()).days
            new_end = new_start + timedelta(days=max(days, 1))
        else:
            new_end = new_start + (ev.end - ev.start)
    ev = ev.model_copy(update={"start": new_start, "end": new_end})
    store.update(ev)
    return ev


def delete_event(store: CalendarStore, event_id: int) -> bool:
    return store.delete(event_id)


def list_events(store: CalendarStore, person: str | None = None,
                start: datetime | None = None, end: datetime | None = None,
                ) -> list[CalendarEvent]:
    return store.events_between(start or now(), end or now() + timedelta(days=7),
                                person=person)


def busy_intervals(store: CalendarStore, persons: list[str],
                   start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Union of busy time for the persons within [start, end)."""
    out: list[tuple[datetime, datetime]] = []
    for p in persons:
        for ev in store.events_between(start, end, person=p):
            out.append((max(ev.start, start), min(ev.end, end)))
    return sorted(out)


def find_free_slots(store: CalendarStore, persons: list[str],
                    first_day: date, last_day: date, duration_min: int = 60,
                    work_start: time = WORK_START,
                    work_end: time = WORK_END) -> list[tuple[datetime, datetime]]:
    """Free intervals (>= duration) inside the work window, as intersection of all
    participants' availabilities. Interval-based; swappable for bitsets later."""
    duration = timedelta(minutes=max(5, duration_min))
    slots: list[tuple[datetime, datetime]] = []
    day = first_day
    while day <= last_day and len(slots) < 5:
        if day.weekday() < 5 or first_day == last_day:
            day_start = datetime.combine(day, work_start)
            day_end = datetime.combine(day, work_end)
            if day_start < day_end:
                cursor = day_start
                for b_start, b_end in busy_intervals(store, persons, day_start, day_end):
                    if b_start - cursor >= duration:
                        slots.append((cursor, b_start))
                    cursor = max(cursor, b_end)
                    if day_end - cursor < duration:
                        break
                if day_end - cursor >= duration:
                    slots.append((cursor, day_end))
        day += timedelta(days=1)
    return slots[:5]


_PERIOD_STRIP = re.compile(
    r"\b(morning|afternoon|evening|night|noon|vormittag|nachmittag|mittag|"
    r"abend|nacht|früh|ganztägig|ganztags|all day)\b", re.IGNORECASE)


def strip_period(text: str) -> str:
    """'tomorrow afternoon' -> 'tomorrow' (day periods removed)."""
    return _PERIOD_STRIP.sub("", text or "")


def _period_of(text: str) -> str:
    """'morgen nachmittag' -> 'afternoon'; '' when no day period is present."""
    low = (text or "").lower()
    for name in _PERIOD_NAMES:
        if re.search(rf"\b{name}\b", low):
            return _PERIOD_ALIASES.get(name, name)
    return ""


# ------------------------------------------------------- temporal resolution

_DE_MONTHS = {"januar": 1, "februar": 2, "märz": 3, "mai": 5, "juni": 6, "juli": 7,
              "august": 8, "september": 9, "oktober": 10, "november": 11,
              "dezember": 12, "jänner": 1}
_EN_MONTHS = {"january": 1, "february": 2, "april": 4, "march": 3, "may": 5,
              "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
              "november": 11, "december": 12}
_MONTHS = {**_DE_MONTHS, **_EN_MONTHS}
_WEEKDAYS = {"monday": 0, "montag": 0, "tuesday": 1, "dienstag": 1, "wednesday": 2,
             "mittwoch": 2, "thursday": 3, "donnerstag": 3, "friday": 4, "freitag": 4,
             "saturday": 5, "samstag": 5, "sonnabend": 5, "sunday": 6, "sonntag": 6}
_DAY_OFFSETS = {"today": 0, "heute": 0, "tomorrow": 1, "morgen": 1,
                "day after tomorrow": 2, "übermorgen": 2, "uebermorgen": 2}
_REL_OFFSET = re.compile(r"in\s+(\d+)\s*(min(?:ute[n]?)?|h|hour[s]?|stunde[n]?|tag(?:e[n]?)?|day[s]?)\b",
                         re.IGNORECASE)


def resolve_date(expr: str, today: date | None = None) -> date | None:
    """Resolve a symbolic/absolute date expression to a concrete local date.

    Understands: today/tomorrow/day after tomorrow (+ German), weekday names
    (optional 'next'), ISO YYYY-MM-DD, German DD.MM.[YYYY], 'DD month' and
    'month DD' in German and English. Returns None when nothing matches.
    """
    if not expr or not expr.strip():
        return None
    e = expr.strip().lower()
    e = re.sub(r"t00:00:00$", "", e)  # model sometimes emits datetime strings
    today = today or now().date()
    if e in _DAY_OFFSETS:
        return today + timedelta(days=_DAY_OFFSETS[e])
    m = _REL_OFFSET.match(e)
    if m:  # relative offsets are handled by resolve_timing; not a pure date
        return None
    try:
        return date.fromisoformat(e)
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?$", e)
    if m:
        d, mo, y = int(m[1]), int(m[2]), m[3]
        year = int(y) + (2000 if y and len(y) == 2 else 0) if y else today.year
        try:
            out = date(year, mo, d)
        except ValueError:
            return None
        if not y and out < today:
            out = out.replace(year=out.year + 1)
        return out
    m = re.match(r"^(\d{1,2})\.?\s+([a-zäöüß]+)$", e) or re.match(r"^([a-zäöüß]+)\s+(\d{1,2})$", e)
    if m and (m[2] in _MONTHS or m[1] in _MONTHS):
        d = int(m[1]) if m[2] in _MONTHS else int(m[2])
        month = _MONTHS[m[2]] if m[2] in _MONTHS else _MONTHS[m[1]]
        try:
            out = date(today.year, month, d)
        except ValueError:
            return None
        if out < today:
            out = out.replace(year=out.year + 1)
        return out
    nxt = re.match(r"(?:next|nächste[nr]?|kommende[nr]?)\s+([a-zäöüß]+)", e)
    wd = _WEEKDAYS.get(nxt[1]) if nxt else _WEEKDAYS.get(e)
    if wd is not None:
        days_ahead = (wd - today.weekday()) % 7 or 7
        return today + timedelta(days=days_ahead)
    if e in ("next week", "nächste woche"):
        return today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    return None


def extract_dates_from_text(text: str, today: date | None = None,
                            roll: bool = False) -> list[date]:
    """All explicit dates (DD.MM.[YYYY] or ISO) in a request text, in order.
    Without an explicit year the current year is assumed; roll=True moves past
    dates to the following year (create semantics), matching existing entries
    uses roll=False (move/delete target matching)."""
    if not text:
        return []
    today = today or now().date()
    hits: list[tuple[int, date]] = []  # (position in text, date) — order matters
    seen: set[tuple[int, int, int]] = set()
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?\b", text):
        d, mo, y = int(m[1]), int(m[2]), m[3]
        try:
            if y:
                hits.append((m.start(), date(int(y) + (2000 if len(y) == 2 else 0), mo, d)))
                continue
            got = date(today.year, mo, d)
            if roll and got < today:
                got = got.replace(year=got.year + 1)
            hits.append((m.start(), got))
        except ValueError:
            continue
    for lang_m in _MONTH_PATTERNS:
        for m in lang_m.finditer(text):
            d = int(m[1]) if m[2].lower() in _MONTHS else int(m[2])
            mo = _MONTHS[m[2].lower()] if m[2].lower() in _MONTHS else _MONTHS[m[1].lower()]
            try:
                got = date(today.year, mo, d)
                if roll and got < today:
                    got = got.replace(year=got.year + 1)
                hits.append((m.start(), got))
            except ValueError:
                continue
    m = _MONTH_RANGE.search(text)
    if m:
        try:
            mo = _MONTHS[m[3].lower()]
            for d in (int(m[1]), int(m[2])):
                got = date(today.year, mo, d)
                if roll and got < today:
                    got = got.replace(year=got.year + 1)
                hits.append((m.start(), got))
        except ValueError:
            pass
    for m in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text):
        try:
            hits.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
        except ValueError:
            continue
    out = []
    for _, d in sorted(hits, key=lambda t: t[0]):
        if (d.year, d.month, d.day) not in seen:
            seen.add((d.year, d.month, d.day))
            out.append(d)
    return out


def extract_date_from_text(text: str, today: date | None = None,
                           roll: bool = False) -> date | None:
    """First explicit date in a request text, or None."""
    dates = extract_dates_from_text(text, today, roll)
    return dates[0] if dates else None


_TIME_AT = re.compile(r"\b(?:um|at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)
_TIME_COLON = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_TIME_UHR = re.compile(r"\b(\d{1,2})\s*uhr\b", re.IGNORECASE)
_MONTH_NAME_DAY = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})\b", re.IGNORECASE)
_DAY_MONTH_NAME = re.compile(
    r"\b(\d{1,2})\.?\s+(" + "|".join(_MONTHS) + r")\b", re.IGNORECASE)
_MONTH_PATTERNS = (_MONTH_NAME_DAY, _DAY_MONTH_NAME)
_MONTH_RANGE = re.compile(
    r"\b(\d{1,2})\.?\s*(?:bis|-|–|to)\s*(\d{1,2})\.?\s+(" + "|".join(_MONTHS) + r")\b",
    re.IGNORECASE)


def extract_time_from_text(text: str) -> time | None:
    """First explicit time of day (HH:MM, 'um 14 Uhr', '17Uhr', 'at 3 pm') in the
    text, or None when the text names no time of day."""
    if not text:
        return None
    m = _TIME_COLON.search(text)
    if m:
        return resolve_time(m[0].strip())
    m = _TIME_AT.search(text)
    if m:
        expr = f"{m[1]}:{m[2]}" if m[2] else m[1]
        if m[3]:
            expr += f" {m[3]}"
        return resolve_time(expr)
    m = _TIME_UHR.search(text)
    if m:
        return resolve_time(f"{m[1]} uhr")
    return None


def has_time_signal(text: str) -> bool:
    """True when the text names any time of day: HH:MM, 'um 14 Uhr', '17Uhr',
    'at 3 pm', or a day period (afternoon/morning/...). A create request without
    a time signal must not inherit the model's invented time (all-day instead)."""
    if not text:
        return False
    if (_TIME_COLON.search(text) or _TIME_AT.search(text)
            or _TIME_UHR.search(text)):
        return True
    return _period_of(text) != ""


def extract_weekday_from_text(text: str, today: date | None = None) -> date | None:
    """Next occurrence of an explicitly named weekday in the text (Python computes
    the date; the model's weekday math is unreliable). None without a weekday."""
    if not text:
        return None
    for m in re.finditer(r"\b(nächste[nr]?|next|kommende[nr]?)?\s*"
                         r"(montag|dienstag|mittwoch|donnerstag|freitag|samstag|"
                         r"sonnabend|sonntag|monday|tuesday|wednesday|thursday|"
                         r"friday|saturday|sunday)\b", text, re.IGNORECASE):
        wd = _WEEKDAYS[m[2].lower()]
        base = today or now().date()
        return base + timedelta(days=(wd - base.weekday()) % 7 or 7)
    return None


def resolve_time(expr: str) -> time | None:
    """Resolve a time expression: '14:00', '14 uhr', '9', '2 pm', 'noon',
    day periods (morning/afternoon/evening/night + German). None if no match."""
    if not expr or not expr.strip():
        return None
    e = expr.strip().lower().replace(".", ":")
    if e in PERIOD_TIMES:
        return PERIOD_TIMES[e]
    m = re.match(r"^(\d{1,2}):(\d{2})$", e)
    if m:
        h, mi = int(m[1]), int(m[2])
        return time(h, mi) if 0 <= h < 24 and 0 <= mi < 60 else None
    m = re.match(r"^(\d{1,2})(?::00)?\s*(?:uhr|o'?clock)?$", e)
    if m:
        h = int(m[1])
        return time(h) if 0 <= h < 24 else None
    m = re.match(r"^(\d{1,2})\s*(am|pm)$", e)
    if m:
        h = int(m[1]) % 12 + (12 if m[2] == "pm" else 0)
        return time(h) if 0 <= h < 24 else None
    return None


_MERGED_TIME = re.compile(
    r"\s*(?:at|um|@|,)?\s*(\d{1,2}(?::\d{2})?\s*(?:uhr)?)$", re.IGNORECASE)
# merged date ranges the model sometimes emits: 'august 3-18', '3. bis 18. august',
# '3.-18. august', '3-18 august'
_MERGED_RANGE = [
    re.compile(r"^([a-zäöüß]+)\s+(\d{1,2})\s*(?:-|–|bis)\s*(\d{1,2})\.?$", re.I),
    re.compile(r"^(\d{1,2})\.?\s*(?:-|–)?\s*bis\s*(\d{1,2})\.?\s+([a-zäöüß]+)$", re.I),
    re.compile(r"^(\d{1,2})\.?(?:-|–)(\d{1,2})\.?\s+([a-zäöüß]+)$", re.I),
]


def resolve_timing(date_expr: str = "", time_expr: str = "", end_date_expr: str = "",
                   duration_min: int = DEFAULT_DURATION_MIN,
                   ref: datetime | None = None) -> tuple[datetime, datetime, bool] | None:
    """Deterministic resolution of symbolic expressions -> (start, end, all_day).

    - 'in N min/hours/days' -> relative to ref (timed)
    - time expression given -> timed event at that time (periods map to fixed times)
    - no time expression   -> all-day; end date (until) is INCLUSIVE, stored +1 day
    Tolerates model quirks: 'tomorrow at 14:00' or a day period merged into the
    date expression, an end date emitted in the time expression, and merged
    date ranges like 'august 3-18'.
    """
    ref = ref or now()
    date_expr, time_expr, end_date_expr = ((date_expr or "").strip(),
                                           (time_expr or "").strip(),
                                           (end_date_expr or "").strip())
    if time_expr and not end_date_expr:
        swapped = resolve_date(time_expr, ref.date())
        if swapped is not None:  # end date landed in the time expression
            end_date_expr, time_expr = time_expr, ""
    period = _period_of(date_expr)
    if period:
        if not time_expr:
            time_expr = period
        date_expr = strip_period(date_expr).strip().rstrip(",")
    if not time_expr and not end_date_expr:
        for pat in _MERGED_RANGE:
            m = pat.match(date_expr)
            if m:
                month = m[1] if pat is _MERGED_RANGE[0] else m[3]
                d1, d2 = (m[2], m[3]) if pat is _MERGED_RANGE[0] else (m[1], m[2])
                cand = f"{month} {d1}"
                if resolve_date(cand, ref.date()) is not None:
                    date_expr = cand
                    end_date_expr = f"{month} {d2}"
                break
    m = _MERGED_TIME.search(date_expr)
    if m:
        cand_date, cand_time = date_expr[:m.start()].strip(), m[1].strip()
        if (cand_time and resolve_date(cand_date, ref.date()) is not None
                and resolve_time(cand_time) is not None):
            time_expr, date_expr = cand_time, cand_date
    m = _REL_OFFSET.match(date_expr.lower())
    if m and not time_expr:
        amount, unit = int(m[1]), m[2].lower()
        delta = (timedelta(minutes=amount) if unit.startswith("min")
                 else timedelta(hours=amount) if unit[0] in "hs"
                 else timedelta(weeks=amount) if unit.startswith("w")
                 else timedelta(days=amount))
        start = ref + delta
        return start, start + timedelta(minutes=duration_min), False
    day = resolve_date(date_expr, ref.date())
    if day is None:
        return None
    t = resolve_time(time_expr or "")
    if t is not None:
        start = datetime.combine(day, t)
        return start, start + timedelta(minutes=max(5, duration_min)), False
    start = datetime.combine(day, time(0, 0))
    if end_date_expr.strip():
        end_day = resolve_date(end_date_expr, ref.date())
        if end_day is None:
            return None
        if end_day < day:  # ranges across year boundaries: end rolls into next year
            end_day = end_day.replace(year=day.year + 1)
        return start, datetime.combine(end_day + timedelta(days=1), time(0, 0)), True
    return start, start + timedelta(days=1), True


def _canonical_person(name: str) -> str:
    n = (name or "").strip().strip(",+").strip()
    if n.lower() in ("ich", "me", "mir", "mich", "myself", "i"):
        return "Ich"
    return n[:1].upper() + n[1:] if n else ""


def parse_persons(value: str | list | None) -> list[str]:
    """'Ich, Lisa' / 'ich und max' / ['Lisa'] -> canonical participant list.
    Empty -> ['Ich'] (personal assistant default)."""
    if isinstance(value, str):
        parts = re.split(r"[,+&]| und | and ", value, flags=re.IGNORECASE)
    else:
        parts = list(value or [])
    names = [_canonical_person(p) for p in parts]
    names = [n for n in names if n]
    return names or ["Ich"]


# ------------------------------------------------------------------- rendering

def _fmt_day(dt: datetime) -> str:
    return f"{WEEKDAYS_DE[dt.weekday()]} {dt.strftime('%d.%m.')}"


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def render_events(events: list[CalendarEvent]) -> str:
    """Markdown rendering: absences as range blocks, timed entries under day headers."""
    if not events:
        return "No entries."
    lines: list[str] = []
    for ev in sorted((e for e in events if e.all_day), key=lambda e: e.start):
        last = ev.end.date() - timedelta(days=1)
        span = (f"{_fmt_day(ev.start)} – {_fmt_day(datetime.combine(last, time()))}"
                if last > ev.start.date() else _fmt_day(ev.start))
        who = f" ({', '.join(ev.participants)})" if ev.participants else ""
        lines.append(f"🚫 **{ev.title}**{who}: {span}")
    if lines:
        lines.append("")
    timed = sorted((e for e in events if not e.all_day), key=lambda e: e.start)
    by_day: dict[date, list[CalendarEvent]] = {}
    for ev in timed:
        by_day.setdefault(ev.start.date(), []).append(ev)
    for day in sorted(by_day):
        lines.append(f"**{_fmt_day(datetime.combine(day, time()))}**")
        for ev in by_day[day]:
            who = f" ({', '.join(ev.participants)})" if ev.participants else ""
            lines.append(f"• {_fmt_time(ev.start)}–{_fmt_time(ev.end)} {ev.title}{who}")
        lines.append("")
    return "\n".join(lines).strip()


def render_slots(slots: list[tuple[datetime, datetime]]) -> str:
    if not slots:
        return "No common free slots found."
    lines = ["Free slots:"]
    for s, e in slots:
        lines.append(f"• {_fmt_day(s)} {_fmt_time(s)}–{_fmt_time(e)}")
    return "\n".join(lines)
