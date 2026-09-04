from datetime import datetime, timedelta, timezone

from modules.timesync import format_local


class Scheduler:
    def __init__(self, store, enabled: bool, lookahead_min: int):
        self.store = store
        self.enabled = enabled
        self.lookahead_min = lookahead_min
        self._announced: set[tuple[str, int]] = set()
        self._last_maintenance: datetime | None = None

    def check(self) -> list[dict]:
        """Liefert fällige Einträge (jeder genau einmal über: _announced)."""
        if not self.enabled:
            return []
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(minutes=self.lookahead_min)
        due: list[dict] = []
        for item in self.store.due_items("todos", now, horizon):
            key = ("todo", item["id"])
            if key not in self._announced:
                self._announced.add(key)
                item["kind"] = "todo"
                due.append(item)
        for ev in self.store.list_events(now, horizon, limit=10):
            key = ("termin", ev["id"])
            if key not in self._announced:
                self._announced.add(key)
                due.append({"id": ev["id"], "kind": "termin", "title": ev["title"], "at": ev["start_at"]})
        due.sort(key=lambda i: i["at"])
        return due

    def due_when_local(self, at) -> str:
        try:
            return format_local(at.isoformat() if hasattr(at, "isoformat") else str(at))
        except Exception:
            return str(at)

    def maintenance_due(self) -> bool:
        if self._last_maintenance is None:
            return True
        return datetime.now(timezone.utc) - self._last_maintenance > timedelta(hours=22)

    def run_maintenance(self, retention_hours: int = 24) -> str | None:
        self._last_maintenance = datetime.now(timezone.utc)
        removed = self.store.prune_stale_facts(older_than_days=30)
        purged = self.store.purge_old_messages(retention_hours)
        note = f"wartung: {removed} veraltete Fakten, {purged} Roh-Chats entfernt"
        self.store.log_change("default", "maintenance", note, None,
                              {"removed": removed, "purged": purged})
        return note if (removed or purged) else None
