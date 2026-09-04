import re
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import needle
except Exception:  # pragma: no cover
    needle = None

from modules.postgres_store import PostgresStore
from modules.timesync import now, resolve_dt, to_utc_iso, _TZ

WRITE_TOOLS = {
    "create_todo", "create_calendar_event", "update_inventory", "consume_inventory",
    "remove_todo", "set_timer", "add_inventory", "add_knowledge", "update_todo",
}

_TIME_RES = (
    re.compile(r"in\s+(\d+)\s+(min(uten?|ute)?s?|m|stunde?n?|h)\b", re.I),
    re.compile(r"um\s+(\d{1,2})[:.](\d{2})", re.I),
    re.compile(r"(\d{1,2})[:.](\d{2})\s*(uhr)?\b", re.I),
    re.compile(r"(über)?morgen\b", re.I),
    re.compile(r"heute\b", re.I),
    re.compile(r"\b(am\s+)?(montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\b", re.I),
)


def _iso(s: str) -> str | None:
    try:
        dt = datetime.fromisoformat(str(s))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ)
        return to_utc_iso(dt)
    except (ValueError, TypeError):
        return None


def _resolve(s: str) -> str:
    iso = _iso(s)
    if iso:
        return iso
    resolved = resolve_dt(str(s))
    return to_utc_iso(resolved) if resolved else str(s)


def _resolve_opt(s: str) -> str | None:
    if not s:
        return None
    iso = _iso(s)
    if iso:
        return iso
    resolved = resolve_dt(str(s))
    return to_utc_iso(resolved) if resolved else None


def _extract_due(title: str, prefer_due: str = "") -> tuple[str, str]:
    for pat in _TIME_RES:
        m = pat.search(title)
        if m:
            resolved = resolve_dt(m.group(0))
            if resolved:
                clean = title[:m.start()] + " " + title[m.end():]
                return " ".join(clean.split()).strip(" ,;"), to_utc_iso(resolved)
    due = _resolve(prefer_due) if prefer_due else ""
    if not due:
        resolved = resolve_dt(title)
        if resolved:
            due = to_utc_iso(resolved)
            title = ""
    return " ".join(title.split()).strip(" ,;"), due


def _f(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": props,
                                                            "required": required or []}}}


def _event_when(start_at, notes: str) -> str:
    from modules.timesync import format_local
    when = format_local(start_at.isoformat()) if start_at and hasattr(start_at, "isoformat") else ""
    return (when + (f" — {notes}" if notes else "")).strip()


