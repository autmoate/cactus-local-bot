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

import json
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
    # multi-user scope (V1); nullable so legacy callers keep working
    calendar_id: int | None = None
    created_by_person_id: int | None = None


def color_for(name: str) -> str:
    """Deterministic person color (stable across restarts, plan §16)."""
    palette = ["#2f6fb0", "#c1622a", "#3f8b53", "#8a4fa3", "#b08a2f",
               "#a63a4b", "#2f8f8f", "#6a6a3a"]
    h = 0
    for ch in (name or ""):
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return palette[h % len(palette)]


# ---------------------------------------------------------------- SQLite store

class CalendarStore:
    """SQLite persistence. One connection per operation; no shared state.

    V1 multi-user model (plan §4): people / calendars / calendar_members /
    action_proposals; events carry calendar_id + created_by_person_id and
    event_participants references people(id). Legacy name-based callers keep
    working: `participants` (names) is mapped to/from people on read/write.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path = "data/calendar.db"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.execute("PRAGMA foreign_keys=ON")
        c.row_factory = sqlite3.Row
        return c

    # -------------------------------------------------------- schema / migration
    def _migrate(self) -> None:
        with self._conn() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            has_events = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                   "AND name='events'").fetchone()
            cols = {r["name"] for r in c.execute("PRAGMA table_info(events)")} \
                if has_events else set()
        if version >= self.SCHEMA_VERSION:
            self._ensure_tables()
            self._ensure_person_index()
            return
        legacy = bool(cols) and "calendar_id" not in cols
        if legacy:
            self.backup()  # plan §5/§33: backup before any schema change
            with self._conn() as c:  # columns first so index creation succeeds
                c.execute("ALTER TABLE events ADD COLUMN calendar_id INTEGER")
                c.execute("ALTER TABLE events ADD COLUMN created_by_person_id INTEGER")
        self._ensure_tables()  # creates missing tables + indexes
        with self._conn() as c:
            if legacy:
                self._finish_v0_migration(c)
            c.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
        self._ensure_person_index()

    def _ensure_person_index(self) -> None:
        with self._conn() as c:
            c.execute("CREATE INDEX IF NOT EXISTS ep_person_idx "
                      "ON event_participants(person_id)")

    def _ensure_tables(self) -> None:
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS people (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER UNIQUE,
                    display_name     TEXT NOT NULL,
                    color_key        TEXT,
                    enabled          INTEGER NOT NULL DEFAULT 1,
                    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS calendars (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind             TEXT NOT NULL CHECK (kind IN ('personal','group')),
                    name             TEXT NOT NULL,
                    owner_person_id  INTEGER REFERENCES people(id),
                    telegram_chat_id INTEGER UNIQUE,
                    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS calendar_members (
                    calendar_id INTEGER NOT NULL REFERENCES calendars(id) ON DELETE CASCADE,
                    person_id   INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    role        TEXT NOT NULL DEFAULT 'member'
                                CHECK (role IN ('member','admin')),
                    can_read    INTEGER NOT NULL DEFAULT 1,
                    can_write   INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (calendar_id, person_id)
                );
                CREATE TABLE IF NOT EXISTS action_proposals (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    token              TEXT NOT NULL UNIQUE,
                    actor_person_id    INTEGER NOT NULL REFERENCES people(id),
                    chat_id            INTEGER NOT NULL,
                    calendar_id        INTEGER NOT NULL REFERENCES calendars(id),
                    tool_name          TEXT NOT NULL,
                    args_json          TEXT NOT NULL,
                    resolved_json      TEXT NOT NULL,
                    context            TEXT NOT NULL DEFAULT '',
                    target_event_id    INTEGER,
                    target_fingerprint TEXT,
                    created_at         TEXT NOT NULL,
                    expires_at         TEXT NOT NULL,
                    status             TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE TABLE IF NOT EXISTS events (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    title   TEXT NOT NULL,
                    kind    TEXT NOT NULL DEFAULT 'appointment'
                            CHECK (kind IN ('appointment', 'absence')),
                    start   TEXT NOT NULL,
                    end     TEXT NOT NULL,
                    all_day INTEGER NOT NULL DEFAULT 0,
                    calendar_id INTEGER REFERENCES calendars(id),
                    created_by_person_id INTEGER REFERENCES people(id)
                );
                CREATE TABLE IF NOT EXISTS event_participants (
                    event_id  INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                    PRIMARY KEY (event_id, person_id)
                );
                CREATE INDEX IF NOT EXISTS events_start_idx ON events(start);
                CREATE INDEX IF NOT EXISTS events_calendar_idx ON events(calendar_id);
            """)

    def _finish_v0_migration(self, c: sqlite3.Connection) -> None:
        """Legacy data -> multi-user values (columns already added). No data loss."""
        before = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        owner = self._ensure_person(c, "Ich", None)
        personal = self._ensure_personal_calendar(c, owner)
        c.execute("UPDATE events SET calendar_id=?, created_by_person_id=?",
                  (personal, owner))
        # event_participants(name) -> event_participants(person_id)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(event_participants)")}
        if "name" in cols:
            name_map = {r["name"]: self._ensure_person(c, r["name"], None)
                        for r in c.execute("SELECT DISTINCT name FROM event_participants")}
            rows = c.execute("SELECT event_id, name FROM event_participants").fetchall()
            c.execute("DROP TABLE event_participants")
            c.execute("""CREATE TABLE event_participants (
                            event_id  INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                            person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                            PRIMARY KEY (event_id, person_id))""")
            c.executemany("INSERT OR IGNORE INTO event_participants VALUES (?,?)",
                          [(r["event_id"], name_map[r["name"]]) for r in rows])
        after = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if before != after:
            raise RuntimeError(f"migration lost events: {before} -> {after}")

    def backup(self) -> str | None:
        """SQLite-consistent backup to <dir>/backups/calendar-YYYYMMDD.db."""
        import datetime as _dt
        import shutil
        try:
            bdir = Path(self.path).parent / "backups"
            bdir.mkdir(parents=True, exist_ok=True)
            dest = bdir / f"calendar-{_dt.date.today():%Y%m%d}.db"
            src = sqlite3.connect(self.path)
            dst = sqlite3.connect(dest)
            with dst:
                src.backup(dst)  # sqlite backup API, not a blind file copy
            dst.close()
            src.close()
            return str(dest)
        except Exception:
            return None  # backup must never crash the bot (plan §33)


    @staticmethod
    def _iso(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    @staticmethod
    def _dt(value: str) -> datetime:
        return datetime.fromisoformat(value)

    # ----------------------------------------------------- people / calendars
    def _ensure_person(self, c, name: str, telegram_user_id: int | None) -> int:
        name = _canonical_person(name) or "Ich"
        if telegram_user_id is not None:
            row = c.execute("SELECT id FROM people WHERE telegram_user_id=?",
                            (telegram_user_id,)).fetchone()
            if row:
                return row["id"]
        row = c.execute("SELECT id FROM people WHERE display_name=? AND enabled=1",
                        (name,)).fetchone()
        if row:
            if telegram_user_id is not None:
                c.execute("UPDATE people SET telegram_user_id=? WHERE id=?",
                          (telegram_user_id, row["id"]))
            return row["id"]
        cur = c.execute("INSERT INTO people (telegram_user_id, display_name, color_key) "
                        "VALUES (?,?,?)", (telegram_user_id, name, color_for(name)))
        return cur.lastrowid

    def ensure_person(self, name: str, telegram_user_id: int | None = None) -> int:
        with self._conn() as c:
            return self._ensure_person(c, name, telegram_user_id)

    # ---------------------------------------------- owner reconciliation (§3)
    @staticmethod
    def _legacy_owner(c) -> sqlite3.Row | None:
        """The migrated pre-multiuser owner bucket: a person 'Ich' that has no
        Telegram id yet. Only this exact bucket is ever reconciled."""
        return c.execute("SELECT * FROM people WHERE telegram_user_id IS NULL "
                         "AND enabled=1 AND display_name='Ich' ORDER BY id LIMIT 1"
                         ).fetchone()

    @staticmethod
    def _personal_calendar_of(c, person_id: int) -> int | None:
        r = c.execute("SELECT id FROM calendars WHERE kind='personal' "
                      "AND owner_person_id=?", (person_id,)).fetchone()
        return r["id"] if r else None

    def _merge_people(self, c: sqlite3.Connection, src: int, dst: int) -> None:
        """Merge person src into dst WITHOUT data loss: calendars, events,
        participants, memberships. src is deleted only when unreferenced."""
        src_cal = self._personal_calendar_of(c, src)
        dst_cal = self._personal_calendar_of(c, dst)
        if src_cal is not None:
            if dst_cal is not None and dst_cal != src_cal:
                c.execute("UPDATE events SET calendar_id=? WHERE calendar_id=?",
                          (dst_cal, src_cal))
                c.execute("DELETE FROM calendar_members WHERE calendar_id=?",
                          (src_cal,))
                c.execute("DELETE FROM calendars WHERE id=?", (src_cal,))
            elif dst_cal is None:
                c.execute("UPDATE calendars SET owner_person_id=? WHERE id=?",
                          (dst, src_cal))
        c.execute("INSERT OR IGNORE INTO event_participants(event_id, person_id) "
                  "SELECT event_id, ? FROM event_participants WHERE person_id=?",
                  (dst, src))
        c.execute("DELETE FROM event_participants WHERE person_id=?", (src,))
        c.execute("UPDATE events SET created_by_person_id=? "
                  "WHERE created_by_person_id=?", (dst, src))
        c.execute("INSERT OR IGNORE INTO calendar_members(calendar_id, person_id, role) "
                  "SELECT calendar_id, ?, role FROM calendar_members WHERE person_id=?",
                  (dst, src))
        c.execute("DELETE FROM calendar_members WHERE person_id=?", (src,))
        refs = c.execute(
            "SELECT (SELECT COUNT(*) FROM event_participants WHERE person_id=?) + "
            "(SELECT COUNT(*) FROM events WHERE created_by_person_id=?) + "
            "(SELECT COUNT(*) FROM calendar_members WHERE person_id=?) + "
            "(SELECT COUNT(*) FROM calendars WHERE owner_person_id=?) + "
            "(SELECT COUNT(*) FROM action_proposals WHERE actor_person_id=?)",
            (src, src, src, src, src)).fetchone()[0]
        if refs == 0:
            c.execute("DELETE FROM people WHERE id=?", (src,))

    def reconcile_owner(self, telegram_user_id: int | None, display_name: str) -> int:
        """Idempotently bind the legacy 'Ich' bucket to the authorized owner
        (plan §3). Fall 1: bind in place (keep calendar + events). Fall 2: if a
        real owner row already exists, merge the legacy bucket into it without
        losing events. Backup before any identity merge. Only the owner calls
        this — allowed non-owner users go through ensure_person()."""
        name = _canonical_person(display_name) or "Ich"
        if telegram_user_id is None:  # legacy private test bot (no user id)
            with self._conn() as c:
                existing = c.execute("SELECT id FROM people WHERE display_name=? "
                                     "AND enabled=1", (name,)).fetchone()
                if existing:
                    return existing["id"]
                legacy = self._legacy_owner(c)
                if legacy is not None:
                    c.execute("UPDATE people SET display_name=?, color_key=? WHERE id=?",
                              (name, color_for(name), legacy["id"]))
                    c.execute("UPDATE calendars SET name=? WHERE kind='personal' "
                              "AND owner_person_id=?", (name, legacy["id"]))
                    return legacy["id"]
            return self.ensure_person(name, None)
        with self._conn() as c:
            owner = c.execute("SELECT * FROM people WHERE telegram_user_id=?",
                              (telegram_user_id,)).fetchone()
            legacy = self._legacy_owner(c)
        if owner is not None:
            if legacy is not None and legacy["id"] != owner["id"]:
                self.backup()
                with self._conn() as c:
                    self._merge_people(c, legacy["id"], owner["id"])
            return owner["id"]
        if legacy is not None:
            with self._conn() as c:
                c.execute("UPDATE people SET telegram_user_id=?, display_name=?, "
                          "color_key=? WHERE id=?",
                          (telegram_user_id, name, color_for(name), legacy["id"]))
                c.execute("UPDATE calendars SET name=? WHERE kind='personal' "
                          "AND owner_person_id=?", (name, legacy["id"]))
            return legacy["id"]
        return self.ensure_person(name, telegram_user_id)

    def _ensure_personal_calendar(self, c, person_id: int) -> int:
        row = c.execute("SELECT id FROM calendars WHERE kind='personal' "
                        "AND owner_person_id=?", (person_id,)).fetchone()
        if row:
            return row["id"]
        name = c.execute("SELECT display_name FROM people WHERE id=?",
                         (person_id,)).fetchone()["display_name"]
        cur = c.execute("INSERT INTO calendars (kind, name, owner_person_id) "
                        "VALUES ('personal', ?, ?)", (name, person_id))
        cid = cur.lastrowid
        c.execute("INSERT OR IGNORE INTO calendar_members "
                  "(calendar_id, person_id, role) VALUES (?,?, 'admin')",
                  (cid, person_id))
        return cid

    def ensure_personal_calendar(self, person_id: int) -> int:
        with self._conn() as c:
            return self._ensure_personal_calendar(c, person_id)

    def ensure_group_calendar(self, chat_id: int, name: str = "Gruppe") -> int:
        with self._conn() as c:
            row = c.execute("SELECT id FROM calendars WHERE telegram_chat_id=?",
                            (chat_id,)).fetchone()
            if row:
                return row["id"]
            cur = c.execute("INSERT INTO calendars (kind, name, telegram_chat_id) "
                            "VALUES ('group', ?, ?)", (name, chat_id))
            return cur.lastrowid

    def add_member(self, calendar_id: int, person_id: int, role: str = "member",
                   can_read: int = 1, can_write: int = 1) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO calendar_members "
                      "(calendar_id, person_id, role, can_read, can_write) "
                      "VALUES (?,?,?,?,?)",
                      (calendar_id, person_id, role, int(can_read), int(can_write)))

    def default_calendar_id(self) -> int:
        with self._conn() as c:
            owner = self._ensure_person(c, "Ich", None)
            return self._ensure_personal_calendar(c, owner)

    def calendar_of_person(self, person_id: int) -> int | None:
        with self._conn() as c:
            row = c.execute("SELECT id FROM calendars WHERE kind='personal' "
                            "AND owner_person_id=?", (person_id,)).fetchone()
            return row["id"] if row else None

    def calendar_of_chat(self, chat_id: int) -> int | None:
        with self._conn() as c:
            row = c.execute("SELECT id FROM calendars WHERE telegram_chat_id=?",
                            (chat_id,)).fetchone()
            return row["id"] if row else None

    def person(self, person_id: int) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
            return dict(r) if r else None

    def person_by_telegram(self, telegram_user_id: int) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM people WHERE telegram_user_id=?",
                          (telegram_user_id,)).fetchone()
            return dict(r) if r else None

    def members_of(self, calendar_id: int) -> list[int]:
        with self._conn() as c:
            return [r["person_id"] for r in c.execute(
                "SELECT person_id FROM calendar_members WHERE calendar_id=?",
                (calendar_id,))]

    def people_names(self, person_ids: list[int]) -> list[str]:
        if not person_ids:
            return []
        marks = ",".join("?" * len(person_ids))
        with self._conn() as c:
            rows = c.execute(f"SELECT display_name FROM people WHERE id IN ({marks}) "
                             "ORDER BY display_name", person_ids).fetchall()
        return [r["display_name"] for r in rows]

    # ------------------------------------------------------------- events CRUD
    def add(self, ev: CalendarEvent, calendar_id: int | None = None,
            created_by_person_id: int | None = None) -> CalendarEvent:
        with self._conn() as c:
            cid = calendar_id or self._ensure_personal_calendar(
                c, self._ensure_person(c, "Ich", None))
            cur = c.execute(
                "INSERT INTO events (title, kind, start, end, all_day, calendar_id, "
                "created_by_person_id) VALUES (?,?,?,?,?,?,?)",
                (ev.title, ev.kind, self._iso(ev.start), self._iso(ev.end),
                 int(ev.all_day), cid, created_by_person_id))
            ev = ev.model_copy(update={"id": cur.lastrowid, "calendar_id": cid,
                                       "created_by_person_id": created_by_person_id})
            self._write_participants(c, ev.id, ev.participants)
        return ev

    def _write_participants(self, c, event_id: int, names: list[str]) -> None:
        ids = {self._ensure_person(c, n, None)
               for n in {_canonical_person(p) for p in names} - {""}}
        c.execute("DELETE FROM event_participants WHERE event_id=?", (event_id,))
        c.executemany("INSERT OR IGNORE INTO event_participants VALUES (?,?)",
                      [(event_id, pid) for pid in ids])

    def update(self, ev: CalendarEvent) -> None:
        with self._conn() as c:
            c.execute("UPDATE events SET title=?, kind=?, start=?, end=?, all_day=? "
                      "WHERE id=?",
                      (ev.title, ev.kind, self._iso(ev.start), self._iso(ev.end),
                       int(ev.all_day), ev.id))
            self._write_participants(c, ev.id, ev.participants)

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
        keys = row.keys()
        return CalendarEvent(
            id=row["id"], title=row["title"], kind=row["kind"],
            start=self._dt(row["start"]), end=self._dt(row["end"]),
            all_day=bool(row["all_day"]), participants=people,
            calendar_id=(row["calendar_id"] if "calendar_id" in keys else None),
            created_by_person_id=(row["created_by_person_id"]
                                  if "created_by_person_id" in keys else None))

    def _load_people(self, c: sqlite3.Connection, ids: list[int]) -> dict[int, list[str]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = c.execute(
            f"SELECT ep.event_id AS eid, p.display_name AS name "
            f"FROM event_participants ep JOIN people p ON p.id=ep.person_id "
            f"WHERE ep.event_id IN ({marks}) ORDER BY ep.event_id, p.display_name",
            ids).fetchall()
        out: dict[int, list[str]] = {}
        for r in rows:
            out.setdefault(r["eid"], []).append(r["name"])
        return out

    def events_between(self, start: datetime, end: datetime,
                       person: str | None = None,
                       calendar_ids: list[int] | None = None,
                       person_ids: list[int] | None = None) -> list[CalendarEvent]:
        """All events overlapping [start, end), scoped by calendar and/or person.

        person (legacy name) and person_ids both restrict to events that have at
        least one of the given persons as participant. calendar_ids restricts to
        the given calendars (empty list = nothing)."""
        if calendar_ids is not None and not calendar_ids:
            return []
        sql = "SELECT * FROM events WHERE start < ? AND end > ?"
        params: list = [self._iso(end), self._iso(start)]
        if calendar_ids is not None:
            marks = ",".join("?" * len(calendar_ids))
            sql += f" AND calendar_id IN ({marks})"
            params += list(calendar_ids)
        pids = list(person_ids or [])
        if person:
            with self._conn() as pc:
                pids += [r["id"] for r in pc.execute(
                    "SELECT id FROM people WHERE display_name=? COLLATE NOCASE",
                    (_canonical_person(person),))]
        if person is not None or person_ids is not None:
            if not pids:
                return []
            marks = ",".join("?" * len(pids))
            sql += (f" AND id IN (SELECT event_id FROM event_participants "
                    f"WHERE person_id IN ({marks}))")
            params += pids
        with self._conn() as c:
            rows = c.execute(sql + " ORDER BY start", params).fetchall()
            people = self._load_people(c, [r["id"] for r in rows])
        return [self._event(r, people.get(r["id"], [])) for r in rows]

    def resolve_event(self, title: str | None = None,
                      day: date | None = None, t: time | None = None,
                      calendar_ids: list[int] | None = None
                      ) -> tuple[list[CalendarEvent], str]:
        """General event identification (plan §2 refactor, §5 scope): every given
        identifier is an optional constraint, AND-combined — never ranking.
        title: bidirectional case-insensitive substring match.
        day:   hard filter — the event must cover this calendar day.
        t:     start-time band ±30 min (identification by time of day).
        calendar_ids: hard scope — a same-named event in another calendar must
        never create ambiguity or be mutated (plan §5). None = unscoped legacy.

        Returns (candidates, status) with status in
        not_found / unique / ambiguous / no_identifiers.
        Mutating callers must never silently pick from ambiguous."""
        title = (title or "").strip()
        if not title and day is None and t is None:
            return [], "no_identifiers"
        if calendar_ids is not None and not calendar_ids:
            return [], "not_found"
        scope_sql, scope_params = "", []
        if calendar_ids is not None:
            scope_sql = (" AND calendar_id IN ("
                         + ",".join("?" * len(calendar_ids)) + ")")
            scope_params = list(calendar_ids)
        candidates: list[CalendarEvent] = []
        if title:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM events WHERE (title LIKE ? "
                    "OR ? LIKE ('%' || title || '%'))" + scope_sql + " ORDER BY start",
                    [f"%{title}%", title] + scope_params).fetchall()
                people = self._load_people(c, [r["id"] for r in rows])
            candidates = [self._event(r, people.get(r["id"], []))
                          for r in rows]
        if day is not None:  # hard filter: an explicit day excludes other days
            if candidates:
                candidates = [e for e in candidates if _covers(e, day)]
            else:
                day_start = datetime.combine(day, time(0, 0))
                candidates = self.events_between(
                    day_start, day_start + timedelta(days=1),
                    calendar_ids=calendar_ids)
        if t is not None and day is not None:
            lo, hi = datetime.combine(day, t) - timedelta(minutes=30), \
                datetime.combine(day, t) + timedelta(minutes=30)
            candidates = [e for e in candidates if e.start <= hi and e.end > lo]
        if not candidates:
            return [], "not_found"
        return candidates, ("unique" if len(candidates) == 1 else "ambiguous")

    def find_by_title(self, title: str, near: datetime | date | None = None,
                      calendar_ids: list[int] | None = None) -> CalendarEvent | None:
        """Case-insensitive substring match in both directions; prefers the next
        upcoming match relative to `near` (default: now), else the latest past one."""
        if not title.strip():
            return None
        t = title.strip()
        if near is not None and not isinstance(near, datetime):
            near = datetime.combine(near, time(0, 0))
        scope_sql, scope_params = "", []
        if calendar_ids is not None:
            if not calendar_ids:
                return None
            scope_sql = (" AND calendar_id IN ("
                         + ",".join("?" * len(calendar_ids)) + ")")
            scope_params = list(calendar_ids)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE (title LIKE ? OR ? LIKE ('%' || title || '%'))"
                + scope_sql + " ORDER BY start", [f"%{t}%", t] + scope_params).fetchall()
            people = self._load_people(c, [r["id"] for r in rows])
        events = [self._event(r, people.get(r["id"], [])) for r in rows]
        if not events:
            return None
        ref = near or now()
        upcoming = [e for e in events if e.end >= ref]
        return upcoming[0] if upcoming else events[-1]

    def event_count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def participant_names(self) -> list[str]:
        with self._conn() as c:
            return [r["display_name"] for r in c.execute(
                "SELECT display_name FROM people WHERE enabled=1 "
                "ORDER BY display_name")]

    def participant_ids(self, event_id: int) -> list[int]:
        with self._conn() as c:
            return [r["person_id"] for r in c.execute(
                "SELECT person_id FROM event_participants WHERE event_id=? "
                "ORDER BY person_id", (event_id,))]

    def participant_ids_map(self, event_ids: list[int]) -> dict[int, set[int]]:
        """Bulk participant ids for many events (one query, plan §10)."""
        if not event_ids:
            return {}
        marks = ",".join("?" * len(event_ids))
        with self._conn() as c:
            rows = c.execute(
                f"SELECT event_id, person_id FROM event_participants "
                f"WHERE event_id IN ({marks})", list(event_ids)).fetchall()
        out: dict[int, set[int]] = {}
        for r in rows:
            out.setdefault(r["event_id"], set()).add(r["person_id"])
        return out

    # ------------------------------------------------------- action proposals
    def create_proposal(self, token: str, actor_person_id: int, chat_id: int,
                        calendar_id: int, tool_name: str, args: dict,
                        resolved: dict, target_event_id: int | None,
                        target_fingerprint: str | None, context: str = "",
                        ttl_seconds: int = 300) -> None:
        import datetime as _dt
        created = now()
        expires = created + _dt.timedelta(seconds=ttl_seconds)
        with self._conn() as c:
            c.execute("""INSERT INTO action_proposals (token, actor_person_id, chat_id,
                            calendar_id, tool_name, args_json, resolved_json, context,
                            target_event_id, target_fingerprint, created_at, expires_at,
                            status)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending')""",
                      (token, actor_person_id, chat_id, calendar_id, tool_name,
                       json.dumps(args, ensure_ascii=False),
                       json.dumps(resolved, ensure_ascii=False, default=str), context,
                       target_event_id, target_fingerprint,
                       self._iso(created), self._iso(expires)))

    def get_proposal(self, token: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM action_proposals WHERE token=?",
                          (token,)).fetchone()
        return dict(r) if r else None

    def set_proposal_status(self, token: str, status: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE action_proposals SET status=? WHERE token=?",
                      (status, token))

    def overlaps(self, ev: CalendarEvent) -> list[CalendarEvent]:
        """Timed appointments sharing a participant that overlap ev (half-open).

        Collision policy (plan §6): a classic calendar allows overlapping
        entries. An absence never blocks a timed appointment. This returns the
        overlapping *timed* appointments so the caller can surface a warning —
        it is never used to reject a write."""
        if ev.all_day:
            return []
        mine = {_canonical_person(p) for p in ev.participants}
        if not mine:
            return []
        out: list[CalendarEvent] = []
        for other in self.events_between(ev.start - COLLISION_WINDOW,
                                         ev.end + COLLISION_WINDOW):
            if other.id == ev.id or other.all_day:
                continue
            if not (mine & {_canonical_person(p) for p in other.participants}):
                continue
            if event_overlaps(ev, other):
                out.append(other)
        return out

    def is_absent(self, person_ids: list[int], start: datetime, end: datetime,
                  calendar_ids: list[int] | None = None) -> bool:
        """True when any given person has an absence overlapping [start, end).
        events_between already restricts to overlapping events, so any all-day
        hit is an absence on that window (plan §6/§19)."""
        if not person_ids:
            return False
        for other in self.events_between(start, end, person_ids=person_ids,
                                         calendar_ids=calendar_ids):
            if other.all_day:
                return True
        return False

    def collision(self, ev: CalendarEvent) -> CalendarEvent | None:
        """First overlapping timed appointment sharing a participant, or None.
        (Backward-compatible helper; absences never collide, plan §6.)"""
        others = self.overlaps(ev)
        return others[0] if others else None


