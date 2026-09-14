"""Model layer: Needle 2 tool definitions, Gemma 4 E2B via cactus serve,
minimal orchestration loop (normalize -> complete -> resolve -> verify -> execute)
with a structured trace for every step. No agent framework."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from collections import deque
from typing import Literal

import needle
from pydantic import BaseModel

from . import calendar as cal

CACTUS_BASE_URL = os.environ.get("CACTUS_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")
# Confidence policy (plan §20). Measured on base needle2 (NOT universally valid):
# grammar-valid calls score ~0.0-0.9, off-topic refusals (empty call) score ~0.99.
# The signal does not separate good from bad calls on this domain, so the floor
# defaults to 0 (execute any grammar-valid call) and stays configurable.
CONFIDENCE_THRESHOLD = float(os.environ.get("NEEDLE_CONFIDENCE_THRESHOLD", "0.3"))
CONFIDENCE_FLOOR = float(os.environ.get("NEEDLE_CONFIDENCE_FLOOR", "0.0"))
MAX_TOOL_CALLS_PER_STEP = int(os.environ.get("NEEDLE_MAX_TOOL_CALLS", "10"))
MAX_AGENT_STEPS = int(os.environ.get("NEEDLE_MAX_AGENT_STEPS", "8"))  # Phase 9 safety ceiling
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

    def decide(self, state: dict) -> ControllerDecision | None:
        """Phase 6/8: Gemma observes and decides continue/ask_user/finish —
        no tool names, no arguments, just the next semantic step."""
        iteration = state.get("iteration", 1)
        user = f"User goal: {state.get('goal', '')} Iteration {iteration}"
        completed = state.get("completed") or []
        if completed:  # plan §5.13: the controller always sees what is done
            user += "\n\nCompleted:\n" + "\n".join(completed)
        if state.get("pending_question"):
            user += f"\nPending question to the user: {state['pending_question']}"
        obs = "\n".join(f"- {o}" for o in state.get("observations", [])[-4:])
        if obs:
            user += f"\n\nObservations:\n{obs}"
        raw = self.chat(CONTROLLER_SYSTEM, user, max_tokens=240)
        return parse_controller_decision(raw)

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

CONTROLLER_SYSTEM = """You coordinate a calendar agent. Decide the next step.

Respond ONLY with JSON, nothing else:
{"action": "continue" | "ask_user" | "finish", "instruction": "...", "message": "..."}

You always receive "Iteration" in the input.

FIRST STEP (no observations yet): your job is to turn the goal into the FIRST
canonical English instruction for the tool model.
- action=continue, instruction = the first operation as ONE short canonical
  English sentence. Keep titles, person names, relative dates and times exactly
  as written (or exactly as they appear in the calendar). Never compute dates.
- If the goal contains SEVERAL operations, output only the FIRST one.
- Never ask_user in the first step. Never finish in the first step.
- finish only for requests with no calendar intent at all (off-topic).

FOLLOW-UP STEPS (observations exist): read the observations carefully.
- If the goal has remaining operations: action=continue with the NEXT operation
  as ONE canonical English instruction (titles exactly as written).
- If the result is ambiguous (several entries match and the goal does not say
  which one): action=ask_user with ONE short German question.
- If everything is done: action=finish with a short German confirmation.
- If a step failed: read the calendar entries in the observation and refer to
  an existing entry exactly as written. If nothing matches, ask_user.

Examples:
Goal: "Termin Zahnarzt morgen um 14 Uhr" Iteration 1
{"action": "continue", "instruction": "Create the Zahnarzt appointment tomorrow at 14:00.", "message": ""}

Goal: "Trag am 17.9. um 10 Uhr Zahnarzt ein, um 13 Uhr Meeting bis 16 Uhr und am 10.10. um 9 Uhr TÜV." Iteration 1
{"action": "continue", "instruction": "Create the Zahnarzt appointment on September 17 at 10:00.", "message": ""}

Goal: (same) Iteration 2
Observation: "created: Zahnarzt (17.09. 10:00)"
{"action": "continue", "instruction": "Create the Meeting appointment on September 17 from 13:00 to 16:00.", "message": ""}

