#!/usr/bin/env python3
"""Calendar-Service vNext: Router → task-spezifisches Needle-FT → DB.

Pipeline:
  1. Router (deterministisch) entscheidet: write / read / reminder / none
  2. Task-spezifisches Needle-FT-Modell extrahiert Argumente (Grounded Spans)
  3. Deterministischer Planner führt die DB-Operation aus

Der Service nutzt die trainierten .cact-Modelle aus calendar_ft/models/.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import needle

FT_DIR = Path(__file__).resolve().parent / "calendar_ft"
SCHEMAS = FT_DIR / "schemas"
MODELS = FT_DIR / "models"

# ============================================================================
# Modelle
# ============================================================================


class CalendarModels:
    """Hält alle .cact-Modelle für die verschiedenen Tasks."""

    def __init__(self):
        self.models = {}
        self.base_agent = None
        self._load_models()

    def _load_models(self):
        task_files = {
            "calendar_write": "calendar_write.cact",
            "calendar_read": "calendar_read.cact",
            "reminder": "reminder.cact",
        }
        for task, filename in task_files.items():
            cact_path = MODELS / filename
            if cact_path.exists():
                tool_schema = json.loads(
                    (SCHEMAS / f"{task}.json").read_text()
                )
                agent = needle.Needle(
                    tools=[tool_schema],
                    weights=str(cact_path),
                )
                self.models[task] = agent
                print(f"[models] Loaded {task} from {cact_path}")

        # Base-Modell für Fallback laden (mit ALLEN Tool-Schemas)
        all_schemas = []
        for schema_file in sorted(SCHEMAS.glob("*.json")):
            schema = json.loads(schema_file.read_text())
            all_schemas.append(schema)
        if all_schemas:
            self.base_agent = needle.Needle(tools=all_schemas)
            print(f"[models] Loaded base model with {len(all_schemas)} tools for fallback")

    def get(self, task: str):
        return self.models.get(task)

    def infer(self, task: str, query: str) -> dict:
        """Run inference with the task-specific model.

        Falls das FT-Modell keine function_calls liefert (z.B. bei
        "Missing required parameter"-Verweigerung), fällt der Service
        auf das Base-Modell zurück.
        """
        agent = self.get(task)
        if agent is None:
            return {"function_calls": [], "error": f"model not loaded: {task}"}
        agent.reset()  # Reset conversation history for clean inference
        response = agent.complete(query)
        calls = response.get("function_calls") or []

        # Fallback: wenn FT-Modell leer liefert, versuche Base-Modell
        if not calls and getattr(self, "base_agent", None):
            self.base_agent.reset()
            base_resp = self.base_agent.complete(query)
            base_calls = base_resp.get("function_calls") or []
            if base_calls:
                return {"name": base_calls[0].get("name", task),
                        "arguments": base_calls[0].get("arguments", {}),
                        "reasoning": base_resp.get("reasoning", ""),
                        "source": "base_fallback"}

        if calls:
            return {"name": calls[0].get("name", task),
                    "arguments": calls[0].get("arguments", {}),
                    "reasoning": response.get("reasoning", "")}
        return {"name": None, "arguments": {}, "reasoning": ""}


# ============================================================================
# Deterministischer Planner (SQLite für Tests)
# ============================================================================


class CalendarPlanner:
    """Deterministic planner that executes calendar operations."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self.now = datetime(2026, 9, 11, 10, 0)  # Fixed test date

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                start_at TEXT NOT NULL,
                end_at TEXT,
                location TEXT,
                status TEXT DEFAULT 'active',
                participants TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.commit()

    def _resolve_date(self, date_str: str) -> datetime:
        """Resolve a German date phrase to a datetime."""
        if not date_str:
            return self.now
        low = date_str.lower().strip()
        # Relative days
        if "übermorgen" in low or "uebermorgen" in low:
            base = self.now + timedelta(days=2)
        elif "morgen" in low and "übermorgen" not in low:
            base = self.now + timedelta(days=1)
        elif "heute" in low:
            base = self.now
        else:
            base = self.now
        return base

    def _resolve_time(self, time_str: str) -> tuple[int, int]:
        """Resolve a time phrase to (hour, minute)."""
        if not time_str:
            return 9, 0
        low = time_str.lower().strip()
        # Try digital time first (14:30, 14.30)
        m = re.search(r"(\d{1,2})[:.](\d{2})", low)
        if m:
            return int(m.group(1)), int(m.group(2))
        # Try "halb X" (halb drei = 2:30)
        m = re.search(r"halb\s+(\S+)", low)
        if m:
            word = m.group(1).lower()
            words = {"eins": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
                     "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
                     "zehn": 10, "elf": 11, "zwölf": 12}
            for w, h in words.items():
                if w in word:
                    return h - 1, 30
            return 2, 30
        # Try "X Uhr" or just "X"
        m = re.search(r"(\d{1,2})", low)
        if m:
            return int(m.group(1)), 0
        return 9, 0

    def _resolve_when(self, when_str: str) -> datetime:
        """Resolve a 'when' phrase to a datetime."""
        if not when_str:
            return self.now + timedelta(days=1)
        low = when_str.lower().strip()

        # Check for "date um time" compound
        m = re.search(r"^(.*?)\s+um\s+(.*)$", low)
        if m:
            date_part = m.group(1)
            time_part = m.group(2)
            base = self._resolve_date(date_part)
            h, min_ = self._resolve_time(time_part)
            return base.replace(hour=h, minute=min_, second=0, microsecond=0)

        # Just a date
        base = self._resolve_date(low)
        return base.replace(hour=9, minute=0, second=0, microsecond=0)

    def create_appointment(self, args: dict) -> dict:
        """Create a new appointment in the DB."""
        title = args.get("title", "Termin")
        date_str = args.get("date", "")
        time_str = args.get("time", "")

        # Resolve date/time
        base = self._resolve_date(date_str)
        if time_str:
            h, m = self._resolve_time(time_str)
            start = base.replace(hour=h, minute=m, second=0, microsecond=0)
        else:
            start = base.replace(hour=9, minute=0, second=0, microsecond=0)

        end = start + timedelta(hours=1)
        location = args.get("location", "")
        participants = args.get("participants", "")

        cursor = self.conn.execute(
            """INSERT INTO appointments (title, start_at, end_at, location, participants)
               VALUES (?, ?, ?, ?, ?)""",
            (title, start.isoformat(), end.isoformat(), location, participants)
        )
        self.conn.commit()
        return {
            "action": "create",
            "id": cursor.lastrowid,
            "title": title,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "location": location,
            "participants": participants,
        }

    def read_calendar(self, args: dict) -> dict:
        """Read appointments from the DB."""
        when_str = args.get("when", "")
        person = args.get("person", "")

        query = "SELECT * FROM appointments WHERE status = 'active'"
        params = []

        if when_str:
            start_dt = self._resolve_when(when_str)
            end_dt = start_dt + timedelta(days=1)
            query += " AND start_at >= ? AND start_at < ?"
            params.extend([start_dt.isoformat(), end_dt.isoformat()])

        if person:
            query += " AND participants LIKE ?"
            params.append(f"%{person}%")

        query += " ORDER BY start_at"
        rows = self.conn.execute(query, params).fetchall()

        events = []
        for row in rows:
            events.append({
                "id": row["id"],
                "title": row["title"],
                "start_at": row["start_at"],
                "end_at": row["end_at"],
                "location": row["location"],
                "participants": row["participants"],
            })
        return {"action": "read", "events": events, "count": len(events)}

    def create_reminder(self, args: dict) -> dict:
        """Create a reminder (relative to an appointment)."""
        target = args.get("target", "")
        relative = args.get("relative", "")
        when_str = args.get("when", "")

        if when_str:
            remind_at = self._resolve_when(when_str)
        elif relative and target:
            # Find the appointment, then subtract the relative offset
            rows = self.conn.execute(
                "SELECT * FROM appointments WHERE title LIKE ? AND status = 'active' ORDER BY start_at",
                (f"%{target}%",)
            ).fetchall()
            if rows:
                appt_start = datetime.fromisoformat(rows[0]["start_at"])
                # Parse relative offset
                m = re.search(r"(\d+)\s*(minuten?|stunden?)", relative.lower())
                if m:
                    amount = int(m.group(1))
                    unit = m.group(2)
                    if "stunde" in unit:
                        remind_at = appt_start - timedelta(hours=amount)
                    else:
                        remind_at = appt_start - timedelta(minutes=amount)
                else:
                    remind_at = appt_start - timedelta(minutes=20)
            else:
                remind_at = self.now + timedelta(hours=1)
        else:
            remind_at = self.now + timedelta(hours=1)

        cursor = self.conn.execute(
            "INSERT INTO reminders (title, remind_at) VALUES (?, ?)",
            (target or "Erinnerung", remind_at.isoformat())
        )
        self.conn.commit()
        return {
            "action": "reminder",
            "id": cursor.lastrowid,
            "target": target,
            "remind_at": remind_at.isoformat(),
        }


