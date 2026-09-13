"""Model layer: Needle 2 tool definitions, Gemma 4 E2B via cactus serve,
minimal orchestration loop (normalize -> complete -> resolve -> verify -> execute)
with a structured trace for every step. No agent framework."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from collections import deque
from typing import Literal

import needle

from . import calendar as cal

CACTUS_BASE_URL = os.environ.get("CACTUS_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
# Confidence policy (plan §20). Measured on base needle2 (NOT universally valid):
# grammar-valid calls score ~0.0-0.9, off-topic refusals (empty call) score ~0.99.
# The signal does not separate good from bad calls on this domain, so the floor
# defaults to 0 (execute any grammar-valid call) and stays configurable.
CONFIDENCE_THRESHOLD = float(os.environ.get("NEEDLE_CONFIDENCE_THRESHOLD", "0.3"))
CONFIDENCE_FLOOR = float(os.environ.get("NEEDLE_CONFIDENCE_FLOOR", "0.0"))
MAX_REPAIRS = int(os.environ.get("NEEDLE_MAX_REPAIRS", "3"))
WRITE_TOOLS = {"calendar_create", "calendar_move", "calendar_delete"}


def system_facts() -> str:
    n = cal.now()
    return f"date: {n:%Y-%m-%d %a %H:%M}; locale: de-DE; device: raspberry-pi"


class Gemma:
    """Gemma 4 E2B through the already-running `cactus serve` (OpenAI-compatible)."""

    def __init__(self, base_url: str = CACTUS_BASE_URL):
        self.base = base_url.rstrip("/")
        self._model: str | None = None
        self.error: str | None = None

    def available(self) -> bool:
        try:
            self.model_id()
            self.error = None
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def model_id(self) -> str:
        if self._model is None:
            with urllib.request.urlopen(self.base + "/models", timeout=5) as r:
                models = json.loads(r.read().decode()).get("data") or []
            if not models:
                raise RuntimeError("cactus serve has no model loaded")
            self._model = models[0]["id"]
        return self._model

    def chat(self, system: str, user: str, max_tokens: int = 200) -> str:
        payload = {"model": self.model_id(), "temperature": 0.0,
                   "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": system},
                                {"role": "user", "content": user}]}
        req = urllib.request.Request(
            self.base + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
        return data["choices"][0]["message"]["content"].strip().strip('"')

    def canonicalize(self, text: str) -> str:
        return self.chat(CANON_SYSTEM, text)

    def repair(self, instruction: str, failures: list[str]) -> str:
        msg = ("Original instruction: " + instruction
               + "\nIt failed verification:\n- " + "\n- ".join(failures)
               + "\nWrite the corrected canonical English instruction.")
        return self.chat(CANON_SYSTEM, msg)

    def respond(self, user_text: str, result: str) -> str:
        return self.chat(
            "Du bist ein lokaler Kalender-Assistent. Antworte auf Deutsch in einem "
            "kurzen Satz auf Basis des Tool-Ergebnisses. Erfinde nichts.",
            f"Nutzer: {user_text}\nTool-Ergebnis:\n{result}", max_tokens=120)


CANON_SYSTEM = """You rewrite a user's calendar request as ONE short canonical English instruction.

Rules:
- Output ONLY the instruction. No explanations, no quotes.
- Preserve the requested ACTION exactly (create, list, move, delete, find free
  slots). 'loeschen'/'entfernen'/'raus' means DELETE, never create.
- Keep person names and entry titles EXACTLY as written in the input (never translate them).
- Keep relative dates ('tomorrow', 'day after tomorrow', 'next friday')
  and times ('14:00', 'tomorrow afternoon') exactly as given. Never compute dates.
- If the request has no calendar intent (create, list, move, delete, free slots),
  output exactly: OFF_TOPIC

