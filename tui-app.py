import json

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from modules.approval import ask_approval
from modules.cactus_engine import CactusEngine
from modules.config import load_config
from modules.needle_router import NeedleRouter
from modules.postgres_store import PostgresStore
from modules.state import AppState
from modules.tool_catalog import ToolCatalog


def render(console: Console, state: AppState, catalog: ToolCatalog, store: PostgresStore, threshold: float) -> None:
    stats = store.stats() if state.postgres_online else {"tables": {}, "vector_dim": "n/a"}
    table = Table(title="Cactus Compute Prototype")
    table.add_column("Area")
    table.add_column("State")
    table.add_row("Route", state.route)
    table.add_row("Cactus/Gemma", "online" if state.cactus_online else "offline")
    table.add_row("Needle", "ready" if state.needle_ready else "fallback heuristic")
    table.add_row("Postgres/pgvector", "online" if state.postgres_online else "offline")
    table.add_row("Confidence", str(threshold))
    table.add_row("Tools", ", ".join(catalog.names()))
    table.add_row("DB", json.dumps(stats, default=str))
    console.print(table)
    if state.events:
        console.print(Panel("\n".join(state.events), title="Recent Events"))


def handle(prompt: str, console: Console, app) -> None:
    state, router, catalog, cactus = app
    proposal = router.propose(prompt)
    if not router.should_execute(proposal):
        state.set("gemma_escalated", proposal.reasoning or "no approved Needle call")
        console.print(Panel(cactus.complete(prompt), title="Gemma"))
        return
    state.set("needle_proposed", proposal.tool)
    if not ask_approval(console, proposal):
        state.set("needle_rejected", proposal.tool)
        console.print(Panel(cactus.complete(prompt, proposal.raw), title="Gemma After Rejection"))
        return
    state.set("user_approved", proposal.tool)
    result = catalog.execute(proposal.tool, proposal.arguments)
    state.set("needle_executed", proposal.tool)
    console.print(Panel(json.dumps(result, indent=2, default=str), title="Tool Result"))


def main() -> None:
    console = Console()
    cfg = load_config()
    state = AppState()
    store = PostgresStore(cfg.database_url)
    cactus = CactusEngine(cfg.cactus_base_url)
    try:
        store.init()
        state.postgres_online = store.ping()
    except Exception as exc:
        state.last_error = str(exc)
    catalog = ToolCatalog(store)
    router = NeedleRouter(catalog.tools, cfg.confidence_threshold, cfg.tool_index_path)
    state.needle_ready = router.ready
    state.cactus_online = cactus.online()
    console.print(Panel("Type a request, or 'quit'. Tool calls require approval.", title="Local TUI"))
    while True:
        render(console, state, catalog, store, cfg.confidence_threshold)
        text = Prompt.ask("request").strip()
        if text.lower() in {"q", "quit", "exit"}:
            break
        if not text:
            continue
        try:
            handle(text, console, (state, router, catalog, cactus))
        except Exception as exc:
            state.set("error", str(exc))
            console.print(Panel(str(exc), title="Error"))


if __name__ == "__main__":
    main()
