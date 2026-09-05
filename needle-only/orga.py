"""Orga-Schicht v4.0 „Merge": entries-Tabelle (eine Tabelle für alles Zeitliche),
Tool-Oberfläche und Planner-Semantik exakt wie v3.1 (bewährt, 17/24).
Sprache = Differenz-Spezifikation: Ops werden normalisiert (add⟷move), der ENDZUSTAND
wird berechnet (Kollisionen dort, nicht pro Schritt) und als Plan mit EINEM Approval
atomar ausgeführt. Fuzzy-Titel via pg_trgm. psycopg direkt, kein serve."""
import json
import re
import psycopg
from datetime import datetime, timedelta
from uuid import uuid4

from modules.postgres_store import _parse_dt
from modules.timesync import format_local, _TZ

DEFAULT_DUR_MIN = 60
_ENTRY_KINDS = ("appointment", "reminder", "task")


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
            c.execute("""create table if not exists entries (
                id bigserial primary key, owner text not null default 'ich',
                kind text not null check (kind in ('appointment', 'reminder', 'task')),
                title text not null, start_at timestamptz not null, end_at timestamptz,
                status text not null default 'active' check (status in ('active', 'done', 'cancelled')),
                alarm_min integer, location text not null default '', notes text not null default '',
                participants text[] not null default '{}', source_id bigint,
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now())""")
            c.execute("""create table if not exists n_notes (
                id bigserial primary key, owner text not null default 'ich',
                subject text not null, body text not null default '',
                created_at timestamptz not null default now())""")
            c.execute("""create table if not exists entry_changes (
                id bigserial primary key, batch_id uuid not null,
                entry_id bigint not null references entries(id) on delete cascade,
                action text not null, old_values jsonb, new_values jsonb,
                actor text not null default 'needle',
                created_at timestamptz not null default now())""")
            c.execute("create index if not exists entries_time_idx on entries (owner, start_at)")
            c.execute("create index if not exists entries_kind_idx on entries (kind, status)")
            # v4.2: alarmed_at für einmaliges Appointment-Alarm-Firing
            c.execute("alter table entries add column if not exists alarmed_at timestamptz")
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
                "select id, title, start_at from entries "
                "where kind='appointment' and status='active' "
                "and (title ilike %s or similarity(title, %s) > 0.35) "
                "order by similarity(title, %s) desc, start_at asc limit 1",
                (f"%{title}%", title, title),
            )
        else:
            rows = self._q(
                "select id, title, start_at from entries "
                "where kind='appointment' and status='active' "
                "and title ilike %s order by start_at asc limit 1",
                (f"%{title}%",),
            )
        return rows[0] if rows else None

    def _shift(self, base, minutes):
        return base + timedelta(minutes=minutes) if (base and minutes) else base

    def _find_reminder(self, title: str):
        """Bidirektionales Matching: 'wasser-erinnerung' findet 'wasser'."""
        if not title:
            return None
        low = title.lower()
        rows = self._q(
            "select id, title, start_at from entries "
            "where kind='reminder' and status='active' "
            "order by start_at asc")
        for row in rows:
            db_title = (row[1] or "").lower()
            if low in db_title or db_title in low:
                return row
        return None

    # ---------- Plan (rein berechnend, schreibt nichts) ----------
    def plan_writes(self, ops: list[dict], text: str = "") -> dict:
        """Ops → Plan: gerenderte Zeilen, Warnungen, ausführbare Ops."""
        lines, exec_ops = [], []
        for op in ops:
            tool, a = op.get("tool"), dict(op.get("arguments") or {})
            title = (a.get("title") or "").strip()
            if tool == "upsert_event":
                start = _norm_dt(a.get("start_at"))
                shift = a.get("shift_min")
                hit = self._find_event(title)
                if hit:
                    new_start = start or (self._shift(hit[2], shift) if shift else hit[2])
                    old_when, new_when = _when(hit[2]), _when(new_start)
                    line = f"ÄNDERN: {hit[1]}"
                    if old_when != new_when:
                        line += f" · {old_when} → {new_when}"
                    if a.get("location"):
                        line += f" · Ort: {a['location']}"
                    lines.append(line)
                    exec_ops.append({"kind": "move", "id": hit[0], "title": hit[1],
                                     "start": new_start, "end": _norm_dt(a.get("end_at")),
                                     "loc": a.get("location", ""),
                                     "notes": a.get("notes", ""),
                                     "part": a.get("participants", ""),
                                     "alarm": a.get("alarm_min")})
                else:
                    if not title or start is None:
                        lines.append(f"ABGELEHNT: '{title or '—'}' — Titel und Zeit nötig.")
                        continue
                    line = f"NEU: {title} ({_when(start)})"
                    if a.get("alarm_min"):
                        line += f" · Alarm {a['alarm_min']}min vorher"
                    if a.get("location"):
                        line += f" · Ort: {a['location']}"
                    lines.append(line)
                    exec_ops.append({"kind": "add", "title": title, "start": start,
                                     "end": _norm_dt(a.get("end_at")),
                                     "loc": a.get("location", ""),
                                     "notes": a.get("notes", ""),
                                     "part": a.get("participants", ""),
                                     "alarm": a.get("alarm_min")})
            elif tool == "upsert_reminder":
                due = _norm_dt(a.get("due_at"))
                if a.get("in_min"):
                    due = datetime.now(_TZ) + timedelta(minutes=int(a["in_min"]))
                if not title or due is None:
                    lines.append("ABGELEHNT: Erinnerung braucht Titel und WANN (Zeit oder Minuten).")
                    continue
                hit = self._find_reminder(title)
                if hit:
                    lines.append(f"ERINNERUNG ÄNDERN: {hit[1]}: {_when(hit[2])} → {_when(due)}")
                    exec_ops.append({"kind": "rem_move", "id": hit[0], "title": hit[1], "start": due})
                else:
                    lines.append(f"ERINNERUNG NEU: {title} ({_when(due)})")
                    exec_ops.append({"kind": "rem_add", "title": title, "start": due})
            elif tool == "cancel_event":
                # Suche zuerst Termine, dann Erinnerungen
                hit = self._find_event(title)
                if not hit:
                    hit = self._find_reminder(title)
                if hit:
                    lines.append(f"ABSAGEN: {hit[1]} ({_when(hit[2])})")
                    exec_ops.append({"kind": "cancel", "id": hit[0], "title": hit[1]})
                else:
                    lines.append(f"ABGELEHNT: '{title}' nicht gefunden.")
            elif tool == "complete_reminder":
                hit = self._find_reminder(title)
                if hit:
                    lines.append(f"ERLEDIGT: {hit[1]} ({_when(hit[2])})")
                    exec_ops.append({"kind": "rem_done", "id": hit[0], "title": hit[1]})
                else:
                    lines.append(f"ABGELEHNT: Erinnerung '{title}' nicht gefunden.")
            elif tool == "remember_note":
                subject, body = a.get("subject") or "", a.get("body") or ""
                if not subject or not body:
                    lines.append("ABGELEHNT: Notiz braucht Thema und Inhalt.")
                    continue
                lines.append(f"NOTIZ: {subject} = {body[:60]}")
                exec_ops.append({"kind": "note", "subject": subject, "body": body})
        # Endzustand-Kollisionen: DB-State + geplante Ops simulieren
        warn = self._collisions_after(exec_ops)
        return {"lines": lines, "ops": exec_ops, "warn": warn}

    def _collisions_after(self, exec_ops: list[dict]) -> list[str]:
        """Simuliert den Endzustand (DB + geplante Ops) und prüft auf Kollisionen."""
        rows = self._q(
            "select id, title, start_at, coalesce(end_at, start_at + interval '60 minutes') "
            "from entries where kind='appointment' and status='active'")
        state = {r[0]: [r[1], r[2], r[3]] for r in rows}
        next_id = max(state.keys(), default=0) + 1
        for op in exec_ops:
            k = op["kind"]
            if k in ("add", "rem_add"):
                end = op.get("end") or (op["start"] + timedelta(minutes=DEFAULT_DUR_MIN))
                state[next_id] = [op["title"], op["start"], end]
                next_id += 1
            elif k in ("move", "rem_move") and op["id"] in state:
                start = op.get("start") or state[op["id"]][1]
                end = op.get("end") or (start + timedelta(minutes=DEFAULT_DUR_MIN))
                state[op["id"]] = [state[op["id"]][0], start, end]
            elif k in ("cancel", "rem_done"):
                state.pop(op["id"], None)
        items = sorted(state.values(), key=lambda x: x[1])
        warns = []
        for i, (t1, s1, e1) in enumerate(items):
            for t2, s2, e2 in items[i+1:]:
                if s1.date() == s2.date() and s2 < e1 and s1 < e2:
                    warns.append(f"Kollision im Endzustand: {t1} ↔ {t2} ({_when(max(s1, s2))})")
        return warns

    # ---------- Ausführung (1 Transaktion, mit Audit) ----------
    def apply_plan(self, plan: dict) -> str:
        batch_id = uuid4()
        with psycopg.connect(self.url) as c:
            for op in plan["ops"]:
                k = op["kind"]
                if k == "add":
                    cur = c.execute(
                        """insert into entries (kind, title, start_at, end_at, location, notes, participants, alarm_min)
                           values ('appointment',%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (op["title"], op["start"], op.get("end"),
                         op.get("loc") or "", op.get("notes") or "",
                         _parse_participants(op.get("part")), op.get("alarm")))
                    new_id = cur.fetchone()[0]
                    c.execute(
                        """insert into entry_changes (batch_id, entry_id, action, new_values)
                           values (%s,%s,'create',%s)""",
                        (batch_id, new_id, json.dumps({"kind": "appointment", "title": op["title"],
                                                       "start_at": str(op["start"])}, default=str)))
                elif k == "move":
                    c.execute(
                        """update entries set start_at=%s, end_at=%s, location=%s, notes=%s,
                           participants=%s, alarm_min=%s, updated_at=now() where id=%s""",
                        (op["start"], op.get("end"), op.get("loc") or "",
                         op.get("notes") or "", _parse_participants(op.get("part")),
                         op.get("alarm"), op["id"]))
                    c.execute(
                        """insert into entry_changes (batch_id, entry_id, action, new_values)
                           values (%s,%s,'update',%s)""",
                        (batch_id, op["id"], json.dumps({"start_at": str(op["start"])}, default=str)))
                elif k == "cancel":
                    c.execute("update entries set status='cancelled', updated_at=now() where id=%s",
                              (op["id"],))
                    c.execute(
                        """insert into entry_changes (batch_id, entry_id, action, new_values)
                           values (%s,%s,'cancel',%s)""",
                        (batch_id, op["id"], json.dumps({"status": "cancelled"})))
                elif k == "rem_add":
                    cur = c.execute(
                        """insert into entries (kind, title, start_at, alarm_min)
                           values ('reminder',%s,%s,0) returning id""",
                        (op["title"], op["start"]))
                    new_id = cur.fetchone()[0]
                    c.execute(
                        """insert into entry_changes (batch_id, entry_id, action, new_values)
                           values (%s,%s,'create',%s)""",
                        (batch_id, new_id, json.dumps({"kind": "reminder", "title": op["title"],
                                                       "start_at": str(op["start"])}, default=str)))
                elif k == "rem_move":
                    c.execute("update entries set start_at=%s, updated_at=now() where id=%s",
                              (op["start"], op["id"]))
                    c.execute(
                        """insert into entry_changes (batch_id, entry_id, action, new_values)
                           values (%s,%s,'update',%s)""",
                        (batch_id, op["id"], json.dumps({"start_at": str(op["start"])}, default=str)))
                elif k == "rem_done":
                    c.execute("update entries set status='done', updated_at=now() where id=%s",
                              (op["id"],))
                    c.execute(
                        """insert into entry_changes (batch_id, entry_id, action, new_values)
                           values (%s,%s,'complete',%s)""",
                        (batch_id, op["id"], json.dumps({"status": "done"})))
                elif k == "note":
                    c.execute("insert into n_notes (subject, body) values (%s,%s)",
                              (op["subject"], op["body"]))
            c.commit()
        return f"Plan ausgeführt: {len(plan['ops'])} Änderung(en)."

    def undo_last(self) -> str:
        """Letzten Batch rückgängig machen (Audit-Trail)."""
        batch = self._q("select distinct batch_id from entry_changes order by batch_id desc limit 1")
        if not batch:
            return "Nichts zum Rückgängigmachen."
        batch_id = batch[0][0]
        with psycopg.connect(self.url) as c:
            changes = c.execute(
                "select entry_id, action from entry_changes where batch_id = %s order by id desc",
                (batch_id,)).fetchall()
            for entry_id, action in changes:
                if action == "create":
                    c.execute("delete from entries where id = %s", (entry_id,))
                elif action in ("update", "cancel", "complete"):
                    c.execute("select old_values from entry_changes "
                              "where entry_id = %s and action = %s order by id desc limit 1",
                              (entry_id, action))
                    row = c.fetchone()
                    if row and row[0]:
                        old = json.loads(row[0])
                        sets = []
                        if "start_at" in old:
                            sets.append(f"start_at = '{old['start_at']}'")
                        if sets:
                            c.execute(f"update entries set {', '.join(sets)} where id = %s",
                                      (entry_id,))
            c.execute("delete from entry_changes where batch_id = %s", (batch_id,))
            c.commit()
        return f"Batch {str(batch_id)[:8]} rückgängig gemacht ({len(changes)} Ops)."

    # ---------- Reads (gerendert) ----------
    def list_entries(self, what: str = "termine", horizon: str = "woche") -> str:
        days = {"heute": 1, "woche": 7, "monat": 31}.get(horizon, 7)
        start = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=days)
        what_map = {"termine": "appointment", "erinnerungen": "reminder",
                    "todos": "task", "aufgaben": "task", "tasks": "task"}
        target = what_map.get(what.lower(), "appointment")
        # NUR aktive Einträge zeigen (abgesagte/erledigte ausblenden)
        rows = self._q(
            "select kind, title, start_at, status from entries "
            "where kind = %s and status = 'active' "
            "and start_at >= %s and start_at < %s "
            "order by start_at",
            (target, start, end))
        if not rows:
            return f"Keine aktiven {what} in den nächsten {days} Tagen."
        lines = []
        for kind, title, start_at, status in rows:
            lines.append(f"  {_when(start_at)}  {title}")
        return f"{what.title()} ({horizon}):\n" + "\n".join(lines)

    def free_slots(self, horizon: str = "woche") -> str:
        days = {"heute": 1, "woche": 7, "monat": 31}.get(horizon, 7)
        start0 = datetime.now(_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = self._q(
            "select start_at, coalesce(end_at, start_at + interval '30 minutes') "
            "from entries where kind='appointment' and status='active' "
            "and start_at >= %s and start_at < %s order by start_at",
            (start0, start0 + timedelta(days=days)))
        by_day: dict = {}
        for s, e in rows:
            s, e = s.astimezone(_TZ), e.astimezone(_TZ)
            if s.date() != e.date():
                e = s.replace(hour=20, minute=0)
            by_day.setdefault(s.date(), []).append((s, e))
        lines = []
        for i in range(days):
            day = start0.date() + timedelta(days=i)
            cursor = datetime(day.year, day.month, day.day, 8, tzinfo=_TZ)
            limit = datetime(day.year, day.month, day.day, 20, tzinfo=_TZ)
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

    def find_notes(self, query: str) -> str:
        if not query:
            return "Bitte Suchbegriff angeben."
        rows = self._q(
            "select subject, body from n_notes "
            "where subject ilike %s or body ilike %s "
            "order by created_at desc limit 5",
            (f"%{query}%", f"%{query}%"))
        if not rows:
            return f"Nichts gefunden zu '{query}'."
        return "Gefunden:\n" + "\n".join(f"  · {s}: {b[:70]}" for s, b in rows)

    def list_notes(self) -> str:
        """Alle Notizen (neueste zuerst, max 20)."""
        rows = self._q(
            "select subject, body from n_notes "
            "order by created_at desc limit 20")
        if not rows:
            return "Keine Notizen gespeichert."
        return "Notizen:\n" + "\n".join(f"  · {s}: {b[:70]}" for s, b in rows)

    def status(self) -> str:
        rows = self._q(
            "select count(*) filter (where kind='appointment' and status='active'), "
            "count(*) filter (where kind='reminder' and status='active'), "
            "count(*) filter (where kind='task' and status='active'), "
            "(select count(*) from n_notes), "
            "(select count(*) from entry_changes) from entries")
        if not rows:
            return "Keine Daten."
        a, r, t, n, ch = rows[0]
        return (f"Termine: {a} · Erinnerungen: {r} · Aufgaben: {t} · "
                f"Notizen: {n} · Änderungen: {ch}")


def _parse_participants(raw) -> list:
    """'lisa, tom' → ['lisa', 'tom'] (kleingeschrieben, getrimmt)."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(p).strip().lower() for p in raw if str(p).strip()]
    return [p.strip().lower() for p in str(raw).split(",") if p.strip()]