Examples:
Input: Pack mir morgen Nachmittag Zahnarzt rein.
Output: Create a Zahnarzt appointment tomorrow afternoon.
Input: Schieb das Meeting mit Lisa lieber auf übermorgen.
Output: Move the existing Meeting with Lisa to the day after tomorrow.
Input: Ich bin vom 3. bis 18. August komplett weg.
Output: I am on vacation from August 3 to August 18.
Input: Nimm den Zahnarzttermin wieder raus.
Output: Delete the Zahnarzttermin.
Input: Termin Hotel-Checkin löschen
Output: Delete the Hotel-Checkin appointment.
Input: Wann haben Lisa und Max gemeinsam Zeit?
Output: Find a free slot for Lisa and Max.
Input: Wie wird das Wetter morgen?
Output: OFF_TOPIC"""


# ------------------------------------------------------------- execution layer

def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _describe(ev: cal.CalendarEvent) -> str:
    year = f"{ev.start.year}" if ev.start.year != cal.now().year else ""
    if ev.all_day:
        last = ev.end.date() - cal.timedelta(days=1)
        span = (f"{cal._fmt_day(ev.start)}{year} – "
                f"{cal._fmt_day(cal.datetime.combine(last, cal.time()))}{year}"
                if last > ev.start.date() else f"{cal._fmt_day(ev.start)}{year}")
        return f"{ev.title} ({span}, ganztägig)"
    return (f"{ev.title} ({cal._fmt_day(ev.start)}{year} "
            f"{cal._fmt_time(ev.start)}–{cal._fmt_time(ev.end)})")


def _fix_text_date(context: str, day: cal.date) -> cal.date:
    """Deterministic post-choice fix (plan: Python computes, not the model):
    an explicit date in the request text wins over the model's computed date,
    because the model regularly miscalculates dates."""
    text_date = cal.extract_date_from_text(context or "")
    if text_date is not None and (text_date.month, text_date.day) != (day.month, day.day):
        return text_date
    return day


def execute_call(store: cal.CalendarStore, name: str, args: dict,
                 context: str = "") -> dict:
    """Resolve symbolic args -> verify -> execute. One structured result per call.
    `context` (canonical/original text) lets Python correct the model's dates."""
    args = {k: v for k, v in (args or {}).items() if v not in ("", None)}
    handlers = {"calendar_create": _do_create, "calendar_move": _do_move,
                "calendar_delete": _do_delete, "calendar_list": _do_list,
                "calendar_find_slot": _do_find_slot}
    handler = handlers.get(name)
    if handler is None:
        return {"ok": False, "message": f"Unbekanntes Tool: {name}",
                "checks": [], "resolved": {}}
    try:
        return handler(store, args, context)
    except Exception as exc:
        return {"ok": False, "message": f"❌ {type(exc).__name__}: {exc}",
                "checks": [], "resolved": {}}


