"""Needle-only v4.0 „Merge": entries-DB (eine Tabelle), Tool-Oberfläche v3.1.
Needle extrahiert Ops → Planner normalisiert (add⟷move, Endzustand-Kollision,
Fuzzy-Titel via pg_trgm) → EIN Approval pro Turn (y/n/e/q) → atomare Ausführung
mit Audit-Trail. `e` = Freitext-Korrektur → neue Needle-Runde.
Alles in LOCAL-Zeit gerendert. Kein serve, keine Embeddings.
Start: uv run python needle-only/run.py"""
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import needle  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.prompt import Prompt  # noqa: E402

from modules.config import load_config  # noqa: E402
from modules.timesync import parse_calendar, resolve_dt  # noqa: E402
from orga import Orga, _norm_dt  # noqa: E402
from tools import MiniTools  # noqa: E402

LOG_ON = True
WRITE = {"upsert_event", "cancel_event", "upsert_reminder",
         "complete_reminder", "remember_note"}
_console = Console()


def _log(line: str) -> None:
    if LOG_ON:
        print(f"  · {line}")


def build():
    """Tools, Needle-Agent und Tool-Funktionen aufsetzen."""
    cfg = load_config()
    tools = MiniTools(Orga(cfg.database_url))
    fns = tools.build()
    index = os.environ.get("NEEDLE_TOOL_INDEX", ".cache/needle-index-v3")
    system = (f"current date: {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
              "locale: de-DE. tool calls must be short and literal.")
    agent = needle.Needle(tools=fns, tool_index_path=index, system=system)
    return tools, agent, fns


def allowed_args(name: str, fns: list) -> set | None:
    """Welche Parameter hat das Tool? (für Whitelist)"""
    import inspect
    for fn in fns:
        if fn.__name__ == name:
            return set(inspect.signature(fn).parameters)
    return None


def resolve_flexible(text: str):
    """Flexible Zeit-Auflösung: 'in 10 min', 'morgen um 9', '14 uhr', etc."""
    from modules.timesync import now as tz_now
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


def _segment(text: str, args: dict) -> str:
    """Multi-Op-Attribution: der Text-Ausschnitt, der zum Titel dieses Ops passt."""
    low = (text or "").lower()
    title = str((args or {}).get("title") or "").strip().lower()
    if not title:
        return low
    for part in re.split(r"\s*(?:,\s*|\bund\b|;)\s*", low):
        if title in part or (len(title) >= 5 and part in title):
            return part
    return low


def fix_args(text: str, name: str, args: dict, fns: list) -> dict:
    """Whitelist + Zeit-Auflösung (Segment zuerst, dann Gesamtsatz)."""
    keep = allowed_args(name, fns)
    clean = {k: v for k, v in (args or {}).items() if keep is None or k in keep}
    seg = _segment(text, args)
    pc = parse_calendar(seg)
    resolved = _norm_dt(pc["iso"]) if pc and pc.get("iso") else None
    if resolved is None:
        resolved = resolve_flexible(seg)
    for key in ("start_at", "due_at"):
        if key in clean:
            if resolved is not None:
                clean[key] = resolved
            else:
                alt = resolve_flexible(str(clean.get(key) or ""))
                if alt is not None:
                    clean[key] = alt
                else:
                    clean.pop(key)
    # Relative Verschiebung: "um 30 min nach hinten"
    if name == "upsert_event" and "start_at" not in clean:
        m = re.search(r"\bum\s+(\d+)\s*(min(ute)?n?|m|stunden?|hours?|h)\b", seg)
        if m and re.search(r"nach hinten|später|früher|vorher|\bvor\b", seg):
            n = int(m.group(1))
            mins = n if m.group(2).startswith(("min", "m")) else n * 60
            clean["shift_min"] = -mins if re.search(r"früher|vorher", seg) else mins
    return clean


def draft_calls(agent, fns, text: str, lang: str = "de") -> list[dict]:
    """Needle-Draft (mit Multi-Op-Retry)."""
    if lang == "en":
        from translate import de2en
        text, _ = de2en(text)
    agent.reset()
    resp = agent.complete(text)
    calls = resp.get("function_calls") or []
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
    for call in calls[:3]:
        name = call.get("name")
        args = call.get("arguments") or {}
        # Intent-Korrektur: "lösche X" fälschlich zu upsert_event geroutet
        if name == "upsert_event" and re.search(
                r"\b(lösche|lösch|delete|entferne|streiche|loesche)\b", text.lower()):
            title = str(args.get("title") or "").strip()
            if title:
                name, args = "cancel_event", {"title": title}
        fixed.append({"tool": name, "arguments": fix_args(text, name, args, fns)})
    return fixed


def process(text: str, tools: MiniTools, agent, fns: list, lang: str = "de") -> str:
    """Haupt-Turn: Needle → fix → read/plan → approval → apply."""
    calls = draft_calls(agent, fns, text)
    if not calls:
        _log("needle: kein Call")
        return ""
    reads, writes = [], []
    for call in calls:
        name, args = call["tool"], call["arguments"]
        _log(f"needle -> {name}({args})")
        if name in WRITE:
            writes.append(call)
        else:
            reads.append(tools.execute(name, args, text))
    if writes:
        return _plan_flow(tools, agent, fns, writes, text)
    return "\n".join(r for r in reads if r)


def _plan_flow(tools: MiniTools, agent, fns, writes: list[dict], text: str) -> str:
    """EIN Approval für den ganzen Plan; e = Freitext-Korrektur."""
    plan = tools.plan(writes, text)
    if not plan["ops"]:
        return "\n".join(plan["lines"]) or "Kein Plan erzeugt."
    panel_lines = plan["lines"] + [f"⚠ {w}" for w in plan.get("warn", [])]
    _console.print(Panel("\n".join(panel_lines),
                         title="Plan · y=ausführen · n=nein · e=Korrektur",
                         border_style="yellow"))
    answer = Prompt.ask("Ausführen?", default="n", choices=["y", "n", "e"],
                        show_choices=False, console=_console).strip().lower()
    if answer == "y":
        return tools.apply(plan)
    if answer == "e" and len(text) < 500:
        fix = Prompt.ask("Korrektur (Freitext):", console=_console).strip()
        if fix:
            return process(fix, tools, agent, fns)
    return "Ok, nichts gespeichert."


def main() -> None:
    tools, agent, fns = build()
    # v4.2: Scheduler starten (Reminder-Firing + Appointment-Alarme)
    from scheduler import Scheduler
    sched = Scheduler(tools.orga,
                      notify=lambda msg: _console.print(f"[bold yellow]{msg}[/]"))
    sched.start()
    _console.print("[bold cyan]needle-only v4.1[/] — Merge + Scheduler · /status · /quit")
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
            _console.print(tools.orga.status())
            continue
        try:
            out = process(text, tools, agent, fns)
            if out:
                _console.print(f"[bold cyan]orga[/] · {out}")
        except Exception as exc:
            _console.print(f"[bold red]fehler:[/] {exc}")
    sched.stop()


if __name__ == "__main__":
    main()
