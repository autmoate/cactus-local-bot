"""Needle-only v5.5 „Router": Semantic Router als Triage-Layer.
Router (~100ms, deterministisch) wählt das Tool, Needle extrahiert Argumente.
Bei Unsicherheit: Fallback auf volle Needle-Routing (alle 5 Tools).

NEU in v5.5:
- Notizen ENTFERNT (Nutzer-Wunsch: verschlanken)
- 'absence' Kind (Urlaub/Reise/krank) — mehrtägig, kollidiert NICHT
- Multi-Day-Support: 'von 07.09. bis 11.09.' → start_at + end_at
- Kollision: NUR appointment+appointment (±30 min)
"""
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
WRITE = {"calendar_create", "calendar_edit", "calendar_delete"}
_console = Console()

# Semantic Router (lazy geladen)
_router = None

# Cache für per-tool Needle-Sessions (Argument-Extraktion)
_extract_cache = {}
_SYSTEM_PROMPT = None

# Abwesenheits-Keywords (für kind='absence' Detection)
ABSENCE_KEYWORDS = (
    "urlaub", "verreist", "abwesend", "krank", "abwesenheit",
    "krankmeldung", "dienstreise", "geschäftsreise", "reise",
)


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
        return cand + timedelta(days=1) if cand < tz_now() else cand
    return None


def _parse_date_range(text: str):
    """Parst 'von 07.09. bis 11.09.' → (start, end).
    Wird für mehrtägige Abwesenheiten (Urlaub etc.) verwendet."""
    # "von 7.9. bis 11.9." / "vom 07.09.2026 bis 11.09.2026"
    m = re.search(
        r'v[oön]m?\s+(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?\s+'
        r'bis\s+(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?',
        text, re.I)
    if m:
        now = tz_now()
        year_s = m.group(3)
        year_e = m.group(6)
        y_s = int(year_s) if year_s else now.year
        y_e = int(year_e) if year_e else y_s
        try:
            start = datetime(y_s, int(m.group(2)), int(m.group(1)),
                             0, 0, 0, tzinfo=now.tzinfo)
            end = datetime(y_e, int(m.group(5)), int(m.group(4)),
                           23, 59, 59, tzinfo=now.tzinfo)
            return start, end
        except ValueError:
            return None, None

    # "von 7.9. bis 11.9." ohne 'von'-Prefix
    m = re.search(
        r'(\d{1,2})\.(\d{1,2})\.?\s+bis\s+(\d{1,2})\.(\d{1,2})\.?',
        text, re.I)
    if m:
        now = tz_now()
        try:
            start = datetime(now.year, int(m.group(2)), int(m.group(1)),
                             0, 0, 0, tzinfo=now.tzinfo)
            end = datetime(now.year, int(m.group(4)), int(m.group(3)),
                           23, 59, 59, tzinfo=now.tzinfo)
            return start, end
        except ValueError:
            return None, None

    return None, None


def _detect_absence(text: str) -> bool:
    """Erkennt Abwesenheits-Anfragen (Urlaub, verreist, krank etc.)."""
    low = text.lower()
    return any(w in low for w in ABSENCE_KEYWORDS)


def fix_args(text: str, name: str, args: dict, fns: list) -> dict:
    """Whitelist + Zeit-Auflösung + Kind-Default + Absence-Handling."""
    keep = allowed_args(name, fns)
    clean = {k: v for k, v in (args or {}).items() if keep is None or k in keep}

    # Title-Cleanup: "termin X" → "X"
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
                # Wochentag-Korrektur: Needle berechnet 'dienstag' oft falsch
                resolved = _correct_weekday_date(text, resolved)
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

    # kind-Handling für calendar_create
    if name == "calendar_create":
        kind = str(clean.get("kind", "")).strip().lower()

        # 1) Absence-Detection: Urlaub/verreist/krank → kind='absence'
        if _detect_absence(text):
            kind = "absence"

        # 2) Wenn kein gültiger kind: aus Text inferieren
        if not kind or kind not in ("appointment", "reminder", "task", "absence"):
            text_lower = (text or "").lower()
            if any(w in text_lower for w in ("erinnerung", "erinnere", "erinner", "reminder", "timer")):
                kind = "reminder"
            elif any(w in text_lower for w in ("aufgabe", "task", "todo", "deadline")):
                kind = "task"
            else:
                kind = "appointment"

        clean["kind"] = kind

        # 3) Multi-Day für Absences: "von 07.09. bis 11.09." → start + end
        if kind == "absence" and not clean.get("end_at"):
            start_dt, end_dt = _parse_date_range(text)
            if start_dt and end_dt:
                clean["start_at"] = start_dt
                clean["end_at"] = end_dt

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
    """Extrahiert Datum/Uhrzeit aus dem Original-Text.
    Needle 45M berechnet 'dienstag' oft falsch (Mo statt Di) —
    daher berechnen wir Wochentage SELBST."""
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
                if not 0 <= hour <= 23:
                    return None
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
                if not 0 <= hour <= 23:
                    return None
                minute = int(tm.group(2)) if tm.lastindex == 2 else 0
                target = target.replace(hour=hour, minute=minute, second=0, microsecond=0)
            return target

    return None


