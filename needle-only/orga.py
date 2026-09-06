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


def _localize(dt):
    """DB-datetime (UTC) nach Europe/Berlin konvertieren für Display."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_TZ)
    return dt.astimezone(_TZ)


def _fmt_day(dt) -> str:
    """Formatiert ein datetime als 'Mo 07.09.' (lokal)."""
    dt = _localize(dt)
    return f"{WEEKDAY_ABBR[dt.weekday()]} {dt.strftime('%d.%m.')}"


def _fmt_time(dt) -> str:
    """Formatiert ein datetime als '09:00' (lokal)."""
    return _localize(dt).strftime("%H:%M")


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
                        kind="appointment", alarm_min=0, notes="",
                        owner="ich", participants=None):
        """Erstellt einen Kalender-Eintrag.

        Kollisions-Check: NUR appointment+appointment (±30 min).
        Abwesenheiten (absence) kollidieren mit NICHTS —
        Urlaub + Termine müssen koexistieren können.
        owner: Für wen der Eintrag ist (Personenname, default 'ich').
        participants: Weitere Beteiligte als Liste (optional).
        """
        if kind not in KINDS:
            kind = "appointment"

        start_dt = _norm_dt(start_at)
        end_dt = _norm_dt(end_at)
        owner = (str(owner).strip() or "ich")

        # participants: String (komma-getrennt) oder Liste → normalisierte Liste
        if isinstance(participants, str):
            parts = [p.strip() for p in re.split(r'[,+]| und ', participants)
                     if p.strip()]
        else:
            parts = [str(p).strip() for p in (participants or [])
                     if str(p).strip()]

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
                "INSERT INTO entries (kind, title, start_at, end_at, "
                "alarm_min, location, notes, owner, participants) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (kind, title, start_dt, end_dt,
                 alarm_min or None, location, notes, owner, parts))
            entry_id = cur.fetchone()[0]

        label = KIND_LABELS.get(kind, "Eintrag")
        icon = KIND_ICONS.get(kind, "•")
        owner_suffix = f" ({owner})" if owner != "ich" else ""

        if kind == "absence" and end_dt:
            # Mehrtägige Abwesenheit: "Urlaub: Mo 07.09. – Fr 11.09."
            return f"✅ {icon} {label} erstellt{owner_suffix}: {title} ({_fmt_day(start_dt)} – {_fmt_day(end_dt)})"
        elif end_dt:
            return f"✅ {icon} {label} erstellt{owner_suffix}: {title} ({_when(start_dt)} – {_when(end_dt)})"
        else:
            return f"✅ {icon} {label} erstellt{owner_suffix}: {title} ({_when(start_dt)})"

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

        person: Optional — filtert auf Owner ODER Participant (case-insensitive).
                None = alle Personen (default).

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

        # Person-Filter (case-insensitive): owner match ODER participant match
        person_filter = None
        if person and str(person).strip():
            p = str(person).strip()
            person_filter = (
                "(owner ILIKE %s OR EXISTS ("
                "SELECT 1 FROM unnest(participants) pp "
                "WHERE pp ILIKE %s))", (f"%{p}%", f"%{p}%"))

        # Abwesenheiten separat laden (sie überspannen mehrere Tage)
        if person_filter:
            cond, params = person_filter
            absences = self._q(
                f"SELECT title, start_at, end_at FROM entries "
                f"WHERE kind = 'absence' AND {cond} "
                f"AND start_at < %s AND (end_at IS NULL OR end_at >= %s) "
                f"ORDER BY start_at",
                (*params, end_range, start_range))
        else:
            absences = self._q(
                "SELECT title, start_at, end_at FROM entries "
                "WHERE kind = 'absence' "
                "AND start_at < %s AND (end_at IS NULL OR end_at >= %s) "
                "ORDER BY start_at",
                (end_range, start_range))

        # Termine/Erinnerungen/Aufgaben laden
        kind_cond = "AND kind = %s " if kind != "all" else ""
        kind_params = (kind,) if kind != "all" else ()

        if person_filter:
            cond, params = person_filter
            rows = self._q(
                f"SELECT kind, title, start_at, end_at FROM entries "
                f"WHERE kind != 'absence' {kind_cond}AND {cond} "
                f"AND start_at >= %s AND start_at < %s "
                f"ORDER BY start_at",
                (*kind_params, *params, start_range, end_range))
        else:
            rows = self._q(
                f"SELECT kind, title, start_at, end_at FROM entries "
                f"WHERE kind != 'absence' {kind_cond}"
                f"AND start_at >= %s AND start_at < %s "
                f"ORDER BY start_at",
                (*kind_params, start_range, end_range))

        if not rows and not absences:
            who = f" für '{person}'" if person else ""
            return f"Keine Einträge{who} in den nächsten {days} Tagen."

        lines = []

        # Abwesenheiten zuerst anzeigen (als Block)
        if absences:
            for title, start_at, end_at in absences:
                if end_at:
                    lines.append(f"🏖️ **{title}**: {_fmt_day(start_at)} – {_fmt_day(end_at)}")
                else:
                    lines.append(f"🏖️ **{title}**: {_fmt_day(start_at)}")
            lines.append("")

        # Nach Tag gruppieren (lokalisiert, damit 0-2 Uhr-Einträge richtig landen)
        by_day = {}
        for k, title, start_at, end_at in rows:
            day = _localize(start_at).strftime("%Y-%m-%d")
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

    def free_slots(self, persons, duration_min=60, date=None, horizon="woche"):
        """Findet gemeinsame freie Zeitslots für eine Gruppe von Personen.

        Logik:
        - Busy-Intervalle: Termine (appointment) der gegebenen Personen
        - Absences der Personen blockieren ebenfalls
        - Slots innerhalb der Geschäftszeiten (Mo-Fr, 09:00-18:00)
        - Nur Slots >= duration_min
        - Maximal 5 Vorschläge
        """
        if not persons or not str(persons).strip():
            return "❌ Keine Personen angegeben."

        # Personen aus String oder Liste
        if isinstance(persons, str):
            person_list = [p.strip() for p in re.split(r'[,\+]| und ', persons) if p.strip()]
        else:
            person_list = [str(p).strip() for p in (persons or []) if str(p).strip()]

        if not person_list:
            return "❌ Keine Personen angegeben."

        days = HORIZONS.get(horizon, 7)
        now = datetime.now(_TZ)
        start_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_range = start_range + timedelta(days=days)

        # Busy-Intervalle sammeln (Termine + Abwesenheiten der Personen)
        busy = []
        for person in person_list:
            p = f"%{person}%"
            # Termine der Person (owner oder participant)
            rows = self._q(
                "SELECT start_at, end_at FROM entries "
                "WHERE kind = 'appointment' "
                "AND (owner ILIKE %s OR EXISTS ("
                "SELECT 1 FROM unnest(participants) pp WHERE pp ILIKE %s)) "
                "AND start_at < %s AND (end_at IS NULL OR end_at > %s)",
                (p, p, end_range, start_range))
            for s, e in rows:
                busy.append((s, e or (s + timedelta(hours=1))))

            # Abwesenheiten der Person blockieren ebenfalls
            abs_rows = self._q(
                "SELECT start_at, end_at FROM entries "
                "WHERE kind = 'absence' "
                "AND (owner ILIKE %s OR EXISTS ("
                "SELECT 1 FROM unnest(participants) pp WHERE pp ILIKE %s)) "
                "AND start_at < %s AND (end_at IS NULL OR end_at > %s)",
                (p, p, end_range, start_range))
            for s, e in abs_rows:
                busy.append((s, e or end_range))

        busy.sort(key=lambda x: x[0])

        # Freie Slots innerhalb Geschäftszeiten finden
        duration = timedelta(minutes=max(15, int(duration_min or 60)))
        slots = []

        # Wenn konkretes Datum: nur dieser Tag; sonst alle Tage im Horizon
        if date:
            try:
                d = _norm_dt(date)
                if d:
                    day_list = [d]
                else:
                    day_list = []
            except Exception:
                day_list = []
        else:
            day_list = []
            cur_day = start_range
            while cur_day < end_range:
                day_list.append(cur_day)
                cur_day += timedelta(days=1)

        for day_start in day_list:
            # Geschäftszeiten: Mo-Fr, 09:00-18:00
            if day_start.weekday() >= 5:
                continue
            day_start = day_start.replace(hour=9, minute=0, second=0, microsecond=0)
            day_end = day_start.replace(hour=18, minute=0, second=0, microsecond=0)

            # Freie Intervalle an diesem Tag berechnen
            cursor = day_start
            for busy_start, busy_end in busy:
                # Überschneidung mit dem aktuellen Tag?
                if busy_end <= cursor or busy_start >= day_end:
                    continue
                # Freies Intervall vor diesem Busy-Block
                if busy_start > cursor and (busy_start - cursor) >= duration:
                    slots.append((cursor, min(busy_start, day_end)))
                cursor = max(cursor, busy_end)
                if cursor >= day_end:
                    break

            # Rest des Tages
            if cursor < day_end and (day_end - cursor) >= duration:
                slots.append((cursor, day_end))

            if len(slots) >= 5:
                break

        if not slots:
            names = ", ".join(person_list)
            return f"❌ Keine gemeinsamen freien Slots >= {int(duration.total_seconds()/60)} min für {names} gefunden."

        # Ausgabe formatieren
        names = ", ".join(person_list)
        lines = [f"🔍 Gemeinsam freie Slots für **{names}** (>= {int(duration.total_seconds()/60)} min):"]
        for s, e in slots[:5]:
            lines.append(f"• {_fmt_day(s)} {_fmt_time(s)} – {_fmt_time(e)}")

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
