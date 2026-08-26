from typing import Any, Literal

try:
    import needle
except Exception:  # pragma: no cover - keeps app diagnosable before uv sync succeeds
    needle = None

from modules.postgres_store import PostgresStore


class ToolCatalog:
    def __init__(self, store: PostgresStore):
        self.store = store
        self.tools = self._build_tools()

    def names(self) -> list[str]:
        return [tool.__name__ for tool in self.tools]

    def execute(self, name: str, args: dict[str, Any]) -> Any:
        actions = {
            "add_inventory": lambda: self.store.upsert_doc("inventory", args["name"], args["description"], args),
            "create_todo": lambda: self.store.upsert_doc("todos", args["title"], args.get("notes", ""), args),
            "add_calendar_event": lambda: self.store.upsert_doc("calendar_events", args["title"], args.get("notes", ""), args),
            "add_knowledge": lambda: self.store.upsert_doc("knowledge", args["title"], args["body"], args),
            "search_records": lambda: self.store.search(args["table"], args["query"], args.get("limit", 5)),
            "db_stats": lambda: self.store.stats(),
        }
        if name not in actions:
            raise ValueError(f"unknown tool: {name}")
        return actions[name]()

    def _build_tools(self):
        decorator = needle.tool if needle else (lambda fn: fn)

        @decorator
        def add_inventory(name: str, description: str, location: str = ""):
            """Add an inventory item.

            Args:
                name: short item name
                description: searchable description of the item
                location: where the item is stored
            """

        @decorator
        def create_todo(title: str, notes: str = "", due_at: str = ""):
            """Create a todo item.

            Args:
                title: concrete task title
                notes: optional task details
                due_at: due date or time if stated by the user
            """

        @decorator
        def add_calendar_event(title: str, starts_at: str, ends_at: str = "", notes: str = ""):
            """Add a calendar event.

            Args:
                title: event title
                starts_at: event start date or time
                ends_at: event end date or time
                notes: optional event details
            """

        @decorator
        def add_knowledge(title: str, body: str, source: str = "manual"):
            """Store knowledge for later retrieval.

            Args:
                title: short title for this knowledge item
                body: full content to store
                source: source label for provenance
            """

        @decorator
        def search_records(
            table: Literal["inventory", "todos", "calendar_events", "knowledge"], query: str, limit: int = 5
        ):
            """Search stored records with vector similarity.

            Args:
                table: database area to search
                query: natural language search query
                limit: maximum number of results
            """

        @decorator
        def db_stats():
            """Show database table counts and vector index shape."""

        return [add_inventory, create_todo, add_calendar_event, add_knowledge, search_records, db_stats]