def _correct_weekday_date(text: str, resolved_dt):
    """Korrigiert das Datum, wenn der Text einen Wochentag enthält.
    Needle 45M berechnet Wochentage oft falsch (z.B. 'dienstag' → Montag statt Dienstag).
    Wir berechnen den Wochentag selbst und überschreiben Needles Datum."""
    if resolved_dt is None:
        return resolved_dt

    WEEKDAYS = {"montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
                "freitag": 4, "samstag": 5, "sonntag": 6}
    low = text.lower()
    for wd_name, wd_num in WEEKDAYS.items():
        if re.search(rf'\b{wd_name}\b', low):
            # Prüfen ob Needles Datum bereits den richtigen Wochentag hat
            if resolved_dt.weekday() == wd_num:
                return resolved_dt  # Bereits korrekt
            # Nein: Wochentag selbst berechnen, Uhrzeit von Needle übernehmen
            now = tz_now()
            days_ahead = (wd_num - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target_date = (now + timedelta(days=days_ahead)).date()
            return resolved_dt.replace(
                year=target_date.year, month=target_date.month, day=target_date.day)
    return resolved_dt


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
    """Regex-Extraktion als Fallback, wenn Needle keine Argumente liefert."""
    low = text.lower().strip()
    now = tz_now()

    if tool_name == "calendar_create":
        # === ABSENCE: Urlaub/verreist/krank (mehrtägig) ===
        if _detect_absence(text):
            start_dt, end_dt = _parse_date_range(text)
            if start_dt and end_dt:
                return {"title": "Urlaub", "kind": "absence",
                        "start_at": start_dt.isoformat(),
                        "end_at": end_dt.isoformat()}

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

        # "stell eine erinnerung X <zeit>"
        m = re.search(r'stell\w*\s+eine\s+erinnerung\s+(.+)', low)
        if m:
            rest = m.group(1).strip()
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
        m = re.search(r'(?:änder\w*|verschieb\w*)\s+(?:den\s+)?termin\s+(.+?)\s+auf\s+(.+)', low)
        if m:
            return {"title": m.group(1).strip().capitalize(),
                    "start_at": m.group(2).strip()}
        m = re.search(r'verschieb\w*\s+(.+?)\s+(?:auf|um|bis)\s+(.+)', low)
        if m:
            return {"title": m.group(1).strip().capitalize(),
                    "start_at": m.group(2).strip()}

    elif tool_name == "calendar_read":
        kind = "all"
        if any(w in low for w in ("erinnerung", "erinner", "reminder")):
            kind = "reminder"
        elif any(w in low for w in ("aufgabe", "aufgaben", "task", "todo")):
            kind = "task"
        elif any(w in low for w in ("termin", "termine", "appointment", "meeting")):
            kind = "appointment"
        return {"kind": kind, "horizon": "woche"}

    elif tool_name == "calendar_filter":
        # Person aus Text extrahieren: "wann hat Lisa diese woche termine"
        person = ""
        # Muster: "wann hat [Person] ..."
        m = re.search(r'wann\s+hat\s+(\S+)', low)
        if m:
            person = m.group(1).strip()
        # Muster: "termine von [Person]"
        m = re.search(r'termine\s+von\s+(\S+)', low)
        if m:
            person = m.group(1).strip()
        # Muster: "kalender von [Person]"
        m = re.search(r'kalender\s+von\s+(\S+)', low)
        if m:
            person = m.group(1).strip()

        horizon = "woche"
        if "heute" in low:
            horizon = "heute"
        elif "monat" in low:
            horizon = "monat"

        return {"person": person, "horizon": horizon}

    return None


def _resolve_datetime(value: str, context: str = "") -> datetime | None:
    """Versucht, einen String in ein datetime zu übersetzen."""
    dt = _norm_dt(value)
    if dt:
        return dt
    return resolve_flexible(value)


def draft_calls(agent, fns, text: str, lang: str = "de") -> list:
    """Router-first: Semantic Router wählt Tool, Needle extrahiert Argumente."""
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

    # === STUFE 2: Volle Needle-Routing (alle 5 Tools) ===
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
        # Write-Tools direkt ausführen
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

    _console.print("[bold cyan]needle-only v5.5[/] — CRUD + Absence · /status · /quit")
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
