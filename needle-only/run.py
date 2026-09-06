"""Needle-only v5.4 „Router": Semantic Router als Triage-Layer.
Router (~100ms, deterministisch) wählt das Tool, Needle extrahiert Argumente.
Bei Unsicherheit: Fallback auf volle Needle-Routing (alle 7 Tools).
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

# Semantic Router (lazy geladen)
_router = None

# Cache für per-tool Needle-Sessions (Argument-Extraktion)
_extract_cache = {}
_SYSTEM_PROMPT = None


def _log(line: str) -> None:
    if LOG_ON:
        print(f"  · {line}")


def _get_router():
    """Lazy-init des Semantic Routers (lädt Embedding-Modell beim ersten Aufruf)."""
    global _router
    if _router is None:
        from router import ToolRouter
        _router = ToolRouter(threshold=0.55)
    return _router


def _get_extract_session(tool_name: str, tool_fn, system_prompt: str):
    """Lazy-init Needle-Session mit NUR diesem Tool (für Argument-Extraktion)."""
    key = tool_name
    if key not in _extract_cache:
        _extract_cache[key] = needle.Needle(
            tools=[tool_fn], system=system_prompt)
    return _extract_cache[key]


def build():
    """Tools, Needle-Agent und Tool-Funktionen aufsetzen."""
    global _SYSTEM_PROMPT
    cfg = load_config()
    tools = MiniTools(Orga(cfg.database_url))
    fns = tools.build()
    _SYSTEM_PROMPT = f"current date: {tz_now().strftime('%Y-%m-%d %H:%M')}. locale: de-DE."
    agent = needle.Needle(tools=fns, system=_SYSTEM_PROMPT)
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
    """Whitelist + Zeit-Auflösung + Kind-Default + Title-Cleanup."""
    keep = allowed_args(name, fns)
    clean = {k: v for k, v in (args or {}).items() if keep is None or k in keep}

    # Title-Cleanup: "termin X" → "X" (Wort 'termin' ist Typ-Indikator, nicht Name)
    if "title" in clean and clean["title"]:
        title_str = str(clean["title"]).strip()
        low_title = title_str.lower()
        if low_title.startswith("termin ") and len(title_str) > 7:
            title_str = title_str[7:].strip()
        elif low_title.startswith("einen termin ") and len(title_str) > 13:
            title_str = title_str[13:].strip()
        clean["title"] = title_str

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

    # Wenn start_at fehlt: Versuche aus dem Original-Text zu extrahieren
    if name in ("calendar_create", "calendar_edit") and not clean.get("start_at"):
        extracted = _extract_datetime_from_text(text)
        if extracted:
            clean["start_at"] = extracted

    # kind-Handling: Default 'appointment' wenn leer/fehlt
    if name == "calendar_create":
        kind = str(clean.get("kind", "")).strip().lower()
        if not kind or kind not in ("appointment", "reminder", "task"):
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
            elif any(w in text_lower for w in ("aufgabe", "aufgaben", "task", "todo")):
                clean["kind"] = "task"
            elif any(w in text_lower for w in ("termin", "termine", "appointment", "meeting")):
                clean["kind"] = "appointment"

    return clean


def _extract_datetime_from_text(text: str):
    """Extrahiert Datum/Uhrzeit aus dem Original-Text (Fallback wenn Needle leer liefert)."""
    # Relativ: "in N minuten" / "in N stunden"
    m = re.search(r'in\s+(\d+)\s*(minuten?|min|m)\b', text, re.I)
    if m:
        return tz_now() + timedelta(minutes=int(m.group(1)))
    m = re.search(r'in\s+(\d+)\s*(stunden?|std|h)\b', text, re.I)
    if m:
        return tz_now() + timedelta(hours=int(m.group(1)))

    # Wochentag: "am dienstag", "dienstag", etc.
    WEEKDAYS = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
                "freitag": 4, "samstag": 5, "sonntag": 6}
    for wd_name, wd_num in WEEKDAYS.items():
        m = re.search(rf'\b{wd_name}\b', text, re.I)
        if m:
            now = tz_now()
            days_ahead = (wd_num - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = now + timedelta(days=days_ahead)
            # Uhrzeit aus Text extrahieren, falls vorhanden
            tm = re.search(r'(\d{1,2})[:.](\d{2})\b', text) or re.search(r'\b(\d{1,2})\s*uhr\b', text)
            if tm:
                hour = int(tm.group(1))
                minute = int(tm.group(2)) if tm.lastindex == 2 else 0
                target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target

    # "morgen" / "heute" / "übermorgen"
    for keyword, days in (("übermorgen", 2), ("morgen", 1), ("heute", 0)):
        if re.search(rf'\b{keyword}\b', text, re.I):
            target = tz_now() + timedelta(days=days)
            tm = re.search(r'(\d{1,2})[:.](\d{2})\b', text) or re.search(r'\b(\d{1,2})\s*uhr\b', text)
            if tm:
                hour = int(tm.group(1))
                minute = int(tm.group(2)) if tm.lastindex == 2 else 0
                target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target

    return None


def _normalize_for_needle(text: str, tool_name: str) -> str:
    """Normalisiert Text für die per-tool Needle-Extraktion.
    Needle 45M kann 'erinnerung'/'aufgabe'-Phrasen nicht parsen,
    aber 'termin'-Phrasen funktionieren zuverlässig."""
    if tool_name != "calendar_create":
        return text

    result = text

    # "erinner mich [in X] an Y" → "erstelle einen termin Y [in X]"
    m = re.search(r'erinner\w*\s+mich\s+(?:in\s+(\S+)\s+)?an\s+(.+)', result, re.I)
    if m:
        time_part = f" in {m.group(1)}" if m.group(1) else ""
        return f"erstelle einen termin {m.group(2)}{time_part}"

    # "stell(e) eine erinnerung X" → "erstelle einen termin X"
    m = re.search(r'stell(?:e|n)?\s+eine\s+erinnerung\s+(.+)', result, re.I)
    if m:
        return f"erstelle einen termin {m.group(1)}"

    # "erstelle eine erinnerung X" → "erstelle einen termin X"
    m = re.search(r'erstelle\s+eine\s+erinnerung\s+(.+)', result, re.I)
    if m:
        return f"erstelle einen termin {m.group(1)}"

    # "erinnerung X" → "erstelle einen termin X"
    m = re.match(r'erinnerung\s+(.+)', result, re.I)
    if m:
        return f"erstelle einen termin {m.group(1)}"

    # "aufgabe X" → "erstelle einen termin X"
    m = re.match(r'aufgabe\s+(.+)', result, re.I)
    if m:
        return f"erstelle einen termin {m.group(1)}"

    # "erstelle eine aufgabe X" → "erstelle einen termin X"
    m = re.search(r'erstelle\s+eine\s+aufgabe\s+(.+)', result, re.I)
    if m:
        return f"erstelle einen termin {m.group(1)}"

    return result


def _template_extract(tool_name: str, text: str, fns: list) -> dict | None:
    """Regex-Extraktion als Fallback, wenn Needle keine Argumente liefert.
    deckt die häufigsten erinnerung-/aufgabe-/notiz-muster ab."""
    low = text.lower().strip()
    now = tz_now()

    if tool_name == "calendar_create":
        # "erinner mich in N minuten an X"
        m = re.search(r'erinner\w*\s+mich\s+in\s+(\d+)\s*min(uten)?\s+an\s+(.+)', low)
        if m:
            mins = int(m.group(1))
            return {"title": m.group(3).strip().capitalize(),
                    "start_at": (now + timedelta(minutes=mins)).isoformat(),
                    "kind": "reminder"}

        # "stell eine erinnerung X in N minuten"
        m = re.search(r'stell\w*\s+eine\s+erinnerung\s+(.+?)\s+in\s+(\d+)\s*min', low)
        if m:
            mins = int(m.group(2))
            return {"title": m.group(1).strip().capitalize(),
                    "start_at": (now + timedelta(minutes=mins)).isoformat(),
                    "kind": "reminder"}

        # "erinnerung X in N minuten"
        m = re.search(r'erinnerung\s+(.+?)\s+in\s+(\d+)\s*min', low)
        if m:
            mins = int(m.group(2))
            return {"title": m.group(1).strip().capitalize(),
                    "start_at": (now + timedelta(minutes=mins)).isoformat(),
                    "kind": "reminder"}

        # "stell eine erinnerung X <zeit>" — Zeit via resolve_dt aus Rest
        m = re.search(r'stell\w*\s+eine\s+erinnerung\s+(.+)', low)
        if m:
            rest = m.group(1).strip()
            # Zeit-Ausdruck am Ende isolieren ("morgen früh 8 uhr", "morgen 14 uhr")
            tm = re.search(r'((?:übermorgen|morgen|heute|montag|dienstag|mittwoch|'
                           r'donnerstag|freitag|samstag|sonntag).*)$', rest)
            title = rest
            start_iso = None
            if tm:
                title = rest[:tm.start()].strip().rstrip(',').rstrip()
                resolved = resolve_dt(tm.group(1))
                if resolved:
                    start_iso = resolved.isoformat()
            if not start_iso:
                resolved = resolve_dt(rest)
                if resolved:
                    start_iso = resolved.isoformat()
                    title = rest
            return {"title": (title or rest).capitalize(),
                    "start_at": start_iso or (now + timedelta(hours=1)).isoformat(),
                    "kind": "reminder"}

        # "aufgabe X bis Y" (task)
        m = re.search(r'aufgabe\s+(.+?)\s+bis\s+(.+)', low)
        if m:
            return {"title": m.group(1).strip().capitalize(),
                    "start_at": m.group(2).strip(),
                    "kind": "task"}

        # "erinnerung X" (ohne Zeitangabe)
        m = re.match(r'erinnerung\s+(.+)', low)
        if m:
            return {"title": m.group(1).strip().capitalize(),
                    "start_at": (now + timedelta(hours=1)).isoformat(),
                    "kind": "reminder"}

    elif tool_name == "calendar_edit":
        # "änder den termin X auf Y" / "verschiebe X auf Y"
        m = re.search(r'(?:änder\w*|verschieb\w*)\s+(?:den\s+)?termin\s+(.+?)\s+auf\s+(.+)', low)
        if m:
            return {"title": m.group(1).strip().capitalize(),
                    "start_at": m.group(2).strip()}
        m = re.search(r'verschieb\w*\s+(.+?)\s+(?:auf|um|bis)\s+(.+)', low)
        if m:
            return {"title": m.group(1).strip().capitalize(),
                    "start_at": m.group(2).strip()}

    elif tool_name == "calendar_read":
        # kind aus Text inferieren
        kind = "all"
        if any(w in low for w in ("erinnerung", "erinner", "reminder")):
            kind = "reminder"
        elif any(w in low for w in ("aufgabe", "aufgaben", "task", "todo")):
            kind = "task"
        elif any(w in low for w in ("termin", "termine", "appointment", "meeting")):
            kind = "appointment"
        return {"kind": kind, "horizon": "woche"}

    elif tool_name == "note_write":
        # "merk(e) dir X kostet Y" / "merk(e) dir X" → subject=X, body=Rest
        m = re.search(r'merk\w*\s+dir\s+(.+)', low)
        if m:
            rest = m.group(1).strip()
            # "X kostet Y" → subject=X, body="kostet Y"
            km = re.match(r'(.+?)\s+kostet\s+(.+)', rest)
            if km:
                return {"subject": km.group(1).strip().capitalize(),
                        "body": f"kostet {km.group(2).strip()}"}
            return {"subject": rest.capitalize(), "body": ""}

        # "speichere X" → subject=X
        m = re.search(r'speicher\w*\s+(.+)', low)
        if m:
            return {"subject": m.group(1).strip().capitalize(), "body": ""}

    elif tool_name == "note_read":
        # "was weißt du über X" → query="X"
        m = re.search(r'(?:weißt|weisst)\s+du\s+(?:über|von|zu)\s+(.+)', low)
        if m:
            return {"query": m.group(1).strip()}
        # "suche X" / "suche notiz X" → query="X"
        m = re.search(r'such\w*\s+(?:notiz\s+)?(.+)', low)
        if m:
            return {"query": m.group(1).strip()}
        # "zeige notizen" / "zeige alle notizen" → query=""
        return {"query": ""}

    return None


def _resolve_datetime(value: str, context: str = "") -> datetime | None:
    """Versucht, einen String in ein datetime zu übersetzen."""
    # Direkter ISO-String?
    dt = _norm_dt(value)
    if dt:
        return dt
    # Relative Angaben wie "in 10 min" oder "morgen 14:00"?
    return resolve_flexible(value)


def draft_calls(agent, fns, text: str, lang: str = "de") -> list:
    """Router-first: Semantic Router wählt Tool, Needle extrahiert Argumente.
    Bei Unsicherheit oder Extraktions-Fehler: Fallback auf volle Needle-Routing."""
    if lang == "en":
        from translate import de2en
        text, _ = de2en(text)

    # === STUFE 1: Semantic Router als Triage (nur Single-Op) ===
    is_multi = bool(re.search(r"\bund\b", text))

    if not is_multi:
        try:
            router = _get_router()
            tool_name, score = router.route(text)

            if tool_name and score >= 0.55:
                # Router ist sicher → Needle mit NUR diesem Tool
                tool_fn = next(
                    (f for f in fns if f.__name__ == tool_name), None)
                if tool_fn:
                    session = _get_extract_session(
                        tool_name, tool_fn, _SYSTEM_PROMPT)
                    session.reset()
                    # Text normalisieren: 'erinnerung'/'aufgabe' → 'termin'
                    needle_text = _normalize_for_needle(text, tool_name)
                    resp = session.complete(needle_text)
                    calls = resp.get("function_calls") or []
                    if calls:
                        args = calls[0].get("arguments") or {}
                        _log(f"router -> {tool_name} (score={score:.2f})")
                        return [{"tool": tool_name,
                                 "arguments": fix_args(text, tool_name, args, fns)}]

                # === STUFE 1b: Template-Fallback wenn Needle leer ===
                template_args = _template_extract(tool_name, text, fns)
                if template_args:
                    _log(f"template -> {tool_name} (needle war leer)")
                    return [{"tool": tool_name,
                             "arguments": fix_args(text, tool_name, template_args, fns)}]

        except Exception as exc:
            _log(f"router error: {exc} → fallback needle")

    # === STUFE 2: Volle Needle-Routing (alle 7 Tools) ===
    agent.reset()
    resp = agent.complete(text)
    calls = resp.get("function_calls") or []

    # Wenn nur 1 Call und der Text "und" enthält → Retry für Multi-Op
    if len(calls) < 2 and is_multi:
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
