import json
from dataclasses import dataclass
from threading import Thread
from time import sleep

from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

from modules.anim import Activity
from modules.approval import ask_approval
from modules.cactus_engine import CactusEngine
from modules.config import load_config
from modules.embeddings import EmbeddingClient
from modules.interpreter import GemmaInterpreter
from modules.memory import Memory
from modules.needle_router import NeedleRouter
from modules.needle_verifier import NeedleVerifier
from modules.pipeline import process as run_pipeline
from modules.postgres_store import PostgresStore
from modules.reflect import Reflect
from modules.scheduler import Scheduler
from modules.shell_cmds import run as run_command
from modules.state import AppState
from modules.tool_catalog import ToolCatalog
from modules.websearch import WebSearch

U = "[bold yellow]"
C = "[bold cyan]"
R = "[bold magenta]"
ERR = "[bold red]"
LOG = "[dim]·[/]"
DIM = "[dim]"


@dataclass
class Runtime:
    state: AppState
    router: NeedleRouter
    cactus: CactusEngine
    catalog: ToolCatalog
    store: PostgresStore
    memory: Memory
    scheduler: Scheduler
    threshold: float
    behavior: str
    reasoning: str | None
    web_enabled: bool = False
    reflect: Reflect | None = None
    interpreter: GemmaInterpreter | None = None
    verifier: NeedleVerifier | None = None
    logs_on: bool = True
    trigger: str = "@"
    proactive_gemma: bool = True
    retention_hours: int = 24
    followup: bool = False


_ACTIONS = (
    ("create_calendar_event", "trage in den Kalender ein …"),
    ("create_todo", "merke vor …"),
    ("set_timer", "stelle Timer …"),
    ("update_todo", "aktualisiere To-do …"),
    ("update_inventory", "aktualisiere Inventar …"),
    ("consume_inventory", "buche Bestand ab …"),
    ("add_inventory", "erfasse Bestand …"),
    ("add_knowledge", "speichere Notiz …"),
    ("web_search", "suche im Web …"),
    ("search_records", "durchsuche Daten …"),
    ("list_upcoming", "schaue in den Kalender …"),
    ("list_facts", "schaue in Fakten …"),
    ("db_stats", "prüfe Datenbank …"),
    ("verifier", "gegenprüfe …"),
    ("follow-up", "prüfe auf mehr …"),
    ("triage", "verstehe …"),
)


def _action_for(line: str) -> str:
    for key, label in _ACTIONS:
        if key in line:
            return label
    return "denke nach …"


def banner(console: Console, rt: Runtime) -> None:
    st = rt.state
    ok = lambda b: "ok" if b else "off"
    console.print(C + "cactus[/] — lokaler Orga-Helfer  "
                 + f"{ok(st.cactus_online)} cactus · {ok(st.postgres_online)} postgres · "
                 + f"{ok(rt.web_enabled)} web")
    console.print("[dim]frag natürlich ('was steht an?', 'erinnere mich in 10 min an …'). /hilfe · /status · /logs[/]")


def _briefing(rt: Runtime) -> str | None:
    try:
        import datetime as _dt
        from modules.timesync import today_iso
        store = rt.store
        now = _dt.datetime.now(_dt.timezone.utc)
        n_todos = len(store.upcoming("todos", now, now + _dt.timedelta(days=7)))
        n_ev = len(store.list_events(now, now + _dt.timedelta(days=7)))
        stats = store.stats()
        return (f"Briefing · {today_iso()} · {n_todos} offene To-dos, {n_ev} Termine diese Woche · "
                f"Inventar {stats['tables'].get('inventory', 0)}, Wissen {stats['tables'].get('knowledge', 0)}, "
                f"{stats['tables'].get('facts', 0)} Fakten")
    except Exception:
        return None


def _ask_confirmation(console: Console, best: dict) -> bool:
    name = best.get("name") or best.get("tool")
    args = best.get("arguments") or {}
    from modules.needle_router import Proposal
    proposal = Proposal(name, args, best.get("confidence"), best.get("reasoning", ""), {})
    return ask_approval(console, proposal)


_REMINDER_STOP = ("bedeutet", "heißt", "heisst", "definiert", "dass", "wegen",
                  "organisiert", "ist ein", "ist eine", "dient", "meint")


def _reminder_line(rt: Runtime, item: dict) -> str:
    title = str(item["title"] or "").strip()
    when = rt.scheduler.due_when_local(item["at"])
    if rt.proactive_gemma and rt.cactus.online() and title:
        try:
            text = rt.cactus.complete(
                f"Nenne dem Nutzer in EINEM kurzen Satz (max. 15 Wörter), dass '{title}' jetzt fällig ist ({when}). "
                f"Erkläre NICHTS, definiere nichts, füge nichts hinzu. Verwende den Titel wörtlich.",
                temperature=0.3,
            )
            low = text.strip().strip('"').lower()
            clean = (not text.startswith("Gemma/Cactus unavailable")
                     and title.lower() in low
                     and len(text) <= 140
                     and not any(m in low for m in _REMINDER_STOP))
            if clean:
                return text.strip().strip('"').removeprefix("Erinnerung:").strip()
        except Exception:
            pass
    return f"{title} ({when})"


