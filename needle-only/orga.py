"""Orga-Schicht (v3.1 „Plan-Werkstatt"): eigenes Datenmodell + Plan-Resolver.
Sprache = Differenz-Spezifikation: Ops werden normalisiert (add⟷move), der ENDZUSTAND
wird berechnet (Kollisionen dort, nicht pro Schritt) und als Plan mit EINEM Approval
atomar ausgeführt. Fuzzy-Titel via pg_trgm (Tippfehler). psycopg direkt, kein serve."""
import re
import psycopg
from datetime import datetime, timedelta

from modules.postgres_store import _parse_dt
from modules.timesync import format_local, _TZ

SPACE = "privat"
_DAY_START, _DAY_END = 8, 20


def _norm_dt(value):
    if value is None or isinstance(value, datetime):
        return value
    return _parse_dt(str(value))


def _when(dt) -> str:
    return format_local(dt.isoformat() if hasattr(dt, "isoformat") else str(dt))


class Orga:
    def __init__(self, url: str):
        self.url = url
        self.trgm = False
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("""create table if not exists n_events (
                id bigserial primary key, space text not null default 'privat',
                owner text not null default 'ich', title text not null,
                start_at timestamptz not null, end_at timestamptz,
                status text not null default 'aktiv', location text not null default '',
                notes text not null default '', participants text[] not null default '{}',
                created_at timestamptz not null default now())""")
            c.execute("""create table if not exists n_reminders (
                id bigserial primary key, space text not null default 'privat',
                owner text not null default 'ich', title text not null,
                due_at timestamptz not null, state text not null default 'offen',
                created_at timestamptz not null default now())""")
            c.execute("""create table if not exists n_notes (
                id bigserial primary key, space text not null default 'privat',
                owner text not null default 'ich', subject text not null,
                body text not null default '', created_at timestamptz not null default now())""")
            c.execute("create index if not exists n_events_time_idx on n_events (space, start_at)")
            c.execute("create index if not exists n_reminders_due_idx on n_reminders (space, state, due_at)")
            try:
                c.execute("create extension if not exists pg_trgm")
                self.trgm = True
            except Exception:
                self.trgm = False

    def _q(self, sql, vals=(), conn=None):
        if conn is not None:
            cur = conn.execute(sql, vals)
            try:
                return cur.fetchall()
            except psycopg.ProgrammingError:
                return []
        with psycopg.connect(self.url) as c:
            cur = c.execute(sql, vals)
            try:
                rows = cur.fetchall()
            except psycopg.ProgrammingError:
                rows = []
            c.commit()
            return rows

    # ---------- Fuzzy-Ziele ----------
    def _find_event(self, title: str):
        if not title:
            return None
        if self.trgm:
            rows = self._q(
                "select id, title, start_at from n_events where space=%s and status='aktiv' "
                "and (title ilike %s or similarity(lower(title), lower(%s)) > 0.35) "
                "order by similarity(lower(title), lower(%s)) desc limit 1",
                (SPACE, f"%{title}%", title, title),
            )
        else:
            rows = self._q(
                "select id, title, start_at from n_events where space=%s and status='aktiv' "
                "and title ilike %s order by start_at asc limit 1", (SPACE, f"%{title}%"))
        return rows[0] if rows else None

    def _find_reminder(self, title: str):
        if not title:
            return None
        rows = self._q(
            "select id, title, due_at from n_reminders where space=%s and state='offen' "
            "and title ilike %s order by due_at asc limit 1", (SPACE, f"%{title}%"),
        )
        return rows[0] if rows else None

    # ---------- Plan (rein berechnend, schreibt nichts) ----------
    def _normalize(self, ops: list[dict], text: str = "") -> list[dict]:
        """Intent-Korrektur: upsert_reminder auf existierenden Termin → upsert_event;
        Needle-Titel ohne DB-Treffer → Titel aus dem Satz (Mention-Resolution)."""
        out = []
        for op in ops:
            a = dict(op.get("arguments") or {})
            if op.get("tool") == "upsert_reminder" and title_ok(a) \
                    and self._find_event(a.get("title", "")) and not self._find_reminder(a.get("title", "")):
                op = {"tool": "upsert_event", "arguments": a}
            if op.get("tool") in ("upsert_event", "cancel_event"):
                a = op["arguments"]
                fixed = self._mention_fix(str(a.get("title") or ""), text, _norm_dt(a.get("start_at")))
                if fixed != a.get("title"):
                    _dbg = fixed
                    a["title"] = fixed
                    a["_resolved_from_text"] = True
            out.append(op)
        return out

    def _mention_fix(self, title: str, text: str, start_dt):
        """Findet den im SATZ gemeinten bestehenden Eintrag, wenn der Modell-Titel keinen Treffer hat.
        Eindeutiger Treffer im Text → nehmen; mehrere → Segment mit passender Uhrzeit entscheidet."""
        low = (text or "").lower()
        if not low or not title:
            return title
        titles = list(dict.fromkeys(self.list_events("monat", plain=True)
                                    + self.list_reminders(plain=True)))
        hits = [t for t in titles if t.lower() in low]
        if not hits:
            return title
        if len(hits) == 1:
            return hits[0]
        segs = [s for s in re.split(r"\s*(?:,\s*|\bund\b|;)\s*", low) if s.strip()]
        if start_dt is not None:
            hhmm = start_dt.astimezone(_TZ).strftime("%H:%M")
            hour = start_dt.astimezone(_TZ).strftime("%H")
            for seg in segs:
                if hhmm in seg or f"{hour} uhr" in seg or f"{hour}uhr" in seg:
                    in_seg = [t for t in hits if t.lower() in seg]
                    if len(in_seg) == 1:
                        return in_seg[0]
        return title

    def plan_writes(self, ops: list[dict], text: str = "") -> dict:
        """Ops (bereits whitelisted/zeit-fixiert) → Plan: gerenderte Zeilen + ausführbare Ops."""
        lines, exec_ops, warn = [], [], []
        ops = self._normalize(ops, text)
        final = self._final_state(ops)
        for op in ops:
            tool, a = op["tool"], dict(op.get("arguments") or {})
            if tool == "upsert_event":
                title, start = (a.get("title") or "").strip(), _norm_dt(a.get("start_at"))
                shift = a.get("shift_min")
                hit = self._find_event(title)
                if hit:
                    new_start = start or (self._shift(hit[2], shift) if shift else None)
                    if new_start is None and not (a.get("location") or a.get("notes")):
                        lines.append(f"ABGELEHNT: '{a.get('title')}' — keine Änderung angegeben (Zeit fehlt).")
                        continue
                    line = f"ÄNDERN: {hit[1]}: {_when(hit[2])}"
                    if new_start and new_start != hit[2]:
                        line += f" → {_when(new_start)}"
                    if a.get("location"):
                        line += f" · Ort: {a['location']}"
                    lines.append(line)
                    exec_ops.append({"kind": "move", "id": hit[0], "title": hit[1],
                                     "start": new_start, "loc": a.get("location"),
                                     "notes": a.get("notes")})
                else:
                    if not title or start is None:
                        lines.append("ABGELEHNT: Neuer Termin braucht Datum und Uhrzeit.")
                        continue
                    lines.append(f"NEU: {title} ({_when(start)})")
                    exec_ops.append({"kind": "add", "title": title, "start": start,
                                     "end": _norm_dt(a.get("end_at")),
                                     "loc": a.get("location", ""), "notes": a.get("notes", "")})
            elif tool == "cancel_event":
                hit = self._find_event(a.get("title", ""))
                if hit:
                    lines.append(f"ABSAGEN: {hit[1]} ({_when(hit[2])})")
                    exec_ops.append({"kind": "cancel", "id": hit[0]})
                else:
                    lines.append(f"ABGELEHNT: Termin '{a.get('title')}' nicht gefunden "
                                 f"(Vorhanden: {', '.join(self.list_events('monat', plain=True)[:4]) or '—'}).")
            elif tool == "upsert_reminder":
                title, due = a.get("title") or "", _norm_dt(a.get("due_at"))
                if a.get("in_min"):
                    from datetime import timedelta as _td
                    due = datetime.now(_TZ) + _td(minutes=int(a["in_min"]))
                if not title or due is None:
                    lines.append("ABGELEHNT: Erinnerung braucht WANN (Zeit oder Minuten).")
                    continue
                rhit = self._find_reminder(title)
                if rhit:
                    lines.append(f"ERINNERUNG ÄNDERN: {rhit[1]}: {_when(rhit[2])} → {_when(due)}")
                    exec_ops.append({"kind": "rem_move", "id": rhit[0], "due": due})
                else:
                    lines.append(f"ERINNERUNG NEU: {title} ({_when(due)})")
                    exec_ops.append({"kind": "rem_add", "title": title, "due": due})
            elif tool == "complete_reminder":
                rhit = self._find_reminder(a.get("title", ""))
                if rhit:
                    lines.append(f"ERLEDIGT: {rhit[1]}")
                    exec_ops.append({"kind": "rem_done", "id": rhit[0]})
                else:
                    lines.append(f"ABGELEHNT: Erinnerung '{a.get('title')}' nicht gefunden.")
            elif tool == "remember_note":
                if not a.get("subject") or not a.get("body"):
                    lines.append("ABGELEHNT: Notiz braucht Thema und Inhalt.")
                    continue
                lines.append(f"NOTIZ: {a['subject']}")
                exec_ops.append({"kind": "note", "subject": a["subject"], "body": a["body"]})
        # Endzustand-Kollisionen
        for a_start, a_end, a_title, b_title in self._collisions_in(final):
            warn.append(f"Kollision im Endzustand: {a_title} ↔ {b_title} ({_when(a_start)})")
        return {"lines": lines, "ops": exec_ops, "warn": warn}

    def _shift(self, base, minutes):
        return base + timedelta(minutes=minutes) if (base and minutes) else None

    def _final_state(self, ops: list[dict]) -> list[tuple]:
        """Endzustand: aktive Termine + Plan (move/add, cancel entfernt) — für Kollisionscheck."""
        rows = self._q("select id, title, start_at, coalesce(end_at, start_at + interval '30 minutes') "
                       "from n_events where space=%s and status='aktiv'", (SPACE,))
        state = {r[0]: [r[1], r[2], r[3]] for r in rows}
        for op in ops:
            tool, a = op.get("tool"), dict(op.get("arguments") or {})
            if tool == "upsert_event":
                hit = self._find_event(a.get("title", ""))
                start = _norm_dt(a.get("start_at"))
                if hit:
                    end = _norm_dt(a.get("end_at"))
                    state[hit[0]] = [hit[1], start or hit[2],
                                     end or (start + timedelta(minutes=60) if start else state[hit[0]][2])]
                elif title_ok(a) and start:
                    state[-(len(state) + 1)] = [a.get("title"), start,
                                                _norm_dt(a.get("end_at")) or start + timedelta(minutes=60)]
            elif tool == "cancel_event":
                hit = self._find_event(a.get("title", ""))
                if hit:
                    state.pop(hit[0], None)
        return [(v[1], v[2], v[0]) for v in state.values() if v[1]]

    def _collisions_in(self, state) -> list[tuple]:
        items = sorted((s, e, t) for s, e, t in state if s and e)
        out, seen = [], set()
        for i, (s1, e1, t1) in enumerate(items):
            for s2, e2, t2 in items[i + 1:]:
                if s1.date() != s2.date() or s2 >= e1 or e2 <= s1:
                    continue
                key = tuple(sorted((t1, t2)))
                if key not in seen:
                    seen.add(key)
                    out.append((max(s1, s2), None, f"{t1} ({_when(s1)})", t2))
                    if len(out) >= 3:
                        return out
        return out

    # ---------- Ausführung (1 Transaktion) ----------
    def apply_plan(self, plan: dict) -> str:
        with psycopg.connect(self.url) as c:
            for op in plan["ops"]:
                k = op["kind"]
                if k == "add":
                    c.execute("insert into n_events (title, start_at, end_at, location, notes) "
                              "values (%s,%s,%s,%s,%s)",
                              (op["title"], op["start"], op.get("end"), op.get("loc") or "", op.get("notes") or ""))
                elif k == "move":
                    sets, vals = ["updated_at = now()"], []
                    if op.get("start"):
                        sets.append("start_at = %s")
                        vals.append(op["start"])
                    if op.get("loc") is not None:
                        sets.append("location = %s")
                        vals.append(op["loc"])
                    if op.get("notes") is not None:
                        sets.append("notes = %s")
                        vals.append(op["notes"])
                    vals.append(op["id"])
                    c.execute(f"update n_events set {', '.join(sets)} where id = %s", vals)
                elif k == "cancel":
                    c.execute("update n_events set status='abgesagt', updated_at=now() where id=%s", (op["id"],))
                elif k == "rem_add":
                    c.execute("insert into n_reminders (title, due_at) values (%s,%s)",
                              (op["title"], op["due"]))
                elif k == "rem_move":
                    c.execute("update n_reminders set due_at=%s where id=%s", (op["due"], op["id"]))
                elif k == "rem_done":
                    c.execute("update n_reminders set state='erledigt' where id=%s", (op["id"],))
                elif k == "note":
                    c.execute("insert into n_notes (subject, body) values (%s,%s)",
                              (op["subject"], op["body"]))
            c.commit()
        return f"Plan ausgeführt: {len(plan['ops'])} Änderung(en)."

    # ---------- Reads (gerendert) ----------
    def list_events(self, horizon: str = "woche", plain: bool = False):
        days = {"heute": 1, "woche": 7, "monat": 31}.get(horizon, 7)
        start = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self._q(
            "select title, start_at from n_events where space=%s and status='aktiv' "
            "and start_at >= %s and start_at < %s order by start_at",
            (SPACE, start, start + timedelta(days=days)),
        )
        return [t for t, _s in rows] if plain else _render_list("Termine", horizon, rows)

    def free_slots(self, horizon: str = "woche") -> str:
        days = {"heute": 1, "woche": 7, "monat": 31}.get(horizon, 7)
        start0 = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self._q(
            "select start_at, coalesce(end_at, start_at + interval '30 minutes') from n_events "
            "where space=%s and status='aktiv' and start_at >= %s and start_at < %s order by start_at",
            (SPACE, start0, start0 + timedelta(days=days)),
        )
        by_day: dict = {}
        for s, e in rows:
            s, e = s.astimezone(_TZ), e.astimezone(_TZ)
            if s.date() != e.date():
                e = s.replace(hour=_DAY_END, minute=0)
            by_day.setdefault(s.date(), []).append((s, e))
        lines = []
        for i in range(days):
            day = start0.date() + timedelta(days=i)
            cursor = datetime(day.year, day.month, day.day, _DAY_START, tzinfo=_TZ)
            limit = datetime(day.year, day.month, day.day, _DAY_END, tzinfo=_TZ)
            gaps = []
            for s, e in by_day.get(day, []):
                if s > cursor and (s - cursor).total_seconds() >= 3600:
                    gaps.append(f"{cursor.strftime('%H:%M')}–{s.strftime('%H:%M')}")
                cursor = max(cursor, e)
            if limit > cursor and (limit - cursor).total_seconds() >= 3600:
                gaps.append(f"{cursor.strftime('%H:%M')}–{limit.strftime('%H:%M')}")
            if gaps:
                lines.append(f"  {day.strftime('%a %d.%m')}: " + ", ".join(gaps))
        return ("Freie Zeiten (8–20 Uhr, ≥60 min):\n" + "\n".join(lines)) if lines else "Keine größeren Lücken."

    def add_reminder_direct(self, title: str, due_at) -> str:
        plan = self.plan_writes([{"tool": "upsert_reminder",
                                  "arguments": {"title": title, "due_at": _norm_dt(due_at)}}])
        if plan["ops"]:
            return self.apply_plan(plan) + "\n" + "\n".join(plan["lines"])
        return "\n".join(plan["lines"]) or "ABGELEHNT"

    def complete_reminder_direct(self, title: str) -> str:
        ops = [{"tool": "complete_reminder", "arguments": {"title": title}}]
        plan = self.plan_writes(ops)
        return self.apply_plan(plan) + "\n" + "\n".join(plan["lines"]) if plan["ops"] else "\n".join(plan["lines"])

    def list_reminders(self, horizon: str = "monat", plain: bool = False):
        days = {"heute": 1, "woche": 7, "monat": 31}.get(horizon, 31)
        end = datetime.now(_TZ) + timedelta(days=days)
        rows = self._q(
            "select title, due_at from n_reminders where space=%s and state='offen' "
            "and due_at < %s order by due_at", (SPACE, end),
        )
        return _render_list("Erinnerungen (offen)", horizon, rows) if not plain else [t for t, _d in rows]

    def add_note(self, subject: str, body: str) -> str:
        plan = self.plan_writes([{"tool": "remember_note",
                                  "arguments": {"subject": subject, "body": body}}])
        if not plan["ops"]:
            return "\n".join(plan["lines"]) or "ABGELEHNT: Notiz braucht Thema und Inhalt."
        return self.apply_plan(plan) + "\n" + plan["lines"][0]

    def find_notes(self, query: str) -> str:
        rows = self._q(
            "select subject, body from n_notes where space=%s and (subject ilike %s or body ilike %s) "
            "order by created_at desc limit 5", (SPACE, f"%{query}%", f"%{query}%"),
        )
        if not rows:
            return f"Nichts gefunden zu '{query}'."
        return "Gefunden:\n" + "\n".join(f"  · {s}: {b[:70]}" for s, b in rows)

    def status(self) -> str:
        rows = self._q(
            "select (select count(*) from n_events where status='aktiv'), "
            "(select count(*) from n_reminders where state='offen'), (select count(*) from n_notes)")
        e, r, n = rows[0]
        return f"Termine {e} · Erinnerungen offen {r} · Notizen {n}"


def title_ok(a: dict) -> bool:
    return bool((a.get("title") or "").strip())


def to_utc_iso(value):
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else _parse_dt(str(value))
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return dt


def _render_list(head: str, horizon: str, rows) -> str:
    if not rows:
        return f"{head}: keine Einträge."
    lines = [f"  {_when(s)}  {t}" for t, s in rows]
    return f"{head} ({horizon}):\n" + "\n".join(lines)