# ------------------------------------------------------------ pure solver part

def event_overlaps(a: CalendarEvent, b: CalendarEvent) -> bool:
    """Half-open interval intersection: [start, end)."""
    return a.start < b.end and b.start < a.end


def segment_event(ev: CalendarEvent, day: date
                  ) -> tuple[datetime, datetime] | None:
    """Pure projection (plan §8): clip a timed event to one calendar day using
    half-open bounds [00:00, next 00:00). Returns the segment or None when the
    event does not actually touch that day. One event -> at most one segment/day.
    All-day events are NOT projected here (they live in the header layer)."""
    day_start = datetime.combine(day, time(0, 0))
    day_end = day_start + timedelta(days=1)
    seg_start, seg_end = max(ev.start, day_start), min(ev.end, day_end)
    if seg_start >= seg_end:
        return None
    return seg_start, seg_end


def allday_span(ev: CalendarEvent) -> tuple[date, date]:
    """Inclusive (first_day, last_day) of an all-day event (plan §7)."""
    return ev.start.date(), ev.end.date() - timedelta(days=1)


def covers_day(ev: CalendarEvent, day: date) -> bool:
    """Does ev touch the given calendar day? Half-open for all-day, clipped for
    timed events (same semantics as segment_event)."""
    if ev.all_day:
        return ev.start.date() <= day < ev.end.date()
    return segment_event(ev, day) is not None


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


