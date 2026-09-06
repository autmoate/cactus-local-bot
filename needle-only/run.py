"""Needle-only v5.3 „CRUD": 7 Tools, CRUD-Semantik, Hard-Deletes.
Termin/Erinnerung/Aufgabe = EIN Event mit kind ∈ {appointment, reminder, task}.
Erinnerungen werden nach Feuern GELÖSCHT, abgesagte Termine GELÖSCHT,
vergangene Termine bleiben (Archiv).
Start: uv run python needle-only/run.py"""
import os
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import needle  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.prompt import Prompt  # noqa: E402

from modules.config import load_config  # noqa: E402
from modules.timesync import parse_calendar, resolve_dt, now as tz_now  # noqa: E402
from orga import Orga, _norm_dt  # noqa: E402
from tools import MiniTools  # noqa: E402

LOG_ON = True
WRITE = {"calendar_create", "calendar_edit", "calendar_delete",
         "note_write", "note_delete"}
_console = Console()


def _log(line: str) -> None:
    if LOG_ON:
        print(f"  · {line}")


def build():
    """Tools, Needle-Agent und Tool-Funktionen aufsetzen."""
    cfg = load_config()
    tools = MiniTools(Orga(cfg.database_url))
    fns = tools.build()
    agent = needle.Needle(tools=fns, system=f"current date: {tz_now().strftime('%Y-%m-%d %H:%M')}. locale: de-DE.")
    return tools, agent, fns


def allowed_args(name: str, fns: list) -> set:
    """Welche Parameter hat das Tool? (für Whitelist)"""
    import inspect
    for fn in fns:
        if fn.__name__ == name:
            return set(inspect.signature(fn).parameters)
    return set()


def resolve_flexible(text: str):
    """Flexible Zeit-Auflösung: 'in 10 min', 'morgen um 9', '14 uhr', etc."""
    resolved = resolve_dt(text)
    if resolved:
        return resolved
    tm = re.search(r"(\d{1,2})[:.](\d{2})\b", text) or re.search(r"\b(\d{1,2})\s*uhr\b", text)
    if tm:
        hour = int(tm.group(1))
        if not 0 <= hour <= 23:
            return None
        minute = int(tm.group(2)) if tm.lastindex == 2 else 0
        cand = tz_now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        return cand + __import__("datetime").timedelta(days=1) if cand < tz_now() else cand
    return None


def fix_args(text: str, name: str, args: dict, fns: list) -> dict:
    """Whitelist + Zeit-Auflösung + Kind-Default."""
    keep = allowed_args(name, fns)
    clean = {k: v for k, v in (args or {}).items() if keep is None or k in keep}

    # Zeit-Auflösung für start_at/end_at
    for key in ("start_at", "end_at"):
        if key in clean and clean[key]:
            resolved = _resolve_datetime(str(clean[key]), text)
            if resolved:
                clean[key] = resolved
            else:
                clean.pop(key, None)
        elif key in clean and not clean[key]:
            clean.pop(key, None)

    # kind-Handling: Default 'appointment' wenn leer/fehlt
    if name == "calendar_create":
        kind = str(clean.get("kind", "")).strip().lower()
        if not kind or kind not in ("appointment", "reminder", "task"):
            # Aus Text inferieren wenn needle kind nicht korrekt füllt
            text_lower = (text or "").lower()
            if any(w in text_lower for w in ("erinnerung", "erinnere", "erinner", "reminder", "timer")):
                kind = "reminder"
            elif any(w in text_lower for w in ("aufgabe", "task", "todo", "deadline")):
                kind = "task"
            else:
                kind = "appointment"
        clean["kind"] = kind

    # calendar_read: kind aus Text inferieren wenn 'all'
    if name == "calendar_read":
        kind = str(clean.get("kind", "all")).strip().lower()
        if kind in ("all", "", "alle"):
            text_lower = (text or "").lower()
            if any(w in text_lower for w in ("erinnerung", "erinner", "reminder")):
                clean["kind"] = "reminder"
            elif any(w in text_lower for w in ("aufgabe", "task", "todo")):
                clean["kind"] = "task"
            elif any(w in text_lower for w in ("termin", "appointment", "meeting")):
                clean["kind"] = "appointment"

    return clean


