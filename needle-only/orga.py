"""Orga v5.5: Kalender-CRUD mit Abwesenheiten und Markdown-Display.

Kinds:
- appointment: Termin (Kollision mit anderen Terminen ±30 min)
- reminder: Erinnerung (feuert → wird gelöscht)
- task: Aufgabe (Deadline-basiert)
- absence: Abwesenheit (Urlaub, Reise, krank) — MEHRTÄGIG, kollidiert NICHT

Kollisions-Logik (vom Nutzer so spezifiziert):
- Urlaub + Termine müssen KOEXISTIEREN können (klassischer Kalender-Kram)
- NUR appointment+appointment triggert Kollision (±30 min)
- absence/reminder/task kollidieren mit NICHTS

Display: Markdown mit Tages-Headern und Bullet-Points:
**Mo 07.09.**
• 09:00 Zahnarzt
"""
import re
import psycopg
from datetime import datetime, timedelta

from modules.postgres_store import _parse_dt
from modules.timesync import format_local, _TZ

KINDS = ("appointment", "reminder", "task", "absence")
HORIZONS = {"heute": 1, "woche": 7, "monat": 31, "alle": 365}

KIND_LABELS = {
    "appointment": "Termin",
    "reminder": "Erinnerung",
    "task": "Aufgabe",
    "absence": "Abwesenheit",
}
KIND_ICONS = {
    "appointment": "📅",
    "reminder": "⏰",
    "task": "📋",
    "absence": "🏖️",
}
WEEKDAY_ABBR = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _norm_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    return _parse_dt(str(value))


def _when(dt):
    return format_local(dt.isoformat() if hasattr(dt, "isoformat") else str(dt))


def _fmt_day(dt) -> str:
    """Formatiert ein datetime als 'Mo 07.09.'"""
    wd = WEEKDAY_ABBR[dt.weekday()]
    return f"{wd} {dt.strftime('%d.%m.')}"


def _fmt_time(dt) -> str:
    """Formatiert ein datetime als '09:00'"""
    return dt.strftime("%H:%M")


