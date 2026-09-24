"""Transport-neutral view models (plan §16-§18, §21).

The Telegram (or later PWA/Matrix) renderer receives finished view data — it
never runs unscoped DB queries itself. Privacy rule: in a group, other members'
private event titles are NEVER exposed; they appear as busy blocks only. Shared
group events keep their titles. Colors are deterministic per person (color_for).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from . import calendar as cal
from .identity import RequestContext


@dataclass
class BusyBlock:
    start: datetime
    end: datetime
    all_day: bool
    title: str | None          # None = private busy of another member
    shared: bool               # group event (title always safe to show)


@dataclass
class MemberLane:
    person_id: int
    name: str
    color: str
    blocks: list[BusyBlock] = field(default_factory=list)


@dataclass
class SharedEventCard:
    title: str
    start: datetime
    end: datetime
    all_day: bool
    participants: list[str]


@dataclass
class DayView:
    day: date
    group: bool
    lanes: list[MemberLane]
    shared: list[SharedEventCard]
    free: list[tuple[datetime, datetime]]      # common free slots (group) / own free


@dataclass
class WeekView:
    first_day: date
    days: list[date]
    group: bool
    lanes: list[MemberLane]
    shared: list[SharedEventCard]


def _bounds(day: date) -> tuple[datetime, datetime]:
    s = datetime.combine(day, time(0, 0))
    return s, s + timedelta(days=1)


def _lane_blocks(store, person_id: int, name: str, start: datetime,
                 end: datetime, *, actor: int, group_target: int | None
                 ) -> list[BusyBlock]:
    """Every event this person takes part in (personal + shared) with titles
    masked unless it is the actor themself or a shared group event."""
    blocks: list[BusyBlock] = []
    for ev in store.events_between(start, end, person=name):
        is_actor = person_id == actor
        shared = group_target is not None and ev.calendar_id == group_target
        blocks.append(BusyBlock(ev.start, ev.end, ev.all_day,
                                ev.title if (is_actor or shared) else None,
                                shared))
    return sorted(blocks, key=lambda b: b.start)


def build_day_view(store, ctx: RequestContext, day: date) -> DayView:
    start, end = _bounds(day)
    group = ctx.is_group
    shared_events = store.events_between(start, end,
                                         calendar_ids=ctx.read_calendar_ids)
    shared = [SharedEventCard(e.title, e.start, e.end, e.all_day, e.participants)
              for e in shared_events]
    lanes = []
    names = []
    group_target = ctx.target_calendar_id if group else None
    for pid in ctx.member_person_ids:
        p = store.person(pid)
        if not p:
            continue
        names.append(p["display_name"])
        lanes.append(MemberLane(pid, p["display_name"],
                                p["color_key"] or cal.color_for(p["display_name"]),
                                _lane_blocks(store, pid, p["display_name"],
                                             start, end, actor=ctx.actor_person_id,
                                             group_target=group_target)))
    free = cal.find_free_slots(store, names or ["Ich"], day, day, 60)
    return DayView(day, group, lanes, shared, free)


def build_week_view(store, ctx: RequestContext, first_day: date) -> WeekView:
    days = [first_day + timedelta(days=i) for i in range(7)]
    start = datetime.combine(days[0], time(0, 0))
    end = datetime.combine(days[-1] + timedelta(days=1), time(0, 0))
    shared_events = store.events_between(start, end, calendar_ids=ctx.read_calendar_ids)
    shared = [SharedEventCard(e.title, e.start, e.end, e.all_day, e.participants)
              for e in shared_events]
    group_target = ctx.target_calendar_id if ctx.is_group else None
    lanes = []
    for pid in ctx.member_person_ids:
        p = store.person(pid)
        if not p:
            continue
        lanes.append(MemberLane(pid, p["display_name"],
                                p["color_key"] or cal.color_for(p["display_name"]),
                                _lane_blocks(store, pid, p["display_name"],
                                             start, end, actor=ctx.actor_person_id,
                                             group_target=group_target)))
    return WeekView(first_day, days, ctx.is_group, lanes, shared)


def group_free(store, ctx: RequestContext, first_day: date, last_day: date,
               duration_min: int = 60) -> list[tuple[datetime, datetime]]:
    """Common free slots over the group members' personal + shared calendars,
    using the exact interval solver (plan §18) — never the 15-min bitset."""
    names = store.people_names(ctx.member_person_ids)
    return cal.find_free_slots(store, names, first_day, last_day, duration_min)