Goal: (same) Iteration 4
Observation: "created: TUEV (10.10. 09:00)"
{"action": "finish", "instruction": "", "message": "Alle drei Termine wurden eingetragen."}

Goal: "Verschieb das Meeting mit Lisa." Iteration 2
Observation: "calendar_list found: Meeting Lisa Di 18.09. 10:00; Meeting Lisa Do 20.09. 14:00"
{"action": "ask_user", "instruction": "", "message": "Welches Meeting mit Lisa meinst du - das am Dienstag oder das am Donnerstag?"}

Goal: "Wie wird das Wetter morgen?" Iteration 1
{"action": "finish", "instruction": "", "message": "Das gehört nicht zum Kalender."}"""


class ControllerDecision(BaseModel):
    """Gemma's allowed controller vocabulary (plan §6): no tool names, no arguments."""
    action: Literal["continue", "ask_user", "finish"]
    instruction: str = ""
    message: str = ""


def parse_controller_decision(raw: str) -> ControllerDecision | None:
    """Extract the JSON decision from Gemma's reply; None when unparseable."""
    if not raw:
        return None
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    action = data.get("action")
    if action not in ("continue", "ask_user", "finish"):
        return None
    return ControllerDecision(action=action,
                              instruction=str(data.get("instruction") or ""),
                              message=str(data.get("message") or ""))


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


def _fix_text_date(context: str, day: cal.date, roll: bool = True) -> cal.date:
    """Deterministic post-choice fix (plan: Python computes, not the model):
    an explicit date in the request text wins over the model's computed date,
    because the model regularly miscalculates dates. roll=True applies the
    past->next-year rule (create); matching existing entries uses roll=False."""
    text_date = cal.extract_date_from_text(context or "", roll=roll)
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
    # explicit time range (plan: date+time+end_time -> explicit interval,
    # end_time is verified, no silent corrections)
    end_time_expr = str(args.get("end_time", "")).strip()
    if end_time_expr and not all_day:
        t_end = cal.resolve_time(end_time_expr)
        if t_end is None:
            return {"ok": False, "checks": checks, "resolved": {},
                    "message": f"❌ Endzeit nicht verstanden: {end_time_expr!r}"}
        end = cal.datetime.combine(start.date(), t_end)
        if end <= start:
            return {"ok": False, "checks": checks, "resolved": {},
                    "message": "❌ Endzeit muss nach der Startzeit liegen."}
        checks.append({"check": "Endzeit", "ok": True,
                       "value": f"{start:%H:%M}–{end:%H:%M}"})
    # Deterministic corrections (plan: Python computes, not the model).
    # Authority: explicit dates in the text > named weekday > model output.
    # No time signal in the text -> all-day (the model's invented time is dropped).
    signal = cal.has_time_signal(context) if context else True
    text_dates = cal.extract_dates_from_text(context, roll=True)
    wd = cal.extract_weekday_from_text(context)
    if text_dates:
        first, last = text_dates[0], text_dates[-1]
        if not signal:
            if first != last:
                timing2 = cal.resolve_timing(
                    f"{first:%d.%m.%Y}", "", f"{last:%d.%m.%Y}",
                    _to_int(args.get("duration_min"), cal.DEFAULT_DURATION_MIN))
                if timing2 is not None:
                    start, end, all_day = timing2
            else:
                duration = end - start
                start = cal.datetime.combine(first, cal.time(0, 0))
                end = start + duration
            all_day = True
            checks.append({"check": "Zeit aus Text", "ok": True,
                           "value": (f"{start:%d.%m.} – {end - cal.timedelta(days=1):%d.%m.},"
                                     " ganztägig" if first != last
                                     else f"{start:%d.%m.}, ganztägig")})
        elif (first.month, first.day) != (start.month, start.day):
            duration = end - start  # duration first: start must not shift `end`
            start = cal.datetime.combine(first, start.time())
            end = start + duration
            checks.append({"check": "Datum aus Text korrigiert", "ok": True,
                           "value": f"{start:%d.%m.%Y}"})
    elif wd is not None and (wd.month, wd.day) != (start.month, start.day):
        duration = end - start
        start = cal.datetime.combine(wd, start.time())
        end = start + duration
        checks.append({"check": "Wochentag aus Text", "ok": True,
                       "value": f"{wd:%d.%m.%Y}"})
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
        reason = ("Abwesenheit blockiert diesen Termin"
                  if clash.kind == "absence"
                  else f"belegt bereits {cal._fmt_day(clash.start)} "
                       f"{cal._fmt_time(clash.start)}")
        return {"ok": False, "checks": checks, "resolved": ev.model_dump(mode="json"),
                "message": (f"⚠️ Kollision: '{clash.title}' {reason}. "
                            "Anderen Zeitpunkt wählen?")}
    ev = store.add(ev)
    label = "🚫 Absence eingetragen" if all_day else "✅ Erstellt"
    note = ""
    if start.date() < cal.now().date():
        note = " ⚠️ Datum liegt in der Vergangenheit."
    return {"ok": True, "checks": checks, "resolved": ev.model_dump(mode="json"),
            "message": f"{label}: {_describe(ev)}{note}"}