class ToolCatalog:
    def __init__(self, store: PostgresStore, searcher=None):
        self.store = store
        self.searcher = searcher
        self.tools = self._build_tools()

    def names(self) -> list[str]:
        return [tool.__name__ for tool in self.tools]

    def schemas(self) -> list[dict]:
        return TOOL_SCHEMAS

    def execute(self, name: str, args: dict[str, Any]) -> Any:
        now_utc = datetime.now(timezone.utc)
        allowed = self._allowed_args(name)
        if allowed is not None:
            args = {k: v for k, v in (args or {}).items() if k in allowed}
        actions = {
            "list_upcoming": lambda: self._upcoming(args, now_utc),
            "search_records": lambda: self._search(args),
            "create_todo": lambda: self._create_todo(args),
            "create_calendar_event": lambda: self._create_event(args),
            "update_inventory": lambda: self.store.update_inventory(args.get("title", args.get("name", "")), args.get("quantity", 0)),
            "consume_inventory": lambda: self._consume(args),
            "remove_todo": lambda: self.store.delete_todo(args.get("title", "")),
            "update_todo": lambda: self._update_todo(args),
            "set_timer": lambda: self._set_timer(args, now_utc),
            "add_inventory": lambda: self.store.upsert_doc("inventory", args.get("name", ""), args.get("description", ""), args),
            "add_knowledge": lambda: self.store.upsert_doc("knowledge", args.get("title", ""), args.get("body", ""), args),
            "web_search": lambda: self._web(args),
            "list_facts": lambda: self.store.active_facts(20),
            "db_stats": lambda: self.store.stats(),
            "update_event": lambda: self.store.update_event(
                args.get("title", ""), start_at=args.get("start_at") or None,
                end_at=args.get("end_at") or None, location=args.get("location"),
                notes=args.get("notes"),
            ),
            "cancel_event": lambda: self.store.cancel_event(args.get("title", "")),
        }
        if name not in actions:
            raise ValueError(f"unknown tool: {name}")
        return actions[name]()

    @staticmethod
    def _allowed_args(name: str) -> set[str] | None:
        """Poka-yoke: unbekannte Phantom-Argumente (z. B. due_at bei add_inventory) fallen weg."""
        for schema in TOOL_SCHEMAS:
            if schema["function"]["name"] == name:
                return set(schema["function"]["parameters"].get("properties", {}))
        return None

    def _create_todo(self, args) -> int:
        title, due = _extract_due(args.get("title", ""), args.get("due_at") or "")
        return self.store.upsert_doc("todos", title or "Erinnerung", args.get("notes", ""),
                                     {"due_at": due, "notes": args.get("notes", "")})

    def _create_event(self, args) -> int:
        title_arg = args.get("title", "")
        start = _resolve(args.get("start_at") or "") or ""
        if not start:
            _, start = _extract_due(title_arg)
        end = _resolve_opt(args.get("end_at") or "") or None
        title, _ = _extract_due(title_arg, "")
        if end is not None and end <= start:
            end = None
        participants = args.get("participants") or args.get("participants_list") or []
        if isinstance(participants, str):
            participants = [p.strip() for p in str(participants).split(",") if p.strip()]
        dup = self.store.find_event(title or title_arg, start or None)
        if dup:
            return dup
        return self.store.add_event(
            title=title or title_arg, start_at=start or "morgen 09:00", end_at=end,
            urgency=args.get("urgency", "normal"), repeats=args.get("repeats", ""),
            notes=args.get("notes", ""), location=args.get("location", ""),
            participants=participants,
        )

    def _consume(self, args) -> dict:
        rid = self.store.update_inventory(args.get("title", args.get("name", "")), args.get("quantity", 1))
        return {"updated": bool(rid), "quantity": args.get("quantity", 1)}

    def _update_todo(self, args) -> dict:
        title, _ = _extract_due(args.get("title", ""), "")
        due = _resolve(args.get("due_at") or "") if args.get("due_at") else None
        rid = self.store.update_todo(args.get("title", ""), due_at=due, notes=args.get("notes"))
        return {"updated": bool(rid)}

    def _set_timer(self, args, now_utc) -> int:
        title_arg = args.get("title") or ""
        if args.get("in_min") is not None:
            mins = int(args["in_min"])
            title, _ = _extract_due(title_arg, "")
        else:
            m = re.search(r"in\s+(\d+)\s*(min(uten?|ute)?s?|m)\b", title_arg, re.I)
            mins = int(m.group(1)) if m else 10
            title, _ = _extract_due(title_arg)
        due = to_utc_iso(now_utc + timedelta(minutes=mins))
        return self.store.upsert_doc("todos", title or "Timer", "Timer",
                                     {"due_at": due, "meta": "timer"})

    def _upcoming(self, args, now_utc) -> list[dict[str, Any]]:
        area = args.get("area", "calendar_events")
        days = {"day": 1, "week": 7, "month": 30}.get(args.get("horizon", "week"), 7)
        end = now_utc + timedelta(days=days)
        items: list[dict[str, Any]] = []
        if area in ("calendar_events", "termine", "all"):
            for e in self.store.list_events(now_utc, end, limit=20):
                items.append({"title": f"[termin] {e['title']}", "at": e["start_at"],
                              "urgency": e.get("urgency"), "status": e.get("status")})
        if area in ("todos", "aufgaben", "all"):
            for t in self.store.upcoming("todos", now_utc, end, limit=20):
                items.append({"title": f"[todo] {t['title']}", "at": t["at"], "urgency": "normal"})
            for t in self._overdue_todos(now_utc):
                items.append({"title": f"[überfällig] {t['title']}", "at": t["at"], "urgency": "hoch"})
        items.sort(key=lambda i: i["at"])
        return items

    def _overdue_todos(self, now_utc) -> list[dict[str, Any]]:
        """Fällige, noch offene To-dos in der Vergangenheit — beantwortet 'warum keine Erinnerung?'."""
        from modules.postgres_store import _parse_dt
        with self.store.connect() as con:
            rows = con.execute(
                "select title, metadata->>'due_at' from todos where metadata ? 'due_at' "
                "order by id desc limit 60").fetchall()
        out = []
        for title, due in rows:
            at = _parse_dt(due or "")
            if at is not None and at < now_utc:
                out.append({"title": title, "at": at})
        out.sort(key=lambda i: i["at"])
        return out[:5]

    def _search(self, args) -> list[dict[str, Any]]:
        table, query = args.get("table", ""), args.get("query", "")
        if table not in ("todos", "calendar_events", "inventory", "knowledge"):
            return []
        if table == "calendar_events":
            with self.store.connect() as con:
                rows = con.execute(
                    "select title, start_at, notes from events where title ilike %s "
                    "order by start_at asc limit %s",
                    (f"%{query}%", args.get("limit", 8)),
                ).fetchall()
            return [{"title": t, "body": _event_when(s, n)} for t, s, n in rows]
        return self.store.search(table, query, args.get("limit", 8))

    def _web(self, args) -> Any:
        if self.searcher is None:
            return [{"title": "Websuche nicht verfügbar", "url": "", "content": ""}]
        hits = self.searcher.search(args.get("query", ""), max=args.get("max_results", 5))
        return [dict(hit, source="web") for hit in hits]

    def _build_tools(self):
        decorator = needle.tool if needle else (lambda fn: fn)

        @decorator
        def list_upcoming(area: str = "calendar_events", horizon: str = "week", limit: int = 10):
            """List upcoming calendar events and/or todos. Keywords: was steht an, termine, todos, agenda."""

        @decorator
        def search_records(table: str, query: str, limit: int = 8):
            """Search local data (todos, calendar_events, inventory, knowledge). Keywords: find, do we have, did you save, lookup."""

        @decorator
        def create_todo(title: str, due_at: str = "", notes: str = ""):
            """Create a todo or reminder with optional due time. Keywords: remind me, erinnerung, todo, task."""

        @decorator
        def create_calendar_event(title: str, start_at: str = "morgen 09:00", end_at: str = "",
                                  urgency: str = "normal", repeats: str = "", notes: str = "",
                                  location: str = "", participants: str = ""):
            """Create a calendar event/appointment. Keywords: termin, kalender, meeting, eintragen, schedule."""

        @decorator
        def update_inventory(title: str, quantity: int):
            """Set stock quantity of an existing inventory item. Keywords: nur noch, bestand, amount left."""

        @decorator
        def consume_inventory(title: str, quantity: int = 1):
            """Consume/use up quantity of an inventory item. Keywords: verbraucht, used up."""

        @decorator
        def remove_todo(title: str):
            """Delete a todo by title. Keywords: lösche, entferne, delete task."""

        @decorator
        def update_todo(title: str, due_at: str = "", notes: str = ""):
            """Change an existing todo (new time/notes). Keywords: ändere, verschiebe, reschedule."""

        @decorator
        def set_timer(title: str = "Timer", in_min: int = 10):
            """Set a timer that fires in in_min minutes. Keywords: timer, stelle einen timer."""

        @decorator
        def add_inventory(name: str, description: str = "", quantity: int = 0, location: str = ""):
            """Add a new inventory item. Keywords: füge hinzu, lege an, neues artikel, add to inventory."""

        @decorator
        def add_knowledge(title: str, body: str, source: str = "conversation"):
            """Save a note or fact for later. Keywords: merk dir, notiere, remember this fact."""

        @decorator
        def web_search(query: str, max_results: int = 5):
            """Web search via local SearXNG (only on explicit request). Keywords: suche im web, google."""

        @decorator
        def list_facts(limit: int = 20):
            """List learned facts about the user. Keywords: was weißt du, gelernt, facts."""

        @decorator
        def db_stats():
            """Show database status. Keywords: status, datenbank, stats."""

        return [list_upcoming, search_records, create_todo, create_calendar_event,
                update_inventory, consume_inventory, remove_todo, update_todo, set_timer,
                add_inventory, add_knowledge, web_search, list_facts, db_stats]


