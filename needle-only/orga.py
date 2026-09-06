"""Orga v5.3: CRUD-basierte Kalender- und Notizverwaltung.
7 Tools: calendar_create/edit/read/delete + note_write/read/delete.

Lifecycle (vom Nutzer so spezifiziert):
- Erinnerungen (kind='reminder') werden nach dem Auslösen GELÖSCHT
- Abgesagte Termine werden GELÖSCHT (calendar_delete = Hard Delete)
- Vergangene Termine BLEIBEN in der DB (Archiv, via Zeitfilter abfragbar)

Kein Status-Feld, kein Lifecycle-Tracking — nur CRUD.
Die Zeit IST der Lifecycle: Vergangenes bleibt, Irrelevantes wird gelöscht."""
import re
import psycopg
from datetime import datetime, timedelta

from modules.postgres_store import _parse_dt
from modules.timesync import format_local, _TZ

KINDS = ("appointment", "reminder", "task")
HORIZONS = {"heute": 1, "woche": 7, "monat": 31, "alle": 365}


def _norm_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    return _parse_dt(str(value))


def _when(dt):
    return format_local(dt.isoformat() if hasattr(dt, "isoformat") else str(dt))


class Orga:
    def __init__(self, url: str):
        self.url = url
        with psycopg.connect(url, autocommit=True) as c:
            # v5.3 Schema: kein 'status' Feld — Lifecycle via DELETE
            c.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    id          bigserial PRIMARY KEY,
                    owner       text NOT NULL DEFAULT 'ich',
                    kind        text NOT NULL CHECK (kind IN ('appointment', 'reminder', 'task')),
                    title       text NOT NULL,
                    start_at    timestamptz NOT NULL,
                    end_at      timestamptz,
                    alarm_min   integer,
                    location    text NOT NULL DEFAULT '',
                    notes       text NOT NULL DEFAULT '',
                    participants text[] NOT NULL DEFAULT '{}',
                    alarmed_at  timestamptz,
                    created_at  timestamptz NOT NULL DEFAULT now()
                )
            """)
            c.execute("CREATE TABLE IF NOT EXISTS n_notes ("
                      "id bigserial primary key, owner text not null default 'ich', "
                      "subject text not null, body text not null default '', "
                      "created_at timestamptz not null default now())")
            c.execute("CREATE INDEX IF NOT EXISTS entries_time_idx ON entries (owner, start_at)")
            c.execute("CREATE INDEX IF NOT EXISTS entries_kind_idx ON entries (kind)")
            c.execute("CREATE INDEX IF NOT EXISTS n_notes_subject_trgm_idx ON n_notes USING gin (subject gin_trgm_ops)")
            try:
                c.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            except Exception:
                pass

    def _q(self, sql, vals=()):
        with psycopg.connect(self.url) as c:
            cur = c.execute(sql, vals)
            try:
                rows = cur.fetchall()
            except psycopg.ProgrammingError:
                rows = []
            c.commit()
            return rows

    # ==========================================
    # Kalender CRUD
    # ==========================================

    def calendar_create(self, title, start_at, end_at=None, location="",
                        kind="appointment", alarm_min=0, notes=""):
        """Erstellt einen Kalender-Eintrag mit Kollisions-Check."""
        if kind not in KINDS:
            kind = "appointment"

        start_dt = _norm_dt(start_at)

        # Kollisions-Check: bereits ein Eintrag zur gleichen Zeit (±30 min)?
        # Prüft ANY entry mit gleicher Startzeit — NICHT nur gleichen Titel!
        if start_dt:
            window_start = start_dt - timedelta(minutes=30)
            window_end = start_dt + timedelta(minutes=30)
            existing = self._q(
                "SELECT title, start_at FROM entries "
                "WHERE start_at BETWEEN %s AND %s LIMIT 1",
                (window_start, window_end))
            if existing:
                return (f"⚠️ Kollision: '{existing[0][0]}' beginnt bereits "
                        f"um {_when(existing[0][1])}. "
                        f"Trotzdem erstellen?")

        with psycopg.connect(self.url, autocommit=True) as c:
            cur = c.execute(
                "INSERT INTO entries (kind, title, start_at, end_at, alarm_min, location, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (kind, title, start_dt, _norm_dt(end_at),
                 alarm_min or None, location, notes))
            entry_id = cur.fetchone()[0]

        kind_label = {"appointment": "Termin", "reminder": "Erinnerung", "task": "Aufgabe"}[kind]
        return f"✅ {kind_label} erstellt: {title} ({_when(start_dt)})"

    def calendar_edit(self, title, start_at=None, end_at=None, location=None,
                      alarm_min=None, notes=None):
        """Bearbeitet einen bestehenden Kalender-Eintrag."""
        entry = self._find_entry(title, start_at)
        if not entry:
            return f"❌ Eintrag '{title}' nicht gefunden."
        sets = []
        params = []
        if start_at is not None:
            sets.append("start_at = %s")
            params.append(start_at)
        if end_at is not None:
            sets.append("end_at = %s")
            params.append(end_at)
        if location is not None:
            sets.append("location = %s")
            params.append(location)
        if alarm_min is not None:
            sets.append("alarm_min = %s")
            params.append(alarm_min)
        if notes is not None:
            sets.append("notes = %s")
            params.append(notes)
        sets.append("alarmed_at = NULL")
        sets_sql = ", ".join(sets)
        params.append(entry[0])
        with psycopg.connect(self.url) as c:
            c.execute(f"UPDATE entries SET {sets_sql} WHERE id = %s", tuple(params))
        return f"✏️ Bearbeitet: {title}"

    def calendar_read(self, kind="all", horizon="woche"):
        """Liest Kalender-Einträge mit optionalem Filter."""
        days = HORIZONS.get(horizon, 7)
        now = datetime.now(_TZ)
        start_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_range = start_range + timedelta(days=days)

        if kind == "all":
            rows = self._q(
                "SELECT kind, title, start_at, end_at FROM entries "
                "WHERE start_at >= %s AND start_at < %s "
                "ORDER BY start_at",
                (start_range, end_range))
        else:
            rows = self._q(
                "SELECT kind, title, start_at, end_at FROM entries "
                "WHERE kind = %s AND start_at >= %s AND start_at < %s "
                "ORDER BY start_at",
                (kind, start_range, end_range))

        if not rows:
            return f"Keine Einträge in den nächsten {days} Tagen."

        kind_labels = {"appointment": "📅", "reminder": "⏰", "task": "📋"}
        lines = []
        for k, title, start_at, end_at in rows:
            icon = kind_labels.get(k, "•")
            end_str = f" - {_when(end_at)}" if end_at else ""
            lines.append(f"{icon} {_when(start_at)}{end_str} {title}")

        return "\n".join(lines)

    def calendar_delete(self, title, start_at=None):
        """Löscht einen Kalender-Eintrag (Hard Delete).
        Sucht zuerst nach Titel, fallback auf start_at wenn Titel nicht gefunden."""
        entry = self._find_entry(title, start_at)
        if not entry:
            return f"❌ Eintrag '{title}' nicht gefunden."
        with psycopg.connect(self.url) as c:
            c.execute("DELETE FROM entries WHERE id = %s", (entry[0],))
        return f"🗑️ Gelöscht: {entry[1]}"

    def _find_entry(self, title, start_at=None):
        """Findet einen Eintrag per Titel-Match (case-insensitive, partial).
        Fallback: Wenn Titel nicht gefunden, suche per start_at (±30 min)."""
        if not title:
            return None

        # 1) Titel-Suche (partial match)
        rows = self._q(
            "SELECT id, title FROM entries WHERE title ILIKE %s "
            "ORDER BY start_at ASC LIMIT 1",
            (f"%{title}%",))
        if rows:
            return rows[0]

        # 2) Fallback: start_at-Suche (±30 min Fenster)
        start_dt = _norm_dt(start_at)
        if start_dt:
            window_start = start_dt - timedelta(minutes=30)
            window_end = start_dt + timedelta(minutes=30)
            rows = self._q(
                "SELECT id, title FROM entries "
                "WHERE start_at BETWEEN %s AND %s "
                "ORDER BY start_at ASC LIMIT 1",
                (window_start, window_end))
            if rows:
                return rows[0]

        return None

    # ==========================================
    # Notiz CRUD
    # ==========================================

    def note_write(self, subject, body):
        """Erstellt eine Notiz."""
        if not subject or not body:
            return "❌ Notiz braucht Thema und Inhalt."
        with psycopg.connect(self.url) as c:
            c.execute("INSERT INTO n_notes (subject, body) VALUES (%s, %s)",
                      (subject, body))
        return f"📝 Notiz gespeichert: {subject}"

    def note_read(self, query=""):
        """Liest/durchsucht Notizen."""
        if query:
            rows = self._q(
                "SELECT subject, body FROM n_notes "
                "WHERE subject ILIKE %s OR body ILIKE %s "
                "ORDER BY created_at DESC LIMIT 10",
                (f"%{query}%", f"%{query}%"))
        else:
            rows = self._q(
                "SELECT subject, body FROM n_notes "
                "ORDER BY created_at DESC LIMIT 20")

        if not rows:
            return f"Keine Notizen gefunden." + (f" (Suche: '{query}')" if query else "")

        lines = [f"📝 {subject}: {body[:80]}" for subject, body in rows]
        return "\n".join(lines)

    def note_delete(self, subject):
        """Löscht eine Notiz (Hard Delete)."""
        if not subject:
            return "❌ Notiz-Thema fehlt."
        rows = self._q(
            "SELECT id, subject FROM n_notes WHERE subject ILIKE %s LIMIT 1",
            (f"%{subject}%",))
        if not rows:
            return f"❌ Notiz '{subject}' nicht gefunden."
        with psycopg.connect(self.url) as c:
            c.execute("DELETE FROM n_notes WHERE id = %s", (rows[0][0],))
        return f"🗑️ Notiz gelöscht: {rows[0][1]}"