class Orga:
    def __init__(self, url: str):
        self.url = url
        with psycopg.connect(url, autocommit=True) as c:
            # v5.5 Schema: 'absence' Kind hinzugefügt (Urlaub/Reise/krank)
            c.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    id          bigserial PRIMARY KEY,
                    owner       text NOT NULL DEFAULT 'ich',
                    kind        text NOT NULL CHECK (kind IN ('appointment', 'reminder', 'task', 'absence')),
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
            # Migration: Alte CHECK-Constraint (ohne 'absence') ersetzen
            c.execute("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'entries_kind_check'
                        AND pg_get_constraintdef(oid) NOT LIKE '%absence%'
                    ) THEN
                        ALTER TABLE entries DROP CONSTRAINT entries_kind_check;
                        ALTER TABLE entries ADD CONSTRAINT entries_kind_check
                            CHECK (kind IN ('appointment', 'reminder', 'task', 'absence'));
                    END IF;
                END $$;
            """)
            c.execute("CREATE INDEX IF NOT EXISTS entries_time_idx ON entries (owner, start_at)")
            c.execute("CREATE INDEX IF NOT EXISTS entries_kind_idx ON entries (kind)")

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
        """Erstellt einen Kalender-Eintrag.

        Kollisions-Check: NUR appointment+appointment (±30 min).
        Abwesenheiten (absence) kollidieren mit NICHTS —
        Urlaub + Termine müssen koexistieren können.
        """
        if kind not in KINDS:
            kind = "appointment"

        start_dt = _norm_dt(start_at)
        end_dt = _norm_dt(end_at)

        # Kollisions-Check: NUR Termin+Termin (±30 min)
        # Abwesenheiten (Urlaub etc.) und Erinnerungen dürfen überlappen
        if start_dt and kind == "appointment":
            window_start = start_dt - timedelta(minutes=30)
            window_end = start_dt + timedelta(minutes=30)
            existing = self._q(
                "SELECT title, start_at FROM entries "
                "WHERE kind = 'appointment' "
                "AND start_at BETWEEN %s AND %s LIMIT 1",
                (window_start, window_end))
            if existing:
                return (f"⚠️ Kollision: '{existing[0][0]}' beginnt bereits "
                        f"um {_when(existing[0][1])}. "
                        f"Trotzdem erstellen?")

        with psycopg.connect(self.url, autocommit=True) as c:
            cur = c.execute(
                "INSERT INTO entries (kind, title, start_at, end_at, alarm_min, location, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (kind, title, start_dt, end_dt,
                 alarm_min or None, location, notes))
            entry_id = cur.fetchone()[0]

        label = KIND_LABELS.get(kind, "Eintrag")
        icon = KIND_ICONS.get(kind, "•")

        if kind == "absence" and end_dt:
            # Mehrtägige Abwesenheit: "Urlaub: Mo 07.09. – Fr 11.09."
            return f"✅ {icon} {label} erstellt: {title} ({_fmt_day(start_dt)} – {_fmt_day(end_dt)})"
        elif end_dt:
            return f"✅ {icon} {label} erstellt: {title} ({_when(start_dt)} – {_when(end_dt)})"
        else:
            return f"✅ {icon} {label} erstellt: {title} ({_when(start_dt)})"

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
            params.append(_norm_dt(start_at))
        if end_at is not None:
            sets.append("end_at = %s")
            params.append(_norm_dt(end_at))
        if location is not None:
            sets.append("location = %s")
            params.append(location)
        if alarm_min is not None:
            sets.append("alarm_min = %s")
            params.append(alarm_min)
        if notes is not None:
            sets.append("notes = %s")
            params.append(notes)
        if not sets:
            return f"❌ Nichts zu bearbeiten für '{title}'."

        sets.append("alarmed_at = NULL")
        sets_sql = ", ".join(sets)
        params.append(entry[0])
        with psycopg.connect(self.url) as c:
            c.execute(f"UPDATE entries SET {sets_sql} WHERE id = %s", tuple(params))
        return f"✏️ Bearbeitet: {title}"

    def calendar_read(self, kind="all", horizon="woche", person=None):
        """Liest Kalender-Einträge — Markdown-Format mit Tages-Headern.

        Format:
        **Mo 07.09.**
        • 09:00 Zahnarzt

        **Di 08.09.**
        • 09:00 Hundefrisör
        """
        days = HORIZONS.get(horizon, 7)
        now = datetime.now(_TZ)
        start_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_range = start_range + timedelta(days=days)

        # Abwesenheiten separat laden (sie überspannen mehrere Tage)
        absences = self._q(
            "SELECT title, start_at, end_at FROM entries "
            "WHERE kind = 'absence' "
            "AND start_at < %s AND (end_at IS NULL OR end_at >= %s) "
            "ORDER BY start_at",
            (end_range, start_range))

        # Termine/Erinnerungen/Aufgaben laden
        if kind == "all":
            rows = self._q(
                "SELECT kind, title, start_at, end_at FROM entries "
                "WHERE kind != 'absence' "
                "AND start_at >= %s AND start_at < %s "
                "ORDER BY start_at",
                (start_range, end_range))
        else:
            rows = self._q(
                "SELECT kind, title, start_at, end_at FROM entries "
                "WHERE kind = %s AND kind != 'absence' "
                "AND start_at >= %s AND start_at < %s "
                "ORDER BY start_at",
                (kind, start_range, end_range))

        if not rows and not absences:
            return f"Keine Einträge in den nächsten {days} Tagen."

        lines = []

        # Abwesenheiten zuerst anzeigen (als Block)
        if absences:
            for title, start_at, end_at in absences:
                if end_at:
                    lines.append(f"🏖️ **{title}**: {_fmt_day(start_at)} – {_fmt_day(end_at)}")
                else:
                    lines.append(f"🏖️ **{title}**: {_fmt_day(start_at)}")
            lines.append("")

        # Nach Tag gruppieren
        by_day = {}
        for k, title, start_at, end_at in rows:
            day = start_at.strftime("%Y-%m-%d")
            by_day.setdefault(day, []).append((k, title, start_at, end_at))

        # Tages-Header + Bullet-Points
        for day in sorted(by_day.keys()):
            entries = by_day[day]
            first_dt = entries[0][2]  # start_at of first entry
            lines.append(f"**{_fmt_day(first_dt)}**")
            for k, title, start_at, end_at in entries:
                icon = KIND_ICONS.get(k, "•")
                end_str = f" – {_fmt_time(end_at)}" if end_at else ""
                lines.append(f"• {icon} {_fmt_time(start_at)}{end_str} {title}")
            lines.append("")

        return "\n".join(lines).strip()

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