def free_slots_from_busy(busy: list[tuple[datetime, datetime]],
                         first_day: date, last_day: date, duration_min: int = 60,
                         work_start: time = WORK_START,
                         work_end: time = WORK_END
                         ) -> list[tuple[datetime, datetime]]:
    """Pure interval solver (plan §25): free windows >= duration inside the work
    window, given a pre-computed busy union. No DB access — callers may feed the
    exact same intervals they already read (plan §10: one read, one truth)."""
    duration = timedelta(minutes=max(5, duration_min))
    slots: list[tuple[datetime, datetime]] = []
    day = first_day
    while day <= last_day and len(slots) < 5:
        if day.weekday() < 5 or first_day == last_day:
            day_start = datetime.combine(day, work_start)
            day_end = datetime.combine(day, work_end)
            if day_start < day_end:
                cursor = day_start
                for b_start, b_end in busy:
                    b_start, b_end = max(b_start, day_start), min(b_end, day_end)
                    if b_end <= day_start or b_start >= day_end:
                        continue
                    if b_start - cursor >= duration:
                        slots.append((cursor, b_start))
                    cursor = max(cursor, b_end)
                    if day_end - cursor < duration:
                        break
                if day_end - cursor >= duration:
                    slots.append((cursor, day_end))
        day += timedelta(days=1)
    return slots[:5]


