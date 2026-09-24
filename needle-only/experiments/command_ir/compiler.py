"""Deterministic Calendar Compiler: surface command -> canonical command.

GENERIC rules only (spike §6/§19). No phrase zoo, no `if "abwesenheit"`, no
`if "zug"`, no "verschiebe bitte". If a form only works via such a hack, it is
a documented failure, not a special case.

Reuses the frozen, well-tested temporal primitives from the production package
(read-only): local_calendar.calendar / .temporal / .identity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from local_calendar import calendar as cal
from local_calendar import temporal
from local_calendar.identity import resolve_name

# --------------------------------------------------------------- data model


@dataclass
class TimeSpec:
    first_day: date | None = None
    last_day: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    all_day: bool = True
    has_date: bool = False
    has_time: bool = False


@dataclass
class AddEvent:
    title: str
    start: datetime
    end: datetime
    all_day: bool
    person_ids: list[int]
    raw: dict = field(default_factory=dict)
    kind: str = "add"


@dataclass
class ChangeEvent:
    event_id: int
    title: str
    start: datetime
    end: datetime
    all_day: bool
    raw: dict = field(default_factory=dict)
    kind: str = "change"


@dataclass
class RemoveEvent:
    event_id: int
    title: str
    raw: dict = field(default_factory=dict)
    kind: str = "remove"


@dataclass
class ShowEvents:
    first_day: date
    last_day: date
    person_ids: list[int] | None
    raw: dict = field(default_factory=dict)
    kind: str = "show"


@dataclass
class Availability:
    person_ids: list[int]
    first_day: date
    last_day: date
    duration_min: int
    raw: dict = field(default_factory=dict)
    kind: str = "availability"


@dataclass
class CompileError:
    code: str
    message: str


@dataclass
class CompileResult:
    command: object | None
    error: CompileError | None
    status: str = "ok"          # ok | ambiguous | not_found | no_identifiers | error


# --------------------------------------------------------- small text helpers

_COLON = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_UHR = re.compile(r"(?<![:.\d])(\d{1,2})\s*uhr\b", re.IGNORECASE)
_UM = re.compile(r"\bum\s+(\d{1,2})(?::(\d{2}))?\b", re.IGNORECASE)
_T_RANGE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(?:bis|[-–])\s*"
                      r"(\d{1,2})(?::(\d{2}))?\s*uhr\b", re.IGNORECASE)
_DUR = re.compile(r"(\d+)\s*(min(?:ute[n]?)?|std|stunden?|h\b)", re.IGNORECASE)
_REL_DAY = {"heute": 0, "today": 0, "morgen": 1, "tomorrow": 1,
            "übermorgen": 2, "uebermorgen": 2}
_ICH = {"ich", "me", "mir", "mich", "myself", "i", "wir", "uns"}


def norm(s: str) -> str:
    return re.sub(r"[^\wäöüß]+", " ", (s or "").lower()).strip()


def _relative_day(span: str, today: date) -> date | None:
    low = (span or "").lower()
    for token, off in _REL_DAY.items():
        if re.search(rf"\b{token}\b", low):
            return today + timedelta(days=off)
    return None


def extract_times(span: str) -> list[time]:
    """All clock times in a text span, text order (generic)."""
    found: list[tuple[int, time]] = []
    for m in _T_RANGE.finditer(span or ""):
        found.append((m.start(), time(int(m[1]), int(m[2] or 0))))
        found.append((m.start() + 1, time(int(m[3]), int(m[4] or 0))))
    if found:
        seen, out = set(), []
        for _, t in sorted(found):
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out
    for m in _COLON.finditer(span or ""):
        found.append((m.start(), time(int(m[1]), int(m[2]))))
    for m in _UHR.finditer(span or ""):
        found.append((m.start(), time(int(m[1]))))
    for m in _UM.finditer(span or ""):
        found.append((m.start(), time(int(m[1]), int(m[2] or 0))))
    seen, out = set(), []
    for _, t in sorted(found):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def parse_when(span: str, today: date, roll: bool) -> TimeSpec:
    """One canonical temporal function (spike §6A). No tool-specific parsers."""
    spec = TimeSpec()
    dates = cal.extract_dates_from_text(span or "", today, roll=roll)
    if not dates:
        rd = _relative_day(span or "", today)
        if rd:
            dates = [rd]
    if not dates:
        wd = cal.extract_weekday_from_text(span or "", today)
        if wd:
            dates = [wd]
    if dates:
        spec.first_day, spec.last_day = dates[0], dates[-1]
        spec.has_date = True
    times = extract_times(span or "")
    if times:
        spec.start_time = times[0]
        spec.has_time = True
        if len(times) > 1:
            spec.end_time = times[-1]
    else:
        period = cal._period_of(span or "")
        if period:
            spec.start_time = cal.PERIOD_TIMES[period]
            spec.has_time = True
    spec.all_day = not spec.has_time
    return spec


def _parse_duration_min(span: str, default: int = 60) -> int:
    low = (span or "").lower()
    if "halbe stunde" in low or "halben stunde" in low:
        return 30
    if "eine stunde" in low or "einer stunde" in low:
        return 60
    m = _DUR.search(low)
    if m:
        n = int(m[1])
        unit = m[2].lower()
        return n if unit.startswith("min") else n * 60
    return default


# ------------------------------------------------------------ scope helpers


def _scope_events(store, ctx) -> list:
    return store.events_between(
        datetime(2000, 1, 1), datetime(2100, 1, 1),
        calendar_ids=[ctx.target_calendar_id])


def people_in_span(store, ctx, span: str) -> list[int]:
    """Known member names as literal evidence (spike: no phrase handling).
    The actor is included for 'ich'/'wir' or the actor's own name; otherwise a
    name must literally appear for add/change."""
    text = norm(span)
    ids: list[int] = []
    actor = store.person(ctx.actor_person_id)
    if (not span.strip()) or (text and any(re.search(rf"\b{w}\b", text) for w in _ICH)) \
            or (actor and actor["display_name"] and norm(actor["display_name"]) in text):
        ids.append(ctx.actor_person_id)
    for pid in ctx.member_person_ids:
        p = store.person(pid)
        if not p or pid in ids:
            continue
        name = norm(p["display_name"])
        if name and name in text:
            ids.append(pid)
    return ids


def _title_in_span(title: str, span: str) -> bool:
    nt, ns = norm(title), norm(span)
    if len(nt) < 2 or not ns:
        return False
    return nt in ns or ns in nt


def _matches_day(e, d: date) -> bool:
    return cal.covers_day(e, d)


def _matches_time(e, t: time) -> bool:
    if e.all_day:
        return False
    mins = t.hour * 60 + t.minute
    s = e.start.hour * 60 + e.start.minute
    en = e.end.hour * 60 + e.end.minute
    return abs(s - mins) <= 30 or s <= mins <= en


def resolve_target(store, ctx, target_span: str, today: date):
    """Evidence-only, scoped event identification (spike §6C). No scoring."""
    events = _scope_events(store, ctx)
    dates = cal.extract_dates_from_text(target_span or "", today, roll=False)
    if not dates:
        rd = _relative_day(target_span or "", today)
        if rd:
            dates = [rd]
    if not dates:
        wd = cal.extract_weekday_from_text(target_span or "", today)
        if wd:
            dates = [wd]
    times = extract_times(target_span or "")
    if not (target_span or "").strip():
        return None, "no_identifiers", []
    title_hits = [e for e in events if _title_in_span(e.title, target_span or "")]
    if not title_hits and not dates and not times:
        return None, "not_found", []
    cand = title_hits if title_hits else list(events)
    if dates:
        cand = [e for e in cand if any(_matches_day(e, d) for d in dates)]
    if times:
        cand = [e for e in cand if any(_matches_time(e, t) for t in times)]
    if not cand:
        return None, "not_found", []
    if len(cand) == 1:
        return cand[0], "unique", cand
    return None, "ambiguous", cand


# ------------------------------------------------------------ time builders


def build_add_times(spec: TimeSpec):
    if not spec.has_date:
        return None
    if spec.has_time:
        day = spec.first_day
        start = datetime.combine(day, spec.start_time)
        if spec.end_time:
            end = datetime.combine(day, spec.end_time)
            if end <= start:  # a range across midnight / bad span
                end = start + timedelta(minutes=60)
        else:
            end = start + timedelta(minutes=60)
        return start, end, False
    start = datetime.combine(spec.first_day, time(0, 0))
    end = datetime.combine(spec.last_day + timedelta(days=1), time(0, 0))
    return start, end, True


def apply_timepatch(old, spec: TimeSpec):
    """Generic TimePatch semantics (spike §6B). Range replaces the range."""
    old_dur = old.end - old.start
    if spec.has_date and spec.last_day and spec.last_day != spec.first_day:
        if spec.has_time:
            start = datetime.combine(spec.first_day, spec.start_time)
            end = (datetime.combine(spec.last_day, spec.end_time)
                   if spec.end_time else start + timedelta(hours=1))
            return start, end, False
        start = datetime.combine(spec.first_day, time(0, 0))
        end = datetime.combine(spec.last_day + timedelta(days=1), time(0, 0))
        return start, end, True
    if spec.has_date and spec.has_time:
        start = datetime.combine(spec.first_day, spec.start_time)
        dur = (datetime.combine(spec.first_day, spec.end_time) - start
               if spec.end_time else
               (old_dur if not old.all_day else timedelta(hours=1)))
        return start, start + dur, False
    if spec.has_date:
        start = datetime.combine(spec.first_day, old.start.time())
        return start, start + old_dur, old.all_day
    if spec.has_time:
        start = datetime.combine(old.start.date(), spec.start_time)
        if spec.end_time:
            return start, datetime.combine(old.start.date(), spec.end_time), False
        dur = old_dur if not old.all_day else timedelta(hours=1)
        return start, start + dur, False
    return old.start, old.end, old.all_day


# ------------------------------------------------------------- per-tool rules


def _person_names(store, ids: list[int]) -> list[str]:
    return store.people_names(ids)


def compile_add(args, ctx, store, today) -> CompileResult:
    title = str(args.get("title", "")).strip()
    if not title:
        return CompileResult(None, CompileError("COMPILER_TIME", "missing title"),
                             "error")
    spec = parse_when(str(args.get("when", "")), today, roll=True)
    times = build_add_times(spec)
    if times is None:
        return CompileResult(None, CompileError("COMPILER_TIME",
                             f"no date in when={args.get('when')!r}"), "error")
    start, end, all_day = times
    pids = people_in_span(store, ctx, str(args.get("people", "")))
    if not pids:
        pids = [ctx.actor_person_id]
    return CompileResult(AddEvent(title, start, end, all_day, pids,
                                  raw=dict(args)), None)


def compile_change(contract, args, ctx, store, today) -> CompileResult:
    target = str(args.get("target", ""))
    if contract == "A":
        change = str(args.get("change", "")).strip()
        spec = parse_when(change, today, roll=False)
        new_title = None if (spec.has_date or spec.has_time) else (change or None)
    else:
        spec = parse_when(str(args.get("new_when", "")), today, roll=False)
        new_title = (str(args.get("new_title", "")).strip() or None)
    ev, status, _ = resolve_target(store, ctx, target, today)
    if status != "unique":
        code = "AMBIGUOUS" if status == "ambiguous" else "COMPILER_TARGET"
        return CompileResult(None, CompileError(code, f"target {status}"), status)
    if not spec.has_date and not spec.has_time and not new_title:
        return CompileResult(None, CompileError("COMPILER_TIME",
                             "no time and no title in change"), "error")
    if spec.has_date or spec.has_time:
        start, end, all_day = apply_timepatch(ev, spec)
    else:
        start, end, all_day = ev.start, ev.end, ev.all_day
    return CompileResult(ChangeEvent(ev.id, new_title or ev.title, start, end,
                                     all_day, raw=dict(args)), None)


def compile_remove(args, ctx, store, today) -> CompileResult:
    target = str(args.get("target", ""))
    ev, status, _ = resolve_target(store, ctx, target, today)
    if status != "unique":
        code = "AMBIGUOUS" if status == "ambiguous" else "COMPILER_TARGET"
        return CompileResult(None, CompileError(code, f"target {status}"), status)
    return CompileResult(RemoveEvent(ev.id, ev.title, raw=dict(args)), None)


def compile_show(contract, args, ctx, store, today) -> CompileResult:
    span = (str(args.get("query", "")) if contract == "A"
            else " ".join(x for x in (str(args.get("when", "")),
                                      str(args.get("person", ""))) if x))
    window = temporal.resolve_read_window(span, {}, today)
    pids = people_in_span(store, ctx, span) or None
    return CompileResult(ShowEvents(window.first_day, window.last_day, pids,
                                    raw=dict(args)), None)


def compile_availability(contract, args, ctx, store, today) -> CompileResult:
    if contract == "A":
        q = str(args.get("query", ""))
        pids = people_in_span(store, ctx, q)
        spec = parse_when(q, today, roll=False)
        dur = _parse_duration_min(q)
    else:
        q = " ".join(x for x in (str(args.get("people", "")),
                                 str(args.get("when", ""))) if x)
        pids = people_in_span(store, ctx, str(args.get("people", "")))
        spec = parse_when(q, today, roll=False)
        dur = _parse_duration_min(str(args.get("duration", ""))) \
            if str(args.get("duration", "")).strip() else 60
    if not pids:
        return CompileResult(None, CompileError("COMPILER_TARGET",
                             "no people"), "error")
    first = spec.first_day if spec.has_date else today
    last = spec.last_day if spec.has_date else today + timedelta(days=6)
    return CompileResult(Availability(pids, first, last, dur, raw=dict(args)), None)


_DISPATCH = {"calendar_add": compile_add, "calendar_change": compile_change,
             "calendar_remove": compile_remove, "calendar_show": compile_show,
             "calendar_availability": compile_availability}


def compile_call(call: dict, contract: str, ctx, store, today) -> CompileResult:
    name = call.get("name", "")
    fn = _DISPATCH.get(name)
    if fn is None:
        return CompileResult(None, CompileError("MODEL_TOOL", f"unknown {name}"),
                             "error")
    args = call.get("arguments") or {}
    if name in ("calendar_add",):
        return fn(args, ctx, store, today)
    if name in ("calendar_change", "calendar_show", "calendar_availability"):
        return fn(contract, args, ctx, store, today)
    return fn(args, ctx, store, today)


def compile_all(calls: list[dict], contract: str, ctx, store, today):
    """Compile every call; a plan is only valid when all commands compile."""
    commands, errors, statuses = [], [], []
    for call in calls:
        r = compile_call(call, contract, ctx, store, today)
        statuses.append(r.status)
        if r.error or r.command is None:
            errors.append({"name": call.get("name"), "code": r.error.code
                           if r.error else "COMPILER", "status": r.status})
        else:
            commands.append(r.command)
    return commands, errors, statuses
