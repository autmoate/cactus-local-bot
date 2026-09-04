"""Needle-only v3.1 „Plan-Werkstatt": Needle extrahiert Ops → Planner normalisiert
(add⟷move, Reihenfolge, Endzustand-Kollision, Fuzzy-Titel) → EIN Approval pro Turn
(y/n/e/q) → atomare Ausführung. `e` = Freitext-Korrektur → neue Needle-Runde.
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
WRITE = {"upsert_event", "cancel_event", "upsert_reminder", "complete_reminder", "remember_note"}
_console = Console()


def _log(line: str) -> None:
    if LOG_ON:
        print(f"  · {line}")


def build():
    cfg = load_config()
    tools = MiniTools(Orga(cfg.database_url))
    fns = tools.build()
    index = os.environ.get("NEEDLE_TOOL_INDEX", ".cache/needle-index-v3")
    system = (f"current date: {datetime.now().strftime('%Y-%m-%d %H:%M')}. locale: de-DE. "
              "tool calls must be short and literal.")
    agent = needle.Needle(tools=fns, tool_index_path=index, system=system)
    return tools, agent, fns


def allowed_args(name: str, fns: list) -> set[str] | None:
    import inspect
    for fn in fns:
        if fn.__name__ == name:
            return set(inspect.signature(fn).parameters)
    return None


def resolve_flexible(text: str):
    from modules.timesync import now as tz_now
    resolved = resolve_dt(text)
    if resolved:
        return resolved
    tm = re.search(r"(\d{1,2})[:.](\d{2})\b", text) or re.search(r"\b(\d{1,2})\s*uhr\b", text)
    if tm:
        hour = int(tm.group(1))
        if not 0 <= hour <= 23:
            return None
        import datetime as _dt
        from modules.timesync import now as tz_now
        cand = tz_now().replace(hour=hour,
                                minute=int(tm.group(2)) if tm.lastindex == 2 else 0,
                                second=0, microsecond=0)
        return cand + _dt.timedelta(days=1) if cand < tz_now() else cand
    return None


def _normalized_text(text: str) -> str:
    low = (text or "").lower()
    for en, de in (("day after tomorrow", "übermorgen"), ("next week", "kommende woche"),
                   ("monday", "montag"), ("tuesday", "dienstag"), ("wednesday", "mittwoch"),
                   ("thursday", "donnerstag"), ("friday", "freitag"), ("saturday", "samstag"),
                   ("sunday", "sonntag"), ("tomorrow", "morgen"), ("today", "heute")):
        low = low.replace(en, de)
    return low


def _segment(text: str, args: dict) -> str:
    """Multi-Op-Attribution: der Satz-Teil, der zum Titel des Calls passt."""
    low = _normalized_text(text)
    title = str((args or {}).get("title") or "").strip().lower()
    if not title:
        return low
    for part in re.split(r"\s*(?:,\s*|\bund\b|;)\s*", low):
        if title in part or (len(title) >= 5 and part in title):
            return part
    return low


def fix_args(text: str, name: str, args: dict, fns: list) -> dict:
    """Whitelist + Zeitauflösung (Segment des Titels zuerst, dann Gesamtsatz)."""
    keep = allowed_args(name, fns)
    clean = {k: v for k, v in (args or {}).items() if keep is None or k in keep}
    low = _segment(text, args)
    pc = parse_calendar(low)
    resolved = _norm_dt(pc["iso"]) if pc.get("iso") else None
    if resolved is None:
        resolved = resolve_flexible(low)
    for key in ("due_at", "start_at"):
        if key in clean:
            if resolved is not None:
                clean[key] = resolved
            else:
                alt = resolve_flexible(str(clean.get(key) or ""))
                if alt is not None:
                    clean[key] = alt
                else:
                    clean.pop(key)
    if name == "upsert_reminder" and clean.get("due_at") is None:
        m = re.search(r"in\s+(\d+)\s*(min(ute)?n?|m|stunden?|hours?|h)\b", low)
        if m:
            n = int(m.group(1))
            clean["in_min"] = n if m.group(2).startswith(("min", "m")) else n * 60
    if name == "upsert_event":
        m_rel = re.search(r"\bum\s+(\d+)\s*(min(ute)?n?|stunden?)\b", low)
        if m_rel and re.search(r"nach hinten|später|früher|vorher|\bvor\b", low):
            n = int(m_rel.group(1)) * (1 if m_rel.group(2).startswith("min") else 60)
            clean["shift_min"] = -n if re.search(r"früher|vorher", low) else n
            clean.pop("start_at", None)
        elif "start_at" not in clean:
            m = re.search(r"\bum\s+(\d+)\s*(min(ute)?n?|stunden?)\b", low)
            if m:
                n = int(m.group(1)) * (1 if m.group(2).startswith("min") else 60)
                clean["shift_min"] = -n if re.search(r"früher|vorher", low) else n
    return clean


def draft_calls(agent, fns, text: str, lang: str = "de") -> list[dict]:
    """Needle-Draft; bei Multi-Op-Sätzen ('und', <2 Calls) ein zweiter Sample-Versuch."""
    prompt = text
    if lang == "en":
        from translate import de2en
        prompt, translated = de2en(text)
        if translated:
            _log(f"en: {prompt}")
    agent.reset()
    response = agent.complete(prompt)
    calls = response.get("function_calls") or []
    if len(calls) < 2 and re.search(r"\bund\b", prompt or ""):
        agent.reset()
        retry = agent.complete(prompt).get("function_calls") or []
        seen = {(c.get("name"), str((c.get("arguments") or {}).get("title", "")).lower()) for c in calls}
        for c in retry:
            key = (c.get("name"), str((c.get("arguments") or {}).get("title", "")).lower())
            if key not in seen:
                calls.append(c)
                seen.add(key)
    fixed = []
    for call in calls[:2]:
        fixed.append({"tool": call.get("name"),
                      "arguments": fix_args(text, call.get("name"), call.get("arguments") or {}, fns)})
    return fixed


def process(text: str, tools: MiniTools, agent, fns: list, lang: str = "de", depth: int = 0) -> str:
    calls = draft_calls(agent, fns, text, lang)
    if not calls:
        _log("needle: kein Call -> still")
        return ""
    outputs, writes = [], []
    for call in calls:
        name, args = call["tool"], call["arguments"]
        _log(f"needle -> {name}({args})")
        if name in WRITE:
            writes.append(call)
        else:
            outputs.append(tools.execute(name, args, prompt))
    if writes and depth < 3:
        out = _plan_flow(tools, agent, fns, writes, depth, prompt)
        if out:
            outputs.append(out)
    return "\n".join(o for o in outputs if o)


def _plan_flow(tools: MiniTools, agent, fns: list, calls: list[dict], depth: int,
               turn_text: str = "") -> str:
    """EIN Approval für den ganzen Plan; e = Freitext-Korrektur → neue Needle-Runde."""
    plan = tools.plan(calls, turn_text)
    if not plan["ops"]:
        return "\n".join(plan["lines"]) or ""
    lines = plan["lines"] + [f"⚠ {w}" for w in plan["warn"]]
    _console.print(Panel("\n".join(lines),
                         title="Plan · y=ausführen · n=nein · e=Korrektur (Freitext) · q=abbrechen",
                         border_style="yellow"))
    answer = Prompt.ask("Ausführen?", default="n", choices=["y", "n", "e", "q"],
                        show_choices=False, console=_console).strip().lower()
    if answer == "y":
        return tools.apply(plan) + "\n" + _after_state(tools, plan)
    if answer == "e":
        fix = Prompt.ask("Korrektur in Freitext (z. B. 'zahnarzt auf 10:30, hundefrisör auf 10 uhr'):",
                         console=_console).strip()
        if fix and depth < 2:
            return process(fix, tools, agent, fns, depth=depth + 1) or "Nichts geändert."
        if not fix:
            return "Ok, nichts gespeichert."
        return "Zu viele Korrekturrunden — abgebrochen."
    if answer == "q":
        return "Abgebrochen."
    return "Ok, nichts gespeichert."


def _after_state(tools: MiniTools, plan: dict) -> str:
    """Der betroffene Tag nach Ausführung (lokale Zeit)."""
    days = {op[k].astimezone().date() for op in plan["ops"] for k in ("start", "due") if op.get(k)}
    if not days:
        return ""
    return tools.orga.list_events("monat")


def main() -> None:
    tools, agent, fns = build()
    _console.print("[bold cyan]needle-only v3.1[/] — Plan-Werkstatt (Kalender · Reminders · Notes) · /status · /quit")
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
            _console.print(tools.execute("show_status", {}))
            continue
        if text.lower() == "/log":
            global LOG_ON
            LOG_ON = not LOG_ON
            _console.print(f"[dim]logs: {'an' if LOG_ON else 'aus'}[/]")
            continue
        try:
            out = process(text, tools, agent, fns)
            if out:
                _console.print(f"[bold cyan]cactus[/] · {out}")
        except Exception as exc:
            _console.print(f"[bold red]fehler:[/] {exc}")


if __name__ == "__main__":
    main()