def handle(prompt: str, console: Console, rt: Runtime) -> None:
    if prompt.startswith("/"):
        run_command(prompt, console, rt)
        return
    console.print(f"{U}du:[/] {escape(prompt)}")
    logs: list[str] = []
    activity = Activity(console)

    def log(line: str) -> None:
        logs.append(line)
        activity.set(_action_for(line))

    def confirm(best: dict) -> bool:
        activity.stop()
        ok = _ask_confirmation(console, best)
        activity.start("warte auf Freigabe …")
        return ok

    box: dict = {}

    def work() -> None:
        try:
            box["out"] = run_pipeline(prompt, rt, log, confirm, rt.trigger)
        except Exception as exc:
            box["err"] = exc

    activity.start()
    worker = Thread(target=work, daemon=True)
    worker.start()
    worker.join()
    activity.stop()
    if rt.logs_on:
        for line in logs[-4:]:
            console.print(f"{LOG} {line}")
    if "err" in box:
        rt.state.set("error", str(box["err"]))
        console.print(f"{ERR}fehler:[/] {box['err']}")
        return
    out = box.get("out", {"decision": "done", "text": ""})
    if out.get("decision") == "silent":
        rt.state.set("silent", "kein Handlungsbedarf")
        rt.memory.record_turn(prompt, "silent")
        return
    decision = out.get("decision", "done")
    text = out.get("text", "")
    rt.state.set("needle_executed", decision)
    rt.memory.record_turn(prompt, decision, reply=text or None)
    if text:
        console.print(text)


def main() -> None:
    console = Console()
    cfg = load_config()
    state = AppState()
    embed = EmbeddingClient(cfg.cactus_base_url, model=cfg.embed_model or None)
    store = PostgresStore(cfg.database_url, embed=embed, dim=cfg.embed_dim)
    cactus = CactusEngine(cfg.cactus_base_url)
    state.cactus_online = cactus.online()
    if cfg.cactus_auto_start and not state.cactus_online:
        console.print("[dim]warte auf Cactus-Server …[/]")
        for _ in range(6):
            sleep(5)
            if cactus.online():
                state.cactus_online = True
                break
    try:
        store.init()
        store._ensure_dim()
        state.postgres_online = store.ping()
    except Exception as exc:
        state.last_error = str(exc)
    searcher = WebSearch(cfg.searxng_url) if cfg.web_enabled else None
    web_on = bool(searcher and searcher.available())
    catalog = ToolCatalog(store, searcher=searcher if web_on else None)
    router = NeedleRouter(catalog.tools, cfg.confidence_threshold, cfg.tool_index_path)
    router.seed()
    interpreter = GemmaInterpreter(cactus).bind(catalog.schemas())
    verifier = NeedleVerifier(catalog.tools, cfg.tool_index_path,
                              system="Strukturiere die Anfrage als Tool-Call.")
    memory = Memory(store)
    scheduler = Scheduler(store, cfg.sensors_on, cfg.reminder_lookahead_min)
    state.needle_ready = router.ready
    reasoning = cfg.reasoning_level if cfg.reasoning_level not in ("", "none") else None
    rt = Runtime(state, router, cactus, catalog, store, memory, scheduler,
                 cfg.confidence_threshold, cfg.behavior, reasoning, web_on,
                 Reflect(store), interpreter, verifier, True)
    activation = store.get_profile("default", "soul").get("activation")
    rt.trigger = activation or cfg.trigger
    rt.proactive_gemma = cfg.proactive_gemma
    rt.retention_hours = cfg.msg_retention_hours
    note = scheduler.run_maintenance(rt.retention_hours) if scheduler.maintenance_due() else None
    banner(console, rt)
    b = _briefing(rt)
    if b:
        console.print(f"{DIM}{b}[/]")
    if note and rt.logs_on:
        console.print(f"{LOG} {note}")
    while True:
        if scheduler.maintenance_due():
            mnote = scheduler.run_maintenance(rt.retention_hours)
            if mnote and rt.logs_on:
                console.print(f"{LOG} {mnote}")
        items = scheduler.check()
        for item in items:
            text = _reminder_line(rt, item)
            state.set("proactive_reminder", text)
            console.print(f"{R}erinnerung:[/] {text}")
        text = Prompt.ask("request", console=console).strip()
        if text.lower() in {"q", "quit", "exit"}:
            break
        if not text:
            continue
        try:
            handle(text, console, rt)
        except Exception as exc:
            state.set("error", str(exc))
            console.print(f"{ERR}fehler:[/] {exc}")


if __name__ == "__main__":
    main()