def _do_create(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    checks: list = []
    title = str(args.get("title", "")).strip()
    checks.append({"check": "Titel", "ok": bool(title), "value": title or "fehlt"})
    if not title:
        return {"ok": False, "checks": checks, "resolved": {},
                "message": "❌ Kein Titel angegeben."}
    timing = cal.resolve_timing(
        str(args.get("date", "")), str(args.get("time", "")),
        str(args.get("until", "")),
        _to_int(args.get("duration_min"), cal.DEFAULT_DURATION_MIN))
    if timing is None:
        return {"ok": False, "checks": checks, "resolved": {},
                "message": (f"❌ Zeitangabe nicht verstanden: "
                            f"{args.get('date')!r} {args.get('time') or ''}".strip())}
    start, end, all_day = timing
    fixed = _fix_text_date(context, start.date())
    if fixed != start.date():
        start = cal.datetime.combine(fixed, start.time())
        end = start + (end - start)
        checks.append({"check": "Datum aus Text korrigiert", "ok": True,
                       "value": f"{start:%d.%m.%Y}"})
    checks.append({"check": "Zeit aufgelöst", "ok": True,
                   "value": f"{start:%d.%m.%Y %H:%M} – {end:%d.%m.%Y %H:%M}"})
    ev = cal.CalendarEvent(
        title=title, kind="absence" if all_day else "appointment",
        start=start, end=end, all_day=all_day,
        participants=cal.parse_persons(args.get("participants", "")))
    clash = store.collision(ev)
    checks.append({"check": "Kollision", "ok": clash is None,
                   "value": clash.title if clash else "keine"})
    if clash:
        return {"ok": False, "checks": checks, "resolved": ev.model_dump(mode="json"),
                "message": (f"⚠️ Kollision: '{clash.title}' belegt bereits "
                            f"{cal._fmt_day(clash.start)} {cal._fmt_time(clash.start)}. "
                            "Anderen Zeitpunkt wählen?")}
    ev = store.add(ev)
    label = "🚫 Absence eingetragen" if all_day else "✅ Erstellt"
    note = ""
    if start.date() < cal.now().date():
        note = " ⚠️ Datum lag in der Vergangenheit — aufs Folgejahr gerollt."
    return {"ok": True, "checks": checks, "resolved": ev.model_dump(mode="json"),
            "message": f"{label}: {_describe(ev)}{note}"}


def _do_move(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    checks: list = []
    ev = store.find_by_title(str(args.get("title", "")))
    checks.append({"check": "Eintrag gefunden", "ok": ev is not None,
                   "value": ev.title if ev else "-"})
    if ev is None:
        return {"ok": False, "checks": checks, "resolved": {},
                "message": f"❌ Kein Eintrag '{args.get('title')}' gefunden."}
    date_expr = str(args.get("date", "")).strip()
    time_expr = str(args.get("time", "")).strip()
    new_day = cal.resolve_date(date_expr) if date_expr else None
    new_time = cal.resolve_time(time_expr) if time_expr else None
    if new_day is None and new_time is None:
        return {"ok": False, "checks": checks, "resolved": {},
                "message": "❌ Kein neues Datum/keine neue Zeit erkannt."}
    if new_day is not None:
        new_day = _fix_text_date(context, new_day)
    if ev.all_day:
        moved = cal.move_event(store, ev, cal.datetime.combine(
            new_day or ev.start.date(), cal.time(0, 0)))
    else:
        moved = cal.move_event(store, ev, cal.datetime.combine(
            new_day or ev.start.date(), new_time or ev.start.time()))
    return {"ok": True, "checks": checks, "resolved": moved.model_dump(mode="json"),
            "message": f"✏️ Verschoben: {_describe(moved)}"}


def _do_delete(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    checks: list = []
    date_expr = str(args.get("date", "")).strip()
    near = cal.resolve_date(date_expr) if date_expr else None
    ev = store.find_by_title(str(args.get("title", "")), near=near)
    checks.append({"check": "Eintrag gefunden", "ok": ev is not None,
                   "value": ev.title if ev else "-"})
    if ev is None:
        return {"ok": False, "checks": checks, "resolved": {},
                "message": f"❌ Kein Eintrag '{args.get('title')}' gefunden."}
    cal.delete_event(store, ev.id)
    return {"ok": True, "checks": checks, "resolved": ev.model_dump(mode="json"),
            "message": f"🗑️ Gelöscht: {_describe(ev)}"}


def _do_list(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    horizon = str(args.get("horizon", "week")).lower()
    days = {"today": 1, "week": 7, "month": 31}.get(horizon, 7)
    person = str(args.get("person", "")).strip()
    who = cal._canonical_person(person) if person else "Ich"
    events = store.events_between(cal.now(), cal.now() + cal.timedelta(days=days),
                                  person=who)
    return {"ok": True, "checks": [], "resolved": {"person": who, "days": days},
            "message": cal.render_events(events)}


def _do_find_slot(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    raw = str(args.get("persons", "")).strip()
    if not raw:
        return {"ok": False, "checks": [], "resolved": {},
                "message": "❌ Keine Personen angegeben."}
    persons = cal.parse_persons(raw)
    duration = max(5, _to_int(args.get("duration_min"), 60))
    date_expr = str(args.get("date", "")).strip()
    if date_expr:
        day = cal.resolve_date(cal.strip_period(date_expr.lower()).strip())
        if day is None:
            return {"ok": False, "checks": [], "resolved": {},
                    "message": f"❌ Datum nicht verstanden: {date_expr!r}"}
        first = last = day
    else:
        first = cal.now().date()
        last = first + cal.timedelta(days=max(1, _to_int(args.get("days"), 7)) - 1)
    period = cal._period_of(date_expr)
    work_start, work_end = cal.PERIOD_WINDOWS.get(
        period, (cal.WORK_START, cal.WORK_END))
    slots = cal.find_free_slots(store, persons, first, last, duration,
                                work_start, work_end)
    return {"ok": True, "checks": [],
            "resolved": {"persons": persons, "day": str(first)},
            "message": cal.render_slots(slots)}


# -------------------------------------------------------------- Needle toolset

def build_tools(store: cal.CalendarStore) -> dict:
    """The five Needle tools bound to one store; bodies share execute_call."""

    @needle.tool
    def calendar_list(person: str = "",
                      horizon: Literal["today", "week", "month"] = "week") -> str:
        """List upcoming calendar entries, optionally for one person.

        Args:
            person: participant name to filter by, empty for the user themself
            horizon: 'today', 'week' or 'month'
        """
        return execute_call(store, "calendar_list",
                            {"person": person, "horizon": horizon})["message"]

    @needle.tool
    def calendar_find_slot(persons: str, duration_min: int = 60,
                           date: str = "", days: int = 7) -> str:
        """Find common free time slots for a group of persons.

        Args:
            persons: comma-separated participant names
            duration_min: minimum slot length in minutes
            date: optional single day like 'tomorrow afternoon'
            days: search horizon in days when no date is given
        """
        return execute_call(store, "calendar_find_slot", {
            "persons": persons, "duration_min": duration_min,
            "date": date, "days": days})["message"]

    @needle.tool
    def calendar_create(title: str, date: str = "", until: str = "",
                        time: str = "", participants: str = "") -> str:
        """Create a calendar entry or all-day absence (vacation, trip).

        Args:
            title: short entry title
            date: first day like 'tomorrow' or 'august 3'
            until: last day for multi-day absences like 'august 18'; empty for single day
            time: time of day like '14:00'; empty for all-day absences
            participants: comma-separated participant names
        """
        return execute_call(store, "calendar_create", {
            "title": title, "date": date, "until": until,
            "time": time, "participants": participants})["message"]

    @needle.tool
    def calendar_move(title: str, date: str = "", time: str = "") -> str:
        """Move an existing entry to another day and/or time.

        Args:
            title: title of the existing entry to move
            date: new day like 'friday' or 'day after tomorrow'
            time: new time like '15:00'; empty keeps the current time
        """
        return execute_call(store, "calendar_move", {
            "title": title, "date": date, "time": time})["message"]

    @needle.tool
    def calendar_delete(title: str, date: str = "") -> str:
        """Delete an existing calendar entry.

        Args:
            title: title of the entry to delete
            date: optional day hint to pick the right entry
        """
        return execute_call(store, "calendar_delete", {
            "title": title, "date": date})["message"]

    return {fn.__name__: fn for fn in
            (calendar_list, calendar_find_slot, calendar_create,
             calendar_move, calendar_delete)}


# ------------------------------------------------------------------ orchestrator

class Agent:
    """One Needle session (5 tools) + optional Gemma normalization/repair.
    handle() is a generator yielding the trace dict after every step."""

    def __init__(self, store: cal.CalendarStore, mode: str = "hybrid"):
        self.store = store
        self.mode = mode
        self.tools = build_tools(store)
        self.gemma = Gemma() if mode == "hybrid" else None
        self.needle = needle.Needle(tools=list(self.tools.values()),
                                    system=system_facts())
        self._facts_key = system_facts()
        self.history: deque = deque(maxlen=10)

    def _step(self, trace: dict, name: str, fn, *args):
        t0 = time.perf_counter()
        try:
            out = fn(*args)
            error = None
        except Exception as exc:
            out, error = None, f"{type(exc).__name__}: {exc}"
        trace["steps"].append({
            "name": name, "input": [repr(a)[:120] for a in args],
            "output": out, "error": error,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
        return out

    def _ask_needle(self, trace: dict, text: str) -> dict:
        facts = system_facts()
        if facts != self._facts_key:
            self._facts_key = facts
            self.needle = needle.Needle(tools=list(self.tools.values()),
                                        system=facts)
        self.needle.reset()  # each request is independent; keep tools loaded
        text = (text or "").strip().rstrip(".!?;:,")  # trailing periods cause refusals
        return self._step(trace, "needle_complete", self.needle.complete, text) or {}

    def _resolve(self, name: str, args: dict) -> dict:
        """Deterministic pre-resolution of symbolic args (trace + verification)."""
        if name == "calendar_create":
            timing = cal.resolve_timing(
                str(args.get("date", "")), str(args.get("time", "")),
                str(args.get("until", "")),
                _to_int(args.get("duration_min"), cal.DEFAULT_DURATION_MIN))
            return {"timing": (None if timing is None else
                               [str(timing[0]), str(timing[1]), timing[2]]),
                    "participants": cal.parse_persons(args.get("participants", ""))}
        return {}

    @staticmethod
    def _merge_calls(calls: list) -> list:
        """Dedup repeated calls and merge consecutive calendar_find_slot calls
        (the engine often emits one call per person) into a single intersection."""
        out: list = []
        pending: list = []
        seen: set = set()
        for call in calls[:3]:
            name = call.get("name", "")
            args = call.get("arguments") or {}
            key = (name, tuple(sorted((args or {}).items())))
            if key in seen:
                continue
            seen.add(key)
            if name == "calendar_find_slot":
                pending.append(str(args.get("persons", "")))
                continue
            if pending:
                out.append({"name": "calendar_find_slot",
                            "arguments": {"persons": ", ".join(pending)}})
                pending = []
            out.append(call)
        if pending:
            out.append({"name": "calendar_find_slot",
                        "arguments": {"persons": ", ".join(pending)}})
        return out

    def _clarify(self, trace: dict, why: str) -> None:
        trace["result"] = (why + " Bitte formuliere den Wunsch konkret: Termin "
                           "anlegen/verschieben/löschen, Urlaub, Liste, freie Slots.")

    def handle(self, user_text: str):
        trace = {"input": user_text, "mode": self.mode, "canonical": None,
                 "steps": [], "result": "", "executed": False,
                 "confidence": None, "total_ms": None, "done": False}
        yield trace
        text = user_text
        if self.gemma is not None:
            if not self.gemma.available():
                trace["steps"][-1]["detail"] = (
                    f"Gemma nicht erreichbar ({self.gemma.error}) — Needle direkt")
            else:
                canon = self._step(trace, "gemma_normalize",
                                   self.gemma.canonicalize, user_text)
                if canon:
                    text = canon
                    trace["canonical"] = canon
                yield trace
                if canon and canon.strip().upper().startswith("OFF_TOPIC"):
                    trace["result"] = ("Das gehört nicht zum Kalender — frag mich gern "
                                       "nach Terminen, Urlaub oder freien Slots.")
                    self._finish(trace)
                    yield trace
                    return
        resp = self._ask_needle(trace, text)
        calls = resp.get("function_calls") or []
        conf = resp.get("confidence")
        trace["confidence"] = conf
        trace["ungrounded"] = (resp.get("validation") or {}).get("ungrounded") or []
        if not calls:
            calls = self._repair_loop(trace, text, ["no matching tool call (refusal)"])
        elif conf is not None and conf < CONFIDENCE_FLOOR:
            calls = self._repair_loop(trace, text,
                                      [f"very low confidence ({conf})"])
        if calls:
            yield trace
            yield from self._execute_steps(trace, calls, text)
            if not trace["executed"]:
                # plan §24: verifier failure -> Gemma receives the exact failure
                calls = self._repair_loop(trace, text,
                                          trace.get("failures") or ["execute failed"])
                if calls:
                    yield from self._execute_steps(trace, calls, text)
        self._finish(trace)
        yield trace

    def _execute_steps(self, trace: dict, calls: list, text: str = ""):
        messages = []
        failures = []
        for call in self._merge_calls(calls)[:3]:
            name = call.get("name", "")
            args = call.get("arguments") or {}
            resolved = self._resolve(name, args)
            if resolved:
                self._step(trace, f"resolve {name}", lambda r=resolved: r)
                yield trace
            out = self._step(trace, f"execute {name}",
                             execute_call, self.store, name, args, text)
            trace["executed"] = trace["executed"] or bool(out and out.get("ok"))
            messages.append(out["message"] if out else "❌ Execute fehlgeschlagen")
            if out is None or not out.get("ok"):
                failures.append(out["message"] if out else "Execute fehlgeschlagen")
            yield trace
        trace["result"] = "\n".join(messages)
        trace["failures"] = failures

    def _repair_loop(self, trace: dict, text: str, failures: list) -> list | None:
        """Gemma receives the exact failure -> new canonical instruction -> Needle."""
        for attempt in range(1, MAX_REPAIRS + 1):
            if self.gemma is None or not self.gemma.available():
                self._clarify(trace, "Ungültig/unsicher: " + "; ".join(failures))
                return None
            new_text = self._step(trace, f"repair {attempt}",
                                  self.gemma.repair, text, failures)
            resp = self._ask_needle(trace, new_text)
            calls = resp.get("function_calls") or []
            if calls:
                trace["confidence"] = resp.get("confidence")
                return calls
            failures = [f"leere Needle-Antwort (confidence={resp.get('confidence')})"]
        self._clarify(trace, "Repair nach 3 Versuchen nicht erfolgreich.")
        return None

    def _finish(self, trace: dict) -> None:
        import resource
        trace["total_ms"] = round(sum(s.get("latency_ms", 0)
                                      for s in trace["steps"]), 1)
        trace["ram_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        trace["done"] = True
        self.history.append(trace)


# --------------------------------------------------- extraction schemas (lab)

from pydantic import BaseModel  # noqa: E402


class TemporalExpression(BaseModel):
    """A temporal reference found in text."""
    date_expression: str | None = None
    time_expression: str | None = None
    period: Literal["morning", "afternoon", "evening", "night", "all_day"] | None = None
    end_date_expression: str | None = None


class ParticipantExpression(BaseModel):
    """People involved in a calendar request."""
    participants: list[str] = []


class EventExpression(BaseModel):
    """A calendar entry described by text."""
    title: str
    kind: Literal["appointment", "absence"] = "appointment"
    participants: list[str] = []