TOOL_SCHEMAS = [
    _f("list_upcoming", "Liste anstehender Termine/To-dos (auch 'list todos').",
       {"area": {"type": "string", "enum": ["calendar_events", "todos", "all"]},
        "horizon": {"type": "string", "enum": ["day", "week", "month"]},
        "limit": {"type": "integer"}}, ["area"]),
    _f("search_records", "Suche in lokalen Daten (todos, calendar_events=Termine, inventory, knowledge).",
       {"table": {"type": "string", "enum": ["todos", "calendar_events", "inventory", "knowledge"]},
        "query": {"type": "string"}, "limit": {"type": "integer"}}, ["table", "query"]),
    _f("create_todo", "Erstelle ein To-do/Erinnerung. due_at kann ISO sein oder relativ wie 'morgen 9'/'in 10 min'.",
       {"title": {"type": "string"}, "due_at": {"type": "string"}, "notes": {"type": "string"}}, ["title"]),
    _f("create_calendar_event", "Lege einen Kalendereintrag an.",
       {"title": {"type": "string"}, "start_at": {"type": "string"}, "end_at": {"type": "string"},
        "urgency": {"type": "string", "enum": ["niedrig", "normal", "hoch"]},
        "repeats": {"type": "string"}, "notes": {"type": "string"},
        "location": {"type": "string"}, "participants": {"type": "string"}}, ["title"]),
    _f("update_inventory", "Setze die Menge eines vorhandenen Inventar-Artikels ('nur noch 4 säcke grillkohle'). title=Artikelname ohne Menge/Verpackung (z. B. 'grillkohle').",
       {"title": {"type": "string"}, "quantity": {"type": "integer"}}, ["title", "quantity"]),
    _f("consume_inventory", "Verbrauche quantity Stück eines Inventar-Artikels. title=Artikelname (das Zählobjekt, z. B. bei '2 flaschen öl' -> 'öl', nicht 'flaschen').",
       {"title": {"type": "string"}, "quantity": {"type": "integer"}}, ["title"]),
    _f("remove_todo", "Lösche ein To-do anhand des Titels.", {"title": {"type": "string"}}, ["title"]),
    _f("update_todo", "Ändere ein bestehendes To-do (neue Zeit/due_at, Notiz). Bei Rückbezug ('das','es') den zuletzt genannten Titel verwenden.",
       {"title": {"type": "string"}, "due_at": {"type": "string"}, "notes": {"type": "string"}}, ["title"]),
    _f("set_timer", "Stelle eine Erinnerung in in_min Minuten.", {"title": {"type": "string"}, "in_min": {"type": "integer"}}, []),
    _f("add_inventory", "Lege einen neuen Inventar-Artikel an.",
       {"name": {"type": "string"}, "description": {"type": "string"},
        "quantity": {"type": "integer"}, "location": {"type": "string"}}, ["name"]),
    _f("add_knowledge", "Speichere Wissen/Notiz für später.",
       {"title": {"type": "string"}, "body": {"type": "string"}, "source": {"type": "string"}}, ["title", "body"]),
    _f("web_search", "Suche im Web über die lokale SearXNG-Instanz (nur bei explizitem Wunsch).",
       {"query": {"type": "string"}, "max_results": {"type": "integer"}}, ["query"]),
    _f("list_facts", "Liste gespeicherte Fakten über den Nutzer / gelerntes Wissen.",
       {"limit": {"type": "integer"}}, []),
    _f("db_stats", "Zeige Datenbank-Status.", {}),
]