def find_free_slots(store: CalendarStore, persons: list[str],
                    first_day: date, last_day: date, duration_min: int = 60,
                    work_start: time = WORK_START,
                    work_end: time = WORK_END) -> list[tuple[datetime, datetime]]:
    """Free intervals (>= duration) inside the work window, as intersection of all
    participants' availabilities. Interval-based; swappable for bitsets later."""
    busy: list[tuple[datetime, datetime]] = []
    day = first_day
    while day <= last_day:
        ds = datetime.combine(day, work_start)
        de = datetime.combine(day, work_end)
        busy += busy_intervals(store, persons, ds, de)
        day += timedelta(days=1)
    return free_slots_from_busy(busy, first_day, last_day, duration_min,
                                work_start, work_end)


def busy_intervals_for_people(store: CalendarStore, person_ids: list[int],
                              calendar_ids: list[int], start: datetime,
                              end: datetime) -> list[tuple[datetime, datetime]]:
    """Busy union for people resolved by ID within an explicit calendar scope
    (plan §18): private = the personal calendar; group = members' personal
    calendars + the current group calendar. Absences count as busy (plan §19),
    even though they never block a manual timed write (plan §6)."""
    out: list[tuple[datetime, datetime]] = []
    if not person_ids or not calendar_ids:
        return out
    for pid in person_ids:
        for ev in store.events_between(start, end, person_ids=[pid],
                                       calendar_ids=calendar_ids):
            s, e = max(ev.start, start), min(ev.end, end)
            if s < e:
                out.append((s, e))
    return sorted(out)