def _resolve_datetime(value: str, context: str = "") -> datetime | None:
    """Versucht, einen String in ein datetime zu übersetzen."""
    # Direkter ISO-String?
    dt = _norm_dt(value)
    if dt:
        return dt
    # Relative Angaben wie "in 10 min" oder "morgen 14:00"?
    return resolve_flexible(value)


def draft_calls(agent, fns, text: str, lang: str = "de") -> list:
    """Needle-Draft → direkt ausführen (write) oder lesen (read)."""
    if lang == "en":
        from translate import de2en
        text, _ = de2en(text)

    agent.reset()
    resp = agent.complete(text)
    calls = resp.get("function_calls") or []

    # Wenn nur 1 Call und der Text "und" enthält → Retry für Multi-Op
    if len(calls) < 2 and re.search(r"\bund\b", text):
        agent.reset()
        retry = agent.complete(text)
        retry_calls = retry.get("function_calls") or []
        seen = {(c.get("name"), str((c.get("arguments") or {}).get("title", "")).lower())
                for c in calls}
        for c in retry_calls:
            key = (c.get("name"), str((c.get("arguments") or {}).get("title", "")).lower())
            if key not in seen:
                calls.append(c)
                seen.add(key)

    fixed = []
    for call in calls[:3]:  # Max 3 Ops pro Turn
        name = call.get("name")
        args = call.get("arguments") or {}
        fixed.append({"tool": name, "arguments": fix_args(text, name, args, fns)})
    return fixed


def process(text: str, tools: MiniTools, agent, fns: list, lang: str = "de") -> str:
    """Haupt-Turn: Needle → fix → execute/read."""
    calls = draft_calls(agent, fns, text, lang)

    if not calls:
        return ""

    reads, writes = [], []
    for call in calls:
        name = call["tool"]
        args = call["arguments"]
        _log(f"needle -> {name}({args})")

        if name in WRITE:
            writes.append(call)
        else:
            # Read-Tool direkt ausführen
            result = tools.execute(name, args, text)
            reads.append(result)

    if writes:
        # Write-Tools direkt ausführen (kein Approval-Flow mehr)
        results = []
        for w in writes:
            result = tools._execute_write(w["tool"], w["arguments"])
            results.append(result)
        return "\n".join(results)

    return "\n".join(r for r in reads if r)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Needle-only Orga-Bot (TUI)")
    ap.add_argument("--ics-export", action="store_true", default=False,
                    help="ICS-Export-Server starten (default: aus, security by design)")
    args = ap.parse_args()

    tools, agent, fns = build()

    # ICS-Server nur mit explizitem --ics-export Flag aktivierbar
    ics_server = None
    if args.ics_export:
        from serve import create_server
        ics_server = create_server(tools.orga)
        ics_thread = threading.Thread(target=ics_server.serve_forever,
                                       daemon=True, name="orga-ics")
        ics_thread.start()
        _console.print(f"[dim]ICS-Server: http://0.0.0.0:{ics_server.server_port}"
                       f"/ics/{ics_server.token}.ics[/]")

    from scheduler import Scheduler
    sched = Scheduler(tools.orga,
                      notify=lambda msg: _console.print(f"[bold yellow]{msg}[/]"))
    sched.start()

    _console.print("[bold cyan]needle-only v5.3[/] — CRUD · /status · /quit")
    while True:
        try:
            text = _console.input("[bold yellow]du: [/]").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in ("/quit", "quit", "q"):
            break
        if text.lower() == "/status":
            _console.print(tools.orga.calendar_read(horizon="monat"))
            continue
        try:
            out = process(text, tools, agent, fns)
            if out:
                _console.print(f"[bold cyan]orga[/] · {out}")
        except Exception as exc:
            _console.print(f"[bold red]fehler:[/] {exc}")


if __name__ == "__main__":
    main()
