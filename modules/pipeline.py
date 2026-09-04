import json
import re

from modules.needle_verifier import _SEMANTIC
from modules.timesync import human_now, format_local
from modules.tool_catalog import WRITE_TOOLS

_MAX_CALLS = 4


def parse_tag(prompt: str, trigger: str = "@") -> tuple[str | None, str]:
    s = prompt.lstrip()
    triggers = {t.strip() for t in (trigger, "@", "@cactus", "&cactus", "&bot", "cactus") if t and t.strip()}
    triggers = sorted(triggers, key=len, reverse=True)
    low = s.lower()
    for t in triggers:
        if low.startswith(t.lower()):
            tag = t.strip("&@ ")
            return (tag or None), s[len(t):].strip()
    return None, s


def _looks_question(text: str) -> bool:
    low = text.lower().strip()
    markers = ("?", "wer ", "was ", "wann", "warum", "kannst", "kann ",
               "bitte", "erkl", "sag", "zeig", "welche", "gibt es",
               "explain", "what ", "how ", "why ", "wie viel", "wieso")
    return any(m in low for m in markers) or low.endswith("?")


def _context(rt) -> str:
    from datetime import datetime, timedelta, timezone
    from modules.timesync import today_iso
    now = datetime.now(timezone.utc)
    parts = [f"Aktuelle Zeit (lokal): {human_now()}.  (Heute: {today_iso()})"]
    try:
        inv = []
        with rt.store.connect() as con:
            rows = con.execute("select title, metadata->>'quantity' from inventory order by id desc limit 6").fetchall()
            inv = [f"{t}={q or '–'}" for t, q in rows]
        if inv:
            parts.append("Inventar: " + ", ".join(inv))
        ev = rt.store.list_events(now, now + timedelta(days=7), limit=5)
        if ev:
            parts.append("Nächste Termine: " + "; ".join(
                f"{format_local(e['start_at'].isoformat() if hasattr(e['start_at'],'isoformat') else str(e['start_at']))} {e['title']}"
                for e in ev))
        last = rt.store.last_todo_title()
        if last:
            parts.append(f"Zuletzt erstellt: {last}")
        dial = rt.memory.recent_dialogue(4)
        if dial:
            parts.append("Letzter Dialog: " + " | ".join(f"{d['role'][:4]}: {d['text'][:60]}" for d in dial))
    except Exception:
        pass
    parts.append("Verfügbare Tools: " + ", ".join(rt.catalog.names()))
    return "\n".join(parts)