def _do_move(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    checks: list = []
    date_expr = str(args.get("date", "")).strip()
    time_expr = str(args.get("time", "")).strip()
    new_day = cal.resolve_date(date_expr, roll=False) if date_expr else None
    new_time = cal.resolve_time(time_expr) if time_expr else None
    if new_day is None and new_time is None:
        return {"ok": False, "checks": checks, "resolved": {},
                "message": "❌ Kein neues Datum/keine neue Zeit erkannt."}
    # Deterministic target fix (plan: Python computes): 'Move X on A to B' — the
    # model often repeats the source day A and/or reads the target day as a time.
    # The LAST explicit date in the text is the target; an explicit time in the
    # text wins, no time in the text keeps the event's current time.
    if context:
        dates = cal.extract_dates_from_text(context, roll=False)
        if dates:
            if new_day is not None:
                if new_day != dates[-1] and new_day in dates:
                    new_day = dates[-1]
                elif len(dates) == 1:
                    new_day = dates[-1]
            else:
                new_day = dates[-1]
            checks.append({"check": "Zieldatum aus Text", "ok": True,
                           "value": f"{new_day:%d.%m.%Y}"})
        text_time = cal.extract_time_from_text(context)
        if text_time is not None:
            new_time = text_time
            checks.append({"check": "Zeit aus Text", "ok": True,
                           "value": f"{text_time:%H:%M}"})
        else:
            new_time = None
    # Source resolution with candidates: ambiguity must ask, never guess (plan §2)
    candidates = store.find_candidates(str(args.get("title", "")))
    if not candidates:
        return {"ok": False, "checks": checks, "resolved": {},
                "message": f"❌ Kein Eintrag '{args.get('title')}' gefunden."}
    if len(candidates) > 1:
        listing = "; ".join(_describe(c) for c in candidates[:5])
        return {"ok": False, "checks": checks, "resolved": {},
                "message": (f"⚠️ Mehrere Einträge '{args.get('title')}' — "
                            f"bitte präzisieren: {listing}")}
    ev = candidates[0]
    checks.append({"check": "Eintrag gefunden", "ok": True, "value": ev.title})
    if new_day is None and new_time is None:
        return {"ok": False, "checks": checks, "resolved": {},
                "message": "❌ Kein neues Datum/keine neue Zeit erkannt."}
    # Phase 1.1: build the candidate first — a move never writes through a
    # collision and never silently blocks itself on its own old slot.
    if ev.all_day:
        new_start = cal.datetime.combine(new_day or ev.start.date(), cal.time(0, 0))
    else:
        new_start = cal.datetime.combine(new_day or ev.start.date(),
                                         new_time or ev.start.time())
    if ev.all_day:
        days = (ev.end.date() - ev.start.date()).days
        new_end = new_start + cal.timedelta(days=max(days, 1))
    else:
        new_end = new_start + (ev.end - ev.start)
    candidate = ev.model_copy(update={"start": new_start, "end": new_end})
    clash = store.collision(candidate)
    checks.append({"check": "Kollision", "ok": clash is None,
                   "value": clash.title if clash else "keine"})
    if clash:
        return {"ok": False, "checks": checks,
                "resolved": candidate.model_dump(mode="json"),
                "message": (f"⚠️ Kollision: '{clash.title}' belegt bereits "
                            f"{cal._fmt_day(clash.start)} {cal._fmt_time(clash.start)}. "
                            "Nicht verschoben.")}
    moved = cal.move_event(store, ev, new_start, new_end)
    return {"ok": True, "checks": checks, "resolved": moved.model_dump(mode="json"),
            "message": f"✏️ Verschoben: {_describe(moved)}"}


def _do_delete(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    checks: list = []
    date_expr = str(args.get("date", "")).strip()
    near = cal.resolve_date(date_expr, roll=False) if date_expr else None
    if near is None and context:
        # the model computes the hint date itself and gets it wrong; an explicit
        # date in the request text is authoritative (no roll: existing entry)
        near = cal.extract_date_from_text(context, roll=False)
    # Phase 1.2/1.3: candidates instead of silent pick; the date hint is a HARD
    # filter — 'Lösch Meeting am 12.9.' must never delete the 17.9. meeting.
    candidates = store.find_candidates(str(args.get("title", "")), near)
    # Event resolution fallback (plan §2 Fall 1): a generic title that matches
    # nothing but carries an explicit date/time resolves through the time
    # window — deterministic, no NLU ('Lösche den Termin am 15.9. 10 Uhr').
    if not candidates and near is not None:
        t_time = cal.extract_time_from_text(date_expr) \
            or (cal.extract_time_from_text(context) if context else None)
        window_candidates = store.find_by_window(near, t_time)
        if len(window_candidates) == 1 or t_time is not None:
            candidates = window_candidates
            checks.append({"check": "Titel ohne Treffer — Zeitfenster", "ok": True,
                           "value": f"{near:%d.%m.}" +
                                    (f" {t_time:%H:%M}" if t_time else "")})
    if not candidates:
        if near is not None:
            return {"ok": False, "checks": checks, "resolved": {},
                    "message": f"❌ Kein Eintrag '{args.get('title')}' am "
                               f"{near:%d.%m.%Y} gefunden."}
        return {"ok": False, "checks": checks, "resolved": {},
                "message": f"❌ Kein Eintrag '{args.get('title')}' gefunden."}
    if len(candidates) > 1:
        listing = "; ".join(_describe(c) for c in candidates[:5])
        return {"ok": False, "checks": checks, "resolved": {},
                "message": (f"⚠️ Mehrere Einträge '{args.get('title')}' — "
                            f"bitte präzisieren: {listing}")}
    ev = candidates[0]
    checks.append({"check": "Eintrag gefunden", "ok": True, "value": ev.title})
    cal.delete_event(store, ev.id)
    return {"ok": True, "checks": checks, "resolved": ev.model_dump(mode="json"),
            "message": f"🗑️ Gelöscht: {_describe(ev)}"}


def _do_list(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    horizon = str(args.get("horizon", "week")).lower()
    days = {"today": 1, "week": 7, "month": 31}.get(horizon, 7)
    person = str(args.get("person", "")).strip()
    who = cal._canonical_person(person) if person else "Ich"
    date_expr = str(args.get("date", "")).strip()
    until_expr = str(args.get("until", "")).strip()
    if date_expr:
        # read queries allow the past as written (plan: no silent rolling)
        first = cal.resolve_date(date_expr, roll=False)
        if first is None:
            first = cal.extract_date_from_text(date_expr, roll=False)
        last = cal.resolve_date(until_expr, roll=False) if until_expr \
            else (cal.extract_date_from_text(until_expr, roll=False) if until_expr else None)
        if first is None:
            return {"ok": False, "checks": [], "resolved": {},
                    "message": f"❌ Datum nicht verstanden: {date_expr!r}"}
        if last is not None and last < first:
            return {"ok": False, "checks": [], "resolved": {},
                    "message": "❌ 'until' liegt vor dem Startdatum."}
        start = cal.datetime.combine(first, cal.time(0, 0))
        end = (cal.datetime.combine(last, cal.time(0, 0)) + cal.timedelta(days=1)
               if last else start + cal.timedelta(days=1))
        resolved = {"person": who, "range": f"{start:%d.%m.%Y} – {end:%d.%m.%Y}",
                    "first_day": f"{first:%Y-%m-%d}"}
    else:
        start = cal.now()
        end = start + cal.timedelta(days=days)
        resolved = {"person": who, "days": days}
    events = store.events_between(start, end, person=who)
    return {"ok": True, "checks": [], "resolved": resolved,
            "message": cal.render_events(events)}


def _do_find_slot(store: cal.CalendarStore, args: dict, context: str = "") -> dict:
    raw = str(args.get("persons", "")).strip()
    if not raw:
        return {"ok": False, "checks": [], "resolved": {},
                "message": "❌ Keine Personen angegeben."}
    persons = cal.parse_persons(raw)
    duration = max(5, _to_int(args.get("duration_min"), 60))
    date_expr = str(args.get("date", "")).strip()
    until_expr = str(args.get("until", "")).strip()
    # no roll: find-slot searches dates as written (past included)
    first = cal.resolve_date(date_expr, roll=False) if date_expr else None
    last = cal.resolve_date(until_expr, roll=False) if until_expr else None
    if date_expr and first is None:
        return {"ok": False, "checks": [], "resolved": {},
                "message": f"❌ Datum nicht verstanden: {date_expr!r}"}
    if until_expr and last is None:
        return {"ok": False, "checks": [], "resolved": {},
                "message": f"❌ Enddatum nicht verstanden: {until_expr!r}"}
    if first is None:
        first = cal.now().date()
        last = first + cal.timedelta(days=max(1, _to_int(args.get("days"), 7)) - 1)
    elif last is None:
        last = first
    period = cal._period_of(date_expr)
    work_start, work_end = cal.PERIOD_WINDOWS.get(
        period, (cal.WORK_START, cal.WORK_END))
    slots = cal.find_free_slots(store, persons, first, last, duration,
                                work_start, work_end)
    # Phase 6.6: the executed solver result is the truth — Telegram renders
    # exactly these data, it never re-schedules with different defaults.
    return {"ok": True, "checks": [],
            "resolved": {"persons": persons,
                         "first_day": f"{first:%Y-%m-%d}",
                         "last_day": f"{last:%Y-%m-%d}",
                         "duration_min": duration,
                         "window_start": f"{work_start:%H:%M}",
                         "window_end": f"{work_end:%H:%M}",
                         "slots": [[f"{s:%Y-%m-%d %H:%M}", f"{e:%Y-%m-%d %H:%M}"]
                                   for s, e in slots]},
            "message": cal.render_slots(slots)}


# -------------------------------------------------------------- Needle toolset

def build_tools(store: cal.CalendarStore) -> dict:
    """The five Needle tools bound to one store; bodies share execute_call."""

    @needle.tool
    def calendar_list(person: str = "", date: str = "", until: str = "",
                      horizon: Literal["today", "week", "month"] = "week") -> str:
        """List calendar entries for a person, a specific day or a date range.

        Args:
            person: participant name to filter by, empty for the user themself
            date: specific day like '7.9.' or 'September 1'; empty uses horizon
            until: last day of a range (inclusive) like 'September 7'
            horizon: 'today', 'week' or 'month' when no date is given
        """
        return execute_call(store, "calendar_list", {
            "person": person, "date": date, "until": until,
            "horizon": horizon})["message"]

    @needle.tool
    def calendar_find_slot(persons: str, duration_min: int = 60,
                           date: str = "", until: str = "", days: int = 7) -> str:
        """Find common free time slots for a group of persons.

        Args:
            persons: comma-separated participant names
            duration_min: minimum slot length in minutes
            date: first day like 'tomorrow afternoon' or 'September 20'
            until: last day of the search range like 'September 24'; empty with date means one day
            days: search horizon in days (from today) when no date is given
        """
        return execute_call(store, "calendar_find_slot", {
            "persons": persons, "duration_min": duration_min,
            "date": date, "until": until, "days": days})["message"]

    @needle.tool
    def calendar_create(title: str, date: str = "", until: str = "",
                        time: str = "", end_time: str = "",
                        participants: str = "") -> str:
        """Create a calendar entry or all-day absence (vacation, trip).

        Args:
            title: short entry title
            date: first day like 'tomorrow' or 'august 3'
            until: last day for multi-day absences like 'august 18'; empty for single day
            time: time of day like '14:00'; empty for all-day absences
            end_time: end time of day like '16:00' for explicit time ranges
            participants: comma-separated participant names
        """
        return execute_call(store, "calendar_create", {
            "title": title, "date": date, "until": until,
            "time": time, "end_time": end_time,
            "participants": participants})["message"]

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
        self.pending: dict[str, dict] = {}  # Phase 12, per session_id (plan §10)
        self.inference_lock = threading.Lock()  # plan §11: needle engine is
        # not reentrant — Telegram threads/Gradio callbacks serialize here

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
        with self.inference_lock:  # plan §11: one engine call at a time
            return self._step(trace, "needle_complete",
                              self.needle.complete, text) or {}

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
        (the engine often emits one call per person) into a single intersection.
        Only `persons` is merged; the other arguments survive from the first call."""
        out: list = []
        pending: list = []
        seen: set = set()

        def flush() -> None:
            if not pending:
                return
            merged = dict(pending[0])
            names: list[str] = []
            for a in pending:
                for p in str(a.get("persons", "")).split(","):
                    p = p.strip()
                    if p and p.lower() not in (n.lower() for n in names):
                        names.append(p)
            merged["persons"] = ", ".join(names)
            out.append({"name": "calendar_find_slot", "arguments": merged})
            pending.clear()

        for call in calls[:MAX_TOOL_CALLS_PER_STEP]:
            name = call.get("name", "")
            args = call.get("arguments") or {}
            key = (name, tuple(sorted((args or {}).items())))
            if key in seen:
                continue
            seen.add(key)
            if name == "calendar_find_slot":
                pending.append(args)
                continue
            flush()
            out.append(call)
        flush()
        return out

    def _clarify(self, trace: dict, why: str) -> None:
        trace["result"] = (why + " Bitte formuliere den Wunsch konkret: Termin "
                           "anlegen/verschieben/löschen, Urlaub, Liste, freie Slots.")

    def handle(self, user_text: str, session_id: str = "local"):
        trace = {"input": user_text, "mode": self.mode, "canonical": None,
                 "steps": [], "result": "", "executed": False,
                 "confidence": None, "total_ms": None, "done": False}
        yield trace
        state = {"goal": user_text, "observations": [], "completed": []}
        pending = self.pending.pop(session_id, None)
        resumed = bool(pending)
        if resumed:
            # Phase 12: the user answered a pending question -> continue the task
            state["goal"] = pending["goal"]
            state["observations"] = pending["observations"] + [
                f"user answered: {user_text!r}"]
            state["pending_question"] = pending["question"]
        controller = (self.gemma is not None and self.gemma.available())
        if self.gemma is not None and not self.gemma.available():
            detail = (f"Gemma nicht erreichbar ({self.gemma.error}) — Needle direkt")
            if trace["steps"]:
                trace["steps"][-1]["detail"] = detail
            else:
                trace["gemma_unavailable"] = detail
        if controller:
            # Phase 5/10: Gemma understands the goal and formulates the first
            # semantic step; multi-item requests are decomposed here, before Needle.
            yield from self._controller_loop(trace, state, last_instruction=None,
                                             session_id=session_id)
        else:
            if self.gemma is not None:
                trace["gemma_unavailable"] = (
                    f"Gemma nicht erreichbar ({self.gemma.error}) — Needle direkt")
            resp = self._ask_needle(trace, user_text)
            calls = resp.get("function_calls") or []
            trace["confidence"] = resp.get("confidence")
            trace["ungrounded"] = ((resp.get("validation") or {}).get("ungrounded")
                                   or [])
            if calls:
                yield trace
                yield from self._execute_steps(trace, calls, user_text)
            else:
                self._clarify(trace, "Kein Kalender-Befehl erkannt.")
        self._finish(trace)
        yield trace

    def _controller_loop(self, trace: dict, state: dict, last_instruction: str | None,
                         session_id: str = "local"):
        """Phase 5-12: Gemma decides continue/ask_user/finish after every result;
        every continue-instruction goes to Needle, which stays the only dispatcher."""
        last_obs = None
        for iteration in range(1, MAX_AGENT_STEPS + 1):
            state["iteration"] = iteration
            decision = self._step(trace, f"controller {iteration}",
                                  self.gemma.decide, state)
            yield trace
            if decision is None:
                self._clarify(trace, "Controller-Entscheidung nicht lesbar — "
                              + (trace["result"] or ""))
                return
            if decision.action == "finish":
                if decision.message:
                    trace["result"] = decision.message
                return
            if decision.action == "ask_user":
                trace["result"] = decision.message or trace["result"]
                self.pending[session_id] = {
                    "goal": state["goal"],
                    "observations": state["observations"][:],
                    "question": decision.message}
                return
            instruction = (decision.instruction or "").strip()
            if not instruction:
                self._clarify(trace, "Controller ohne Instruction — "
                              + (trace["result"] or "Bitte präzisieren."))
                return
            if self._same(instruction, last_instruction):
                self._clarify(trace, "Loop erkannt (identische Instruction) — "
                              + (trace["result"] or ""))
                return
            if any(self._same(instruction, done)
                   for done in state.get("completed_instructions", [])):
                # Phase 5.14: an already executed instruction is never re-planned
                self._clarify(trace, "Schritt bereits abgeschlossen — "
                              + (trace["result"] or ""))
                return
            last_instruction = instruction
            trace["canonical"] = instruction
            resp = self._ask_needle(trace, instruction)
            calls = resp.get("function_calls") or []
            trace["confidence"] = resp.get("confidence")
            if calls:
                yield from self._execute_steps(trace, calls, instruction)
                if trace["executed"]:
                    state["completed"].append(
                        f"- {trace['result'].splitlines()[0][:120]}")
                    state.setdefault("completed_instructions", []).append(
                        instruction)
            else:
                trace["result"] = "Kein Kalender-Befehl erkannt."
            obs = self._observation(trace, instruction)
            if self._same(obs, last_obs):
                self._clarify(trace, "Loop erkannt (identische Observation) — "
                              + (trace["result"] or ""))
                return
            last_obs = obs
            state["observations"].append(obs)
            yield trace
        self._clarify(trace, f"Abbruch nach {MAX_AGENT_STEPS} Agent-Schritten — "
                      + (trace["result"] or ""))

    @staticmethod
    def _same(a: str, b: str | None) -> bool:
        def norm(v):
            return re.sub(r"\W+", "", v or "").lower()
        return b is not None and norm(a) == norm(b)

    def _observation(self, trace: dict, instruction: str) -> str:
        """Compact observation for the controller (plan §8): result, failures
        and — on failures — the current calendar entries."""
        obs = f"instruction: {instruction}\nresult: {(trace.get('result') or '')[:400]}"
        if trace.get("failures"):
            obs += "\nfailures: " + "; ".join(trace["failures"][:3])
            obs += "\ncurrent calendar entries:\n" + self._calendar_context()
        return obs

    def _execute_steps(self, trace: dict, calls: list, text: str = ""):
        messages = []
        failures = []
        for call in self._merge_calls(calls)[:MAX_TOOL_CALLS_PER_STEP]:
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

    def _calendar_context(self) -> str:
        """The entries Gemma may inspect during repair/observation (plan §8/§24):
        the exact failure AND the calendar, so titles can be matched."""
        events = self.store.events_between(
            cal.now() - cal.timedelta(days=7), cal.now() + cal.timedelta(days=30))
        return cal.render_events(events)

    def _finish(self, trace: dict) -> None:
        import resource
        trace["total_ms"] = round(sum(s.get("latency_ms", 0)
                                      for s in trace["steps"]), 1)
        trace["ram_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        trace["done"] = True
        trace["ts"] = f"{cal.now():%Y-%m-%d %H:%M:%S}"
        self.history.append(trace)
        self._persist_trace(trace)

    def _persist_trace(self, trace: dict) -> None:
        """Append-only trace log (JSONL, one request per line) next to the DB —
        persistent across restarts so traces can be revisited while debugging."""
        try:
            from pathlib import Path
            log = Path(self.store.path).parent / "traces.jsonl"
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(trace, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass  # tracing must never break the request


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
