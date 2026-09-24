"""Synthetic fixtures + reference/real execution + DB snapshots.

No real private data — only anonymous names (Ada, Ben, Cleo) and generic titles.
The reference transform uses ONLY the absolute ISO values in expected_ops, so
the gold final state never depends on the compiler under test.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from local_calendar.calendar import CalendarEvent, CalendarStore
from local_calendar.identity import resolve_group, resolve_private

WIDE_START = datetime(2000, 1, 1)
WIDE_END = datetime(2100, 1, 1)


def build_store(fixture: dict, path: str | None = None):
    """Fresh store + RequestContext from a synthetic fixture spec."""
    if path is None:
        path = str(Path(tempfile.mkdtemp()) / "fixture.db")
    store = CalendarStore(path)
    if fixture.get("kind") == "group":
        ctx = resolve_group(store, fixture.get("chat_id", 9001), 101,
                            fixture.get("actor", "Ada"),
                            fixture.get("name", "Team"), owner=True)
        store.ensure_personal_calendar(ctx.actor_person_id)
        for name in fixture.get("members", []):
            pid = store.ensure_person(name, abs(hash(name)) % 10**8 + 1000)
            store.ensure_personal_calendar(pid)
            store.add_member(ctx.target_calendar_id, pid)
        ctx.member_person_ids = store.members_of(ctx.target_calendar_id)
    else:
        ctx = resolve_private(store, fixture.get("uid", 101), fixture.get("chat", 101),
                              fixture.get("actor", "Ada"), owner=True)
        store.ensure_personal_calendar(ctx.actor_person_id)
    for e in fixture.get("events", []):
        store.add(CalendarEvent(title=e["title"], start=_dt(e["start"]),
                                end=_dt(e["end"]), all_day=bool(e.get("all_day")),
                                busy=bool(e.get("busy", True)),
                                participants=list(e.get("participants") or [])),
                  calendar_id=(fixture.get("calendar_id")
                               or ctx.target_calendar_id))
    return store, ctx, path


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def snapshot(store, ctx) -> list[tuple]:
    """Canonical event set in the current target calendar."""
    events = store.events_between(WIDE_START, WIDE_END,
                                  calendar_ids=[ctx.target_calendar_id])
    return sorted((e.title, e.start.isoformat(), e.end.isoformat(), bool(e.all_day))
                  for e in events)


# ------------------------------------------------------ reference (gold) apply


def apply_expected_ops(fixture: dict, ops: list[dict]) -> list[tuple]:
    """Reference transform using absolute ISO values only (no parsing)."""
    events = [dict(e) for e in fixture.get("events", [])]
    for op in ops:
        if op["op"] == "add":
            events.append({"title": op["title"], "start": op["start"],
                           "end": op["end"], "all_day": op.get("all_day", False)})
        elif op["op"] == "change":
            for e in events:
                if e["title"] == op["target"]:
                    for key in ("start", "end", "all_day"):
                        if key in op:
                            e[key] = op[key]
                    if "new_title" in op:
                        e["title"] = op["new_title"]
                    break
        elif op["op"] == "remove":
            if "match_start" in op:
                ms = _iso(op["match_start"])
                events = [e for e in events if not (e["title"] == op["target"]
                                                    and _iso(e["start"]) == ms)]
            else:
                events = [e for e in events if e["title"] != op["target"]]
    return sorted((e["title"], _iso(e["start"]), _iso(e["end"]),
                   bool(e.get("all_day", False))) for e in events)


def _iso(value: str) -> str:
    return datetime.fromisoformat(value).isoformat()


# ------------------------------------------------------- real command execution


def apply_commands(commands: list, store, ctx) -> list[str]:
    """Execute compiled commands against the fixture (test copy). Returns the
    list of write kinds actually applied."""
    applied: list[str] = []
    for c in commands:
        if c.kind == "add":
            store.add(CalendarEvent(title=c.title, start=c.start, end=c.end,
                                    all_day=c.all_day, busy=True,
                                    participants=store.people_names(c.person_ids)),
                      calendar_id=ctx.target_calendar_id,
                      created_by_person_id=ctx.actor_person_id)
            applied.append("add")
        elif c.kind == "change":
            ev = store.get(c.event_id)
            if ev is None:
                continue
            store.update(ev.model_copy(update={"title": c.title, "start": c.start,
                                               "end": c.end, "all_day": c.all_day}))
            applied.append("change")
        elif c.kind == "remove":
            if store.get(c.event_id) is not None:
                store.delete(c.event_id)
                applied.append("remove")
    return applied


def write_count(commands: list) -> int:
    return sum(1 for c in commands if c.kind in ("add", "change", "remove"))