# ============================================================================
# Calendar Service (Haupt-Pipeline)
# ============================================================================


class CalendarService:
    """Main service: router → needle-FT → planner → response."""

    def __init__(self, db_path: str = ":memory:"):
        self.router = None  # loaded lazily
        self.models = CalendarModels()
        self.planner = CalendarPlanner(db_path)

    def _get_router(self):
        if self.router is None:
            from router_calendar import route_calendar
            self.router = route_calendar
        return self.router

    def handle(self, text: str) -> dict:
        """Process a user query through the full pipeline."""
        # 1. Route
        route = self._get_router()(text)

        # 2. Infer arguments with task-specific model
        if route in ("calendar_write", "calendar_read", "reminder"):
            inference = self.models.infer(route, text)
            args = inference.get("arguments", {})
        else:
            inference = {"name": None, "arguments": {}}
            args = {}

        # 3. Execute via planner
        if route == "calendar_write" and args:
            result = self.planner.create_appointment(args)
        elif route == "calendar_read" and args:
            result = self.planner.read_calendar(args)
        elif route == "reminder" and args:
            result = self.planner.create_reminder(args)
        else:
            result = {"action": "none", "message": "Kein Kalender-Intent erkannt."}

        return {
            "query": text,
            "route": route,
            "inference": inference,
            "result": result,
        }
