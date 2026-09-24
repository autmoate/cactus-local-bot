"""Transport-neutral view models (plan §7/§8/§9/§18/§20-§24).

The renderer receives finished view data — it never runs unscoped DB queries,
resolves identities or invents calendar arithmetic. Two hard rules:

* PRIVATE context: `shared` is EMPTY. The own events live in the actor's lane
  (timed) and in the header layer (all-day). A private "Büro" must never be
  classified as a shared event and painted as a full-width band (plan §9).
* GROUP context: `shared` is ONLY the current group calendar. Other groups and
  members' private events never leak titles (masked as "belegt").

All-day entries ('absence') go into the AllDayBanner header layer, timed entries
into TimedSegment/BusyBlock lanes — clipped per day by cal.segment_event (plan §8).
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
    event_id: int | None = None
    editable: bool = False     # lives in the current target calendar (buttons)


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
    event_id: int | None = None


@dataclass
class AllDayBanner:
    title: str | None          # None = another member's absence (masked)
    start_day: date
    end_day: date              # inclusive
    shared: bool


@dataclass
class DayView:
    day: date
    group: bool
    banners: list[AllDayBanner]
    lanes: list[MemberLane]
    shared: list[SharedEventCard]
    free: list[tuple[datetime, datetime]]      # common free (group) / own free


@dataclass
class WeekView:
    first_day: date
    days: list[date]
    group: bool
    banners: list[AllDayBanner]
    lanes: list[MemberLane]
    shared: list[SharedEventCard]


def _bounds(first: date, last: date) -> tuple[datetime, datetime]:
    return (datetime.combine(first, time(0, 0)),
            datetime.combine(last + timedelta(days=1), time(0, 0)))


def _read_events(store, ctx: RequestContext, start: datetime,
                 end: datetime) -> list[cal.CalendarEvent]:
    """The SINGLE scoped read that feeds this view (plan §10). For group this is
    the union of members' personal calendars and the current group calendar; for
    private it is the personal calendar only."""
    return store.events_between(start, end,
                                calendar_ids=ctx.busy_calendar_ids(store))


def _group_visible(ctx: RequestContext, e, shared_ids: set[int]) -> bool:
    """An event is group-visible when it lives in the group calendar or was
    explicitly shared to it (plan §3)."""
    return ctx.is_group and (e.calendar_id == ctx.target_calendar_id
                             or e.id in shared_ids)


def _banners(ctx: RequestContext, events, ids_map,
             shared_ids: set[int]) -> list[AllDayBanner]:
    out: list[AllDayBanner] = []
    for e in events:
        if not e.all_day:
            continue
        shared = _group_visible(ctx, e, shared_ids)
        if ctx.is_group and not shared:
            continue  # member absences stay in their lane, never in the header
        first, last = cal.allday_span(e)
        out.append(AllDayBanner(e.title, first, last, shared))
    return sorted(out, key=lambda b: b.start_day)


def _shared_cards(ctx: RequestContext, events, ids_map,
                  shared_ids: set[int]) -> list[SharedEventCard]:
    if not ctx.is_group:
        return []  # plan §9: private events are never shared
    out = [SharedEventCard(e.title, e.start, e.end, e.all_day, e.participants,
                           event_id=e.id)
           for e in events
           if _group_visible(ctx, e, shared_ids) and not e.all_day]
    return sorted(out, key=lambda c: c.start)


def _lanes(store, ctx: RequestContext, events, ids_map,
           shared_ids: set[int]) -> list[MemberLane]:
    group_target = ctx.target_calendar_id if ctx.is_group else None
    lanes: list[MemberLane] = []
    for pid in ctx.member_person_ids:
        p = store.person(pid)
        if not p:
            continue
        blocks: list[BusyBlock] = []
        for e in events:
            if pid not in ids_map.get(e.id, set()):
                continue
            shared = _group_visible(ctx, e, shared_ids)
            title = e.title if (pid == ctx.actor_person_id or shared) else None
            editable = (pid == ctx.actor_person_id
                        and e.calendar_id == ctx.target_calendar_id)
            blocks.append(BusyBlock(e.start, e.end, e.all_day, title, shared,
                                    event_id=e.id, editable=editable))
        lanes.append(MemberLane(pid, p["display_name"],
                                p["color_key"] or cal.color_for(p["display_name"]),
                                sorted(blocks, key=lambda b: b.start)))
    return lanes


def _free(events, ids_map, member_ids, first: date, last: date,
          duration_min: int = 60) -> list[tuple[datetime, datetime]]:
    members = set(member_ids)
    busy = [(e.start, e.end) for e in events
            if ids_map.get(e.id, set()) & members]
    return cal.free_slots_from_busy(busy, first, last, duration_min)


def _shared_ids(store, ctx: RequestContext) -> set[int]:
    return store.shared_event_ids(ctx.target_calendar_id) if ctx.is_group else set()


def build_day_view_from_events(store, ctx: RequestContext, day: date,
                               events) -> DayView:
    ids_map = store.participant_ids_map([e.id for e in events])
    shared_ids = _shared_ids(store, ctx)
    return DayView(day, ctx.is_group, _banners(ctx, events, ids_map, shared_ids),
                   _lanes(store, ctx, events, ids_map, shared_ids),
                   _shared_cards(ctx, events, ids_map, shared_ids),
                   _free(events, ids_map, ctx.member_person_ids, day, day))


def build_range_view_from_events(store, ctx: RequestContext, first: date,
                                 last: date, events) -> WeekView:
    ids_map = store.participant_ids_map([e.id for e in events])
    shared_ids = _shared_ids(store, ctx)
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    return WeekView(first, days, ctx.is_group,
                    _banners(ctx, events, ids_map, shared_ids),
                    _lanes(store, ctx, events, ids_map, shared_ids),
                    _shared_cards(ctx, events, ids_map, shared_ids))


def build_day_view(store, ctx: RequestContext, day: date) -> DayView:
    start, end = _bounds(day, day)
    return build_day_view_from_events(store, ctx, day,
                                      _read_events(store, ctx, start, end))


def build_week_view(store, ctx: RequestContext, first_day: date) -> WeekView:
    days = [first_day + timedelta(days=i) for i in range(7)]
    start, end = _bounds(days[0], days[-1])
    return build_range_view_from_events(store, ctx, days[0], days[-1],
                                        _read_events(store, ctx, start, end))


def group_free(store, ctx: RequestContext, first_day: date, last_day: date,
               duration_min: int = 60) -> list[tuple[datetime, datetime]]:
    """Common free slots, ID-based and scoped (plan §18), exact interval solver.
    Absences count as busy; the bit-set kernel is not used as the solver."""
    return cal.find_free_slots_for_people(
        store, ctx.member_person_ids, ctx.busy_calendar_ids(store),
        first_day, last_day, duration_min)
