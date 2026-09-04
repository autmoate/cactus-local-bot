import json

from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table

HELP = """Befehle:
  /status            Status-Block (Tools, DB, Verhalten, letzte Ereignisse)
  /logs              Hintergrund-Logs ein-/ausschalten (anzeigen was im Hintergrund passiert)
  /graph             Entity-Graph als ASCII (Knoten & Kanten)
  /soul              Persona anzeigen (Standard-Scope)
  /soul <key>=<wert> Persona-Schlüssel setzen (z. B. tone=knapp, deutsch)
  /facts             gelernte Fakten
  /changes           Audit der Selbstpflege
  /memory            Speicher-Stand (Nachrichten, Fakten)
  /help              diese Liste
  quit               beenden

Tipp: frag natürlich ('was steht an?', 'erinnere mich in 10 min an …'),
@ vor einer Nachricht erzwingt Antwort/Aktion.
"""


def status(console, rt) -> None:
    state, store, catalog = rt.state, rt.store, rt.catalog
    stats = store.stats() if state.postgres_online else {"tables": {}, "vector_dim": "n/a"}
    table = Table(title="Cactus · Status", expand=False)
    table.add_column("Bereich")
    table.add_column("Wert")
    table.add_row("Route", state.route)
    table.add_row("Cactus/Gemma", "online" if state.cactus_online else "offline")
    table.add_row("Needle", "ready" if rt.verifier and rt.verifier.ready else "n/a")
    table.add_row("Postgres/pgvector", "online" if state.postgres_online else "offline")
    table.add_row("Web (SearXNG)", "an" if rt.web_enabled else "aus")
    table.add_row("Behavior", rt.behavior)
    table.add_row("Reasoning", rt.reasoning or "aus")
    table.add_row("Logs", "an" if rt.logs_on else "aus")
    table.add_row("Tools", ", ".join(catalog.names()))
    table.add_row("DB", json.dumps(stats, default=str))
    console.print(table)
    if state.events:
        console.print(Panel("\n".join(state.events[-8:]), title="Letzte Ereignisse"))


def run(prompt: str, console, rt) -> bool:
    cmd = prompt.strip().lower()
    if cmd in ("/help", "/hilfe"):
        console.print(Panel(HELP, title="Befehle"))
        return True
    if cmd == "/status":
        status(console, rt)
        return True
    if cmd == "/logs":
        rt.logs_on = not rt.logs_on
        console.print(f"[bold green]Logs:[/] {'an' if rt.logs_on else 'aus'}")
        return True
    if cmd == "/graph":
        g = rt.store.graph_dump()
        lines = [f"{n['id']}: {n['subject']}" for n in g["nodes"]]
        for e in g["edges"]:
            lines.append(f"{e['src']} -[{e['rel']}]-> {e['dst']}")
        console.print(Panel("\n".join(lines) or "(Graph leer)", title="Graph"))
        return True
    if cmd in ("/soul", "/persona") or (cmd + " ").startswith(("/soul ", "/persona ")):
        return _soul(cmd, console, rt)
    if cmd == "/facts":
        console.print(Panel(Pretty(rt.store.active_facts(20)), title="Fakten"))
        return True
    if cmd == "/changes":
        console.print(Panel(Pretty(rt.store.list_changes("default", 10)), title="Audit / Selbstpflege"))
        return True
    if cmd == "/memory":
        console.print(Panel(Pretty({"stats": rt.store.stats(), "recent": rt.store.recent_messages(5)}), title="Memory"))
        return True
    return False


def _soul(cmd, console, rt) -> bool:
    store = rt.store
    soul = dict(store.get_profile("default", "soul"))
    rest = cmd.strip().split(None, 1)
    if len(rest) == 2 and "=" in rest[1]:
        key, _, value = rest[1].partition("=")
        key = key.strip()
        soul[key] = value.strip()
        store.set_profile("default", "soul", soul, f"user set {key}")
        console.print(f"[bold green]Persona '{key}' gesetzt.[/]")
        return True
    console.print(Panel(Pretty(soul), title="Soul (default)"))
    return True