def _fix_time(rest: str, call: dict) -> dict:
    from modules.timesync import now, to_utc_iso
    import datetime as _dt
    low = rest.lower()
    m = re.search(r"in\s+(\d+)\s*(min(uten?|ute)?s?|m|stunde?n?|h)\b", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = _dt.timedelta(minutes=n) if unit.startswith(("min", "m")) else _dt.timedelta(hours=n)
        due = to_utc_iso(now() + delta)
        call = dict(call)
        args = dict(call.get("arguments") or {})
        if call["name"] == "set_timer":
            args["in_min"] = int(delta.total_seconds() // 60)
        else:
            args["due_at"] = due
        call["arguments"] = args
        return call
    tm = re.search(r"(\d{1,2}):(\d{2})\b", rest) or re.search(r"(\d{1,2})\.(\d{2})\s*uhr\b", rest)
    if tm and call["name"] in ("create_todo", "update_todo", "create_calendar_event"):
        ref = now()
        cand = ref.replace(hour=int(tm.group(1)), minute=int(tm.group(2)), second=0, microsecond=0)
        if cand < ref:
            cand += _dt.timedelta(days=1)
        call = dict(call)
        args = dict(call.get("arguments") or {})
        args["due_at" if call["name"] != "create_calendar_event" else "start_at"] = to_utc_iso(cand)
        call["arguments"] = args
    return call


def _fix_list(rest: str, args: dict) -> dict:
    low = rest.lower()
    has_todo = any(w in low for w in ("todo", "to-do", "to dos", "aufgab"))
    has_cal = any(w in low for w in ("termin", "kalender", "event", "meeting"))
    args = dict(args)
    if has_todo and not has_cal:
        args["area"] = "todos"
    elif has_cal and not has_todo:
        args["area"] = "calendar_events"
    if any(w in low for w in ("heute", "morgen")):
        args["horizon"] = "day"
    elif "monat" in low:
        args["horizon"] = "month"
    return args


def _fill_timer(rest: str, args: dict) -> dict:
    args = dict(args)
    if not args.get("title") or str(args.get("title")) in ("Timer", ""):
        low = rest
        for pat in (r"^set\s+timer\s*", r"^timer\s*", r"^stelle?\s+einen?\s+timer\s*"):
            low = re.sub(pat, "", low)
        m = re.search(r"in\s+\d+\s*(min(uten?|ute)?s?|m)\b", low)
        if m:
            low = low[:m.start()] + " " + low[m.end():]
        m2 = re.match(r"^\s*\d+\s*(min(uten?|ute)?s?|m)\b", low)
        if m2:
            low = low[m2.end():]
        title = " ".join(low.split())
        if title:
            args["title"] = title[:60]
    return args


_CAL_LEAD = ("lege", "trage", "trag", "plane", "plan", "plant", "vereinbare",
             "vereinbart", "notiere", "notier", "bitte", "für", "das", "den", "der", "die",
             "ein", "einen", "eine", "deinen", "deine", "gerne", "auch", "am", "und",
             "kannst", "könntest", "du", "uns", "mir", "mit")


def _cal_title(clean: str) -> str | None:
    toks = [w.strip(".,;:!?") for w in clean.split()]
    while toks and toks[0].lower() in _CAL_LEAD:
        toks.pop(0)
    if not toks:
        return None
    return " ".join(toks[:3]).strip(" .,;:!?") or None


def _fix_cal(rest: str, call: dict) -> dict:
    if call.get("name") != "create_calendar_event":
        return call
    from modules.timesync import parse_calendar
    pc = parse_calendar(rest)
    if not pc["iso"]:
        return call
    call = dict(call)
    args = dict(call.get("arguments") or {})
    args["start_at"] = pc["iso"]
    clean_title = _cal_title(pc["cleaned"]) or None
    if clean_title:
        args["title"] = clean_title
    call["arguments"] = args
    return call


def _fix_item(text: str, call: dict) -> dict:
    """Generalistisch: 'N <einheit> <artikel>' -> Titel ist der Artikel, nicht die Einheit."""
    if call.get("name") not in ("update_inventory", "consume_inventory", "add_inventory"):
        return call
    args = dict(call.get("arguments") or {})
    key = "title" if args.get("title") else ("name" if args.get("name") else None)
    title = str(args.get(key) or "") if key else ""
    m = re.search(rf"\b\d+\s+{re.escape(title.lower())}\s+([a-zäöüß]+)", (text or "").lower())
    if key and title and m:
        args[key] = m.group(1)
        call = dict(call)
        call["arguments"] = args
    return call


def resolve_calls(text: str, calls: list[dict]) -> list[dict]:
    """Deterministische Fixer auf bereits gewählte Calls — nie Toolwahl, nur Struktur/Zeit."""
    out, seen = [], set()
    for call in calls or []:
        tool = call.get("name")
        if not tool or tool in seen:
            continue
        seen.add(tool)
        args = dict(call.get("arguments") or {})
        if tool == "list_upcoming":
            args = _fix_list(text, args)
        elif tool == "set_timer":
            args = _fill_timer(text, args)
        call = _fix_time(text, {"name": tool, "arguments": args})
        call = _fix_item(text, call)
        if tool == "create_calendar_event":
            call = _fix_cal(text, call)
        out.append(call)
        if len(out) >= _MAX_CALLS:
            break
    return out


def format_result(tool: str, result) -> str:
    if tool == "list_upcoming":
        if not result:
            return "Keine anstehenden Einträge im Zeitraum."
        lines = []
        for i in result:
            when = i.get("at")
            d = format_local(when.isoformat()) if hasattr(when, "isoformat") else str(when)
            extra = f" {i.get('urgency') or ''}" if i.get("urgency") not in (None, "normal") else ""
            lines.append(f"  {d}  {i.get('title')}{extra}")
        return "Anstehend:\n" + "\n".join(lines)
    if tool == "search_records":
        if not result:
            return "Nichts gefunden."
        lines = [f"  '{i.get('title')}'" + (f" — {(i.get('body') or '')[:80]}" if i.get("body") else "")
                 for i in result[:5]]
        return "Gefunden:\n" + "\n".join(lines)
    if tool == "web_search":
        if not result:
            return "Keine Treffer (SearXNG best-effort)."
        return "\n".join(f"  WEB: {i.get('title') or ''} {i.get('url') or ''}\n    {(i.get('content') or '')[:120]}"
                         for i in result)
    return json.dumps(result, indent=2, default=str)


def _execute(rt, call: dict, log) -> str:
    tool, args = call["name"], call.get("arguments") or {}
    try:
        result = rt.catalog.execute(tool, args)
    except Exception as exc:
        log(f"exec {tool} -> FEHLER {str(exc)[:60]}")
        return f"Fehler bei {tool}: {str(exc)[:80]}"
    log(f"exec {tool} -> {str(result)[:80]}")
    text = format_result(tool, result)
    if tool in WRITE_TOOLS:
        rt.reflect.learn(tool, args)
        confirm = rt.reflect.confirm(tool, args)
        if confirm:
            text = confirm
    return text


def _cross_check(rt, text: str, call: dict, context: str, log):
    """Nur bei Writes: Needle als Zweitmeinung, advisory (blockiert nie)."""
    best_args = dict(call.get("arguments") or {})
    if rt.verifier is None or not rt.verifier.ensure():
        log("verifier: n/a (nur gemma)")
        return {"best": call}
    eng = rt.interpreter.english_instruction(text, context)
    if eng.startswith("Gemma/Cactus unavailable"):
        return {"best": call}
    nc = rt.verifier.check(eng)
    if not nc.get("ok"):
        log(f"verifier: needle no-call ({nc.get('reason') or nc.get('error')})")
        return {"best": call}
    agreed, msg = rt.verifier.agreed(call["name"], call.get("arguments") or {}, nc)
    if agreed:
        log(f"verifier: ok (needle conf {nc.get('confidence')})")
    else:
        log(f"verifier: abweichung (info): {msg[:90]}")
    for k in _SEMANTIC:
        if k not in best_args and k in (nc.get("args") or {}):
            best_args[k] = nc["args"][k]
    return {"best": {"name": call["name"], "arguments": best_args}}


def _run_calls(rt, rest, calls, context, log, confirm, outputs, seen, saw_web=False) -> bool:
    for call in calls:
        tool = call.get("name")
        if not tool or tool in seen or len(seen) >= _MAX_CALLS:
            continue
        seen.add(tool)
        if tool == "web_search":
            saw_web = True
        elif saw_web and tool in WRITE_TOOLS:
            log(f"guard: write nach web_search blockiert ({tool})")
            outputs.append("Erst suchen, dann schreiben — nenn mir nach der Suche den Eintrag, dann trage ich ihn ein.")
            continue
        if tool in WRITE_TOOLS:
            best = _cross_check(rt, rest, call, context, log)["best"]
            if not confirm(best):
                log(f"write {tool}: abgelehnt")
                outputs.append("Ok, nichts gespeichert.")
                continue
            outputs.append("cactus · " + _execute(rt, best, log))
        else:
            outputs.append("cactus · " + _execute(rt, call, log))
    return saw_web


_FOLLOW = ("Prüfe den ursprünglichen Wunsch: Steckt darin noch ein SCHREIB-Auftrag (anlegen/ändern), "
           "der noch nicht ausgeführt wurde? Dann rufe genau dieses Tool auf. "
           "Lese-Tools brauchst du nicht mehr. Sonst antworte nur mit: fertig.")


def process(prompt: str, rt, log, confirm, trigger: str = "@") -> dict:
    tag, rest = parse_tag(prompt, trigger)
    context = _context(rt)
    calls = resolve_calls(rest, rt.interpreter.draft(rest, context))
    log(f"triage: gemma -> {len(calls)} call(s): {', '.join(c['name'] for c in calls) or 'keine'}")
    if not calls and (tag or _looks_question(rest)):
        calls = resolve_calls(rest, rt.interpreter.draft(
            rest,
            context + "\nHinweis: Prüfe zuerst, ob ein SCHREIB-Auftrag steckt (anlegen/ändern), "
                      "sonst ein lokales Lese-Tool (Termine, To-dos, Inventar, Fakten, Status). "
                      "Für Allgemeinwissen kein Tool.",
            history=[{"role": "assistant", "content": "Passendes Tool wählen oder ohne Tool antworten."}]))
        if calls:
            log(f"triage: retry -> {', '.join(c['name'] for c in calls)}")
    if not calls:
        if tag or _looks_question(rest):
            answer = rt.cactus.complete(rest, context, rt.reasoning)
            log("triage: kontextfrage -> antwort")
            return {"decision": "answer", "text": answer}
        log("triage: silent (kein Handlungsbedarf)")
        return {"decision": "silent", "text": ""}

    outputs: list[str] = []
    seen: set[str] = set()
    saw_web = _run_calls(rt, rest, calls, context, log, confirm, outputs, seen)
    if getattr(rt, "followup", False):
        follow = rt.interpreter.draft(
            _FOLLOW, context,
            history=[{"role": "user", "content": rest},
                     {"role": "assistant", "content": "; ".join(outputs[-3:]) or "fertig"}],
        )
        log(f"follow-up: {', '.join(c['name'] for c in follow) or 'keine'}")
        _run_calls(rt, rest, resolve_calls(rest, follow), context, log, confirm, outputs, seen, saw_web)
    return {"decision": "done", "text": "\n".join(outputs)}