def find_free_slots_for_people(store: CalendarStore, person_ids: list[int],
                               calendar_ids: list[int], first_day: date,
                               last_day: date, duration_min: int = 60,
                               work_start: time = WORK_START,
                               work_end: time = WORK_END) -> list[tuple[datetime, datetime]]:
    """Exact interval solver (plan §18/§25), ID-based and scoped. The bit-set
    kernel stays a renderer detail; this remains the domain truth."""
    busy = busy_intervals_for_people(
        store, person_ids, calendar_ids,
        datetime.combine(first_day, work_start),
        datetime.combine(last_day + timedelta(days=1), work_start))
    return free_slots_from_busy(busy, first_day, last_day, duration_min,
                                work_start, work_end)


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


def resolve_date(expr: str, today: date | None = None, roll: bool = True) -> date | None:
    """Resolve a symbolic/absolute date expression to a concrete local date.

    Understands: today/tomorrow/day after tomorrow (+ German), weekday names
    (optional 'next'), ISO YYYY-MM-DD, German DD.MM.[YYYY], 'DD month' and
    'month DD' in German and English. Returns None when nothing matches.
    roll=False keeps year-less past dates in the current year (read queries
    must allow the past as written)."""
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
    try:
        return datetime.fromisoformat(e).date()  # model emits ISO datetimes too
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
        if roll and not y and out < today:
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
        if roll and out < today:
            out = out.replace(year=out.year + 1)
        return out
    nxt = re.match(r"(?:next|nächste[nr]?|kommende[nr]?)\s+([a-zäöüß]+)", e)
    wd = _WEEKDAYS.get(nxt[1]) if nxt else _WEEKDAYS.get(e)
    if wd is not None:
        days_ahead = (wd - today.weekday()) % 7 or 7
        return today + timedelta(days=days_ahead)
    past = re.match(r"(?:letzten?|letzte[nr]?|vergangenen?|last)\s+([a-zäöüß]+)", e)
    if past and past[1] in _WEEKDAYS:
        wd = _WEEKDAYS[past[1]]
        days_back = (today.weekday() - wd) % 7 or 7
        return today - timedelta(days=days_back)
    if e in ("next week", "nächste woche"):
        return today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    return None


def extract_dates_from_text(text: str, today: date | None = None,
                            roll: bool = False) -> list[date]:
    """All explicit dates (DD.MM.[YYYY], month names, ISO) in a request text,
    in text order. Range patterns are EXCLUSIVE: 'vom 3. bis 18. august' yields
    exactly its two dates — no mixed candidates. roll=True applies create
    semantics (past -> next year); matching existing entries uses roll=False."""
    if not text:
        return []
    today = today or now().date()
    seen: set[tuple[int, int, int]] = set()
    out: list[date] = []

    def _keep(d: date) -> None:
        if (d.year, d.month, d.day) not in seen:
            seen.add((d.year, d.month, d.day))
            out.append(d)

    def _roll(d: date) -> date:
        if roll and d < today:
            return d.replace(year=d.year + 1)
        return d

    m = _MONTH_RANGE.search(text)
    if m:  # 'vom 3. bis 18. august' / 'august 3 to august 18' with month name
        try:
            mo = _MONTHS[m[3].lower()]
            for d in (int(m[1]), int(m[2])):
                _keep(_roll(date(today.year, mo, d)))
            return out
        except ValueError:
            pass
    m = _DAY_RANGE.search(text)  # 'vom 23. bis 27.' (shared month, no time)
    if m and int(m[1]) <= 31 and int(m[2]) <= 31 \
            and "uhr" not in text[m.end():m.end() + 8].lower():
        try:
            for d in (int(m[1]), int(m[2])):
                _keep(_roll(date(today.year, today.month, d)))
            return out
        except ValueError:
            pass
    for lang_m in _MONTH_PATTERNS:
        for mm in lang_m.finditer(text):
            dd = int(mm[1]) if mm[2].lower() in _MONTHS else int(mm[2])
            mo = _MONTHS[mm[2].lower()] if mm[2].lower() in _MONTHS \
                else _MONTHS[mm[1].lower()]
            try:
                _keep(_roll(date(today.year, mo, dd)))
            except ValueError:
                continue
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?\b", text):
        dd, mo, y = int(m[1]), int(m[2]), m[3]
        try:
            if y:
                _keep(date(int(y) + (2000 if len(y) == 2 else 0), mo, dd))
            else:
                _keep(_roll(date(today.year, mo, dd)))
        except ValueError:
            continue
    for m in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text):
        try:
            _keep(date(int(m[1]), int(m[2]), int(m[3])))
        except ValueError:
            continue
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
_DAY_RANGE = re.compile(
    r"\bvom\s+(\d{1,2})\.?\s*bis\s+(\d{1,2})\.?(?!\s*uhr)\b", re.IGNORECASE)
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


def _covers(e: CalendarEvent, d: date) -> bool:
    """Does event e cover the calendar day d? (half-open for all-day,
    closed for timed events whose end is on the same/next day)"""
    if e.all_day:
        return e.start.date() <= d < e.end.date()
    return e.start.date() <= d <= e.end.date()


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
    """German Markdown rendering: absences as range banners, timed entries under
    day headers. This is a presentation helper (plan §17); the structured
    ReadResult keeps text and image on the same data (plan §10/§11)."""
    if not events:
        return "Keine Termine."
    lines: list[str] = []
    for ev in sorted((e for e in events if e.all_day), key=lambda e: e.start):
        last = ev.end.date() - timedelta(days=1)
        span = (f"{_fmt_day(ev.start)} – {_fmt_day(datetime.combine(last, time()))}"
                if last > ev.start.date() else _fmt_day(ev.start))
        who = f" ({', '.join(ev.participants)})" if ev.participants else ""
        title = ev.title or "Abwesenheit"
        lines.append(f"🏖 **{title}**{who}: {span}")
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
        return "Keine gemeinsamen freien Zeiten gefunden."
    lines = ["Freie Zeiten:"]
    for s, e in slots:
        lines.append(f"• {_fmt_day(s)} {_fmt_time(s)}–{_fmt_time(e)}")
    return "\n".join(lines)
