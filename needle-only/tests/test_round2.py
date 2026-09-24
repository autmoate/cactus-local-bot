"""Production-Ready Round 2 regressions (plan §1/§3/§6/§8/§9/§10/§12/§30/§31/§32).

Deterministic, no model. Freezes now() to 2026-09-24 (Thursday) for the
temporal cases.
"""
import threading
from datetime import date, datetime, time, timedelta

import pytest

from local_calendar import calendar as cal
from local_calendar import temporal, views
from local_calendar.agent import execute_call
from local_calendar.calendar import CalendarEvent, CalendarStore
from local_calendar.identity import (RequestContext, resolve_group, resolve_private,
                                     resolve_read_person)
from local_calendar.service import AtomicService

NOW = datetime(2026, 9, 24, 10, 0)   # Thursday
D = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(cal, "now", lambda: NOW)


def _store(tmp_path) -> CalendarStore:
    return CalendarStore(tmp_path / "c.db")


def _svc(store, calls):
    svc = AtomicService.__new__(AtomicService)
    svc.store, svc.tools, svc.lock = store, {}, threading.Lock()
    svc.interpret = lambda text: calls
    return svc


def _mk(store, title, day: date, h0: int, h1: int, cal_id, people=("Ich",),
        all_day=False):
    if all_day:
        ev = CalendarEvent(title=title, kind="absence",
                           start=datetime.combine(day, time(0, 0)),
                           end=datetime.combine(day + timedelta(days=h1 if h1 else 1),
                                                time(0, 0)),
                           all_day=True, participants=list(people))
    else:
        ev = CalendarEvent(title=title, start=datetime.combine(day, time(h0, 0)),
                           end=datetime.combine(day, time(h1, 0)),
                           participants=list(people))
    return store.add(ev, calendar_id=cal_id)


# ===================================================== PHASE 3: owner reconcile
def test_reconcile_binds_legacy_ich_in_place(tmp_path):
    store = _store(tmp_path)
    legacy = store.ensure_person("Ich", None)
    legacy_cal = store.ensure_personal_calendar(legacy)
    _mk(store, "Arzt", D + timedelta(days=1), 10, 11, legacy_cal, ("Ich",))
    owner = store.reconcile_owner(111, "Oll")
    assert owner == legacy                              # same row, calendar kept
    assert store.person(owner)["display_name"] == "Oll"
    assert store.person(owner)["telegram_user_id"] == 111
    evs = store.events_between(NOW, NOW + timedelta(days=7),
                               calendar_ids=[legacy_cal])
    assert [e.title for e in evs] == ["Arzt"]           # events preserved
    assert store.reconcile_owner(111, "Oll") == owner   # idempotent


def test_reconcile_merges_two_owner_rows(tmp_path):
    store = _store(tmp_path)
    legacy = store.ensure_person("Ich", None)
    legacy_cal = store.ensure_personal_calendar(legacy)
    _mk(store, "Arzt", D + timedelta(days=1), 10, 11, legacy_cal, ("Ich",))
    owner = store.ensure_person("Oll", 111)
    owner_cal = store.ensure_personal_calendar(owner)
    _mk(store, "Sport", D + timedelta(days=2), 10, 11, owner_cal, ("Oll",))
    merged = store.reconcile_owner(111, "Oll")
    assert merged == owner
    all_ids = [e.id for e in store.events_between(
        NOW, NOW + timedelta(days=7), calendar_ids=[owner_cal])]
    titles = {e.title for e in store.events_between(NOW, NOW + timedelta(days=7))}
    assert titles == {"Arzt", "Sport"}                  # no event lost/dup
    assert store.person(legacy) is None                 # unreferenced -> removed
    assert len(all_ids) == 2


# ============================================ PHASE 6/19: absence vs appointment
def test_absence_does_not_block_breakfast(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Oll", owner=True)
    _mk(store, "Urlaub", D, 0, 0, ctx.target_calendar_id, ("Oll",), all_day=True)
    # 24.09.-27.09. absence
    with store._conn() as c:
        c.execute("UPDATE events SET end=? WHERE title='Urlaub'",
                  (store._iso(datetime.combine(date(2026, 9, 28), time(0, 0))),))
    svc = _svc(store, [{"name": "calendar_create",
                        "arguments": {"title": "Frühstück", "date": "25.9.",
                                      "time": "08:00"}}])
    d = svc.prepare("Trag am 25.9. Frühstück ab 8 Uhr ein", ctx)
    assert d.kind == "write_proposal"                   # NOT blocked by vacation
    assert svc.confirm(d.proposal["token"], ctx)["ok"]
    assert any(e.title == "Frühstück" for e in store.events_between(
        NOW, NOW + timedelta(days=7), calendar_ids=ctx.read_calendar_ids))


def test_absence_still_blocks_availability(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Oll", owner=True)
    _mk(store, "Urlaub", D, 0, 0, ctx.target_calendar_id, ("Oll",), all_day=True)
    with store._conn() as c:
        c.execute("UPDATE events SET end=? WHERE title='Urlaub'",
                  (store._iso(datetime.combine(date(2026, 9, 28), time(0, 0))),))
    out = execute_call(store, "calendar_find_slot",
                       {"persons": "Ich", "date": "25.9."}, "", scope=ctx)
    assert out["ok"] and out["resolved"]["slots"] == []  # vacation = unavailable


def test_overlap_is_warning_not_reject(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Oll", owner=True)
    _mk(store, "Meeting", D + timedelta(days=1), 10, 11, ctx.target_calendar_id,
        ("Oll",))
    svc = _svc(store, [{"name": "calendar_create",
                        "arguments": {"title": "Kaffee", "date": "25.9.",
                                      "time": "10:30"}}])
    d = svc.prepare("Trag am 25.9. um 10:30 Kaffee ein", ctx)
    assert d.kind == "write_proposal" and d.warnings
    assert svc.confirm(d.proposal["token"], ctx)["ok"]   # confirm still works


# ================================================= PHASE 8: day segmentation
def test_segment_event_half_open_and_multiday():
    ev = CalendarEvent(title="Büro", start=datetime(2026, 9, 29, 9),
                       end=datetime(2026, 9, 29, 16))
    assert cal.segment_event(ev, date(2026, 9, 29)) == (
        datetime(2026, 9, 29, 9), datetime(2026, 9, 29, 16))
    for d in (date(2026, 9, 28), date(2026, 9, 30)):
        assert cal.segment_event(ev, d) is None
    multi = CalendarEvent(title="Reise", start=datetime(2026, 9, 29, 22),
                          end=datetime(2026, 10, 1, 2))
    assert cal.segment_event(multi, date(2026, 9, 29)) == (
        datetime(2026, 9, 29, 22), datetime(2026, 9, 30, 0))
    assert cal.segment_event(multi, date(2026, 9, 30)) == (
        datetime(2026, 9, 30, 0), datetime(2026, 10, 1, 0))


# ================================ PHASE 9/31: Büro only its own day in the week
def test_office_only_on_its_day_in_week(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Oll", owner=True)
    _mk(store, "Büro", date(2026, 9, 29), 9, 16, ctx.target_calendar_id, ("Oll",))
    monday = date(2026, 9, 28)
    v = views.build_week_view(store, ctx, monday)
    assert v.shared == []                                # private never shared (§9)
    block = next(b for l in v.lanes for b in l.blocks)
    assert cal.segment_event(block, date(2026, 9, 29)) is not None
    for d in (date(2026, 9, 28), date(2026, 9, 30), date(2026, 10, 1)):
        assert cal.segment_event(block, d) is None


# ============================================ PHASE 12/31: temporal windows
def test_next_week_window():
    w = temporal.resolve_read_window("Liste meine Termine nächste Woche", {}, D)
    assert (w.first_day, w.last_day) == (date(2026, 9, 28), date(2026, 10, 4))
    assert w.source == "next_week"


def test_explicit_range_matches_next_week():
    w = temporal.resolve_read_window("Liste meine Termine 28.9. - 04.10.", {}, D)
    assert (w.first_day, w.last_day) == (date(2026, 9, 28), date(2026, 10, 4))
    assert w.source == "text:range"


def test_single_day_window():
    w = temporal.resolve_read_window("Zeig den 29.9.", {}, D)
    assert (w.first_day, w.last_day) == (date(2026, 9, 29), date(2026, 9, 29))


# ================================== PHASE 10/11/31: one read -> text == widget
def test_list_text_and_widget_share_the_same_events(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Oll", owner=True)
    _mk(store, "Büro", date(2026, 9, 29), 9, 16, ctx.target_calendar_id, ("Oll",))
    out = execute_call(store, "calendar_list",
                       {"date": "29.9.", "until": "29.9."}, "Zeig den 29.9.",
                       scope=ctx)
    assert out["ok"] and len(out["data"]) == 1
    assert "Büro" in out["message"]
    from local_calendar.telegram import _events_from_data
    events = _events_from_data(out["data"])
    v = views.build_day_view_from_events(store, ctx, date(2026, 9, 29), events)
    # text and widget are built from the SAME data, so the widget shows Büro too
    assert v.lanes and any("Büro" == b.title for l in v.lanes for b in l.blocks)


# ============================ PHASE 4/31: person "" / "Ich" / name same actor
def test_person_aliases_resolve_to_actor(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Oll", owner=True)
    ids_empty, _ = resolve_read_person(store, ctx, "")
    ids_ich, _ = resolve_read_person(store, ctx, "Ich")
    ids_name, _ = resolve_read_person(store, ctx, "Oll")
    assert ids_empty == ids_ich == ids_name == [ctx.actor_person_id]


# ==================================================== PHASE 30: scope & privacy
def test_delete_scoped_to_own_calendar(tmp_path):
    store = _store(tmp_path)
    a = resolve_private(store, 111, 111, "Anna", owner=True)
    b = resolve_private(store, 222, 222, "Ben", owner=True)
    ev_a = _mk(store, "Zahnarzt", D + timedelta(days=1), 10, 11,
               a.target_calendar_id, ("Anna",))
    _mk(store, "Zahnarzt", D + timedelta(days=1), 11, 12, b.target_calendar_id,
        ("Ben",))
    svc = _svc(store, [{"name": "calendar_delete",
                        "arguments": {"title": "Zahnarzt"}}])
    d = svc.prepare("Lösch Zahnarzt", a)
    assert d.kind == "write_proposal"
    assert svc.confirm(d.proposal["token"], a)["ok"]
    assert store.get(ev_a.id) is None                    # A gone
    remaining = store.events_between(NOW, NOW + timedelta(days=7),
                                     calendar_ids=[b.target_calendar_id])
    assert len(remaining) == 1                           # B untouched


def test_group_shared_event_title_private_hidden(tmp_path):
    store = _store(tmp_path)
    g = resolve_group(store, 500, 111, "Anna", owner=True)
    store.ensure_personal_calendar(g.actor_person_id)
    ben = store.ensure_person("Ben", 222)
    store.ensure_personal_calendar(ben)
    store.add_member(g.target_calendar_id, ben)
    g.member_person_ids = store.members_of(g.target_calendar_id)
    day = D + timedelta(days=1)
    _mk(store, "Teammeeting", day, 13, 14, g.target_calendar_id, ("Anna", "Ben"))
    _mk(store, "Privatarzt", day, 10, 11, store.calendar_of_person(ben), ("Ben",))
    v = views.build_day_view(store, g, day)
    by_name = {l.name: l for l in v.lanes}
    assert [c.title for c in v.shared] == ["Teammeeting"]
    assert by_name["Ben"].blocks[0].title is None        # other member masked


def test_other_group_does_not_leak(tmp_path):
    store = _store(tmp_path)
    g1 = resolve_group(store, 500, 111, "Anna", owner=True)
    store.ensure_personal_calendar(g1.actor_person_id)
    g2 = resolve_group(store, 600, 111, "Anna", owner=True)  # other group, same actor
    day = D + timedelta(days=1)
    _mk(store, "Fremdgruppe", day, 15, 16, g2.target_calendar_id, ("Anna",))
    v = views.build_day_view(store, g1, day)
    assert all(c.title != "Fremdgruppe" for c in v.shared)
    assert all(b.title != "Fremdgruppe" for l in v.lanes for b in l.blocks)


def test_delete_private_ignores_same_title_in_group(tmp_path):
    store = _store(tmp_path)
    p = resolve_private(store, 111, 111, "Oll", owner=True)
    g = resolve_group(store, 500, 111, "Oll", owner=True)
    g.member_person_ids = store.members_of(g.target_calendar_id)
    day = D + timedelta(days=1)
    ev_p = _mk(store, "Arzt", day, 10, 11, p.target_calendar_id, ("Oll",))
    ev_g = _mk(store, "Arzt", day, 15, 16, g.target_calendar_id, ("Oll",))
    svc = _svc(store, [{"name": "calendar_delete", "arguments": {"title": "Arzt"}}])
    d = svc.prepare("Lösch Arzt", p)
    assert d.kind == "write_proposal"
    assert svc.confirm(d.proposal["token"], p)["ok"]
    assert store.get(ev_p.id) is None and store.get(ev_g.id) is not None


def test_group_delete_only_group_calendar(tmp_path):
    store = _store(tmp_path)
    g = resolve_group(store, 500, 111, "Oll", owner=True)
    store.ensure_personal_calendar(g.actor_person_id)
    g.member_person_ids = store.members_of(g.target_calendar_id)
    day = D + timedelta(days=1)
    _mk(store, "Arzt", day, 10, 11,
        store.calendar_of_person(g.actor_person_id), ("Oll",))
    ev_g = _mk(store, "Arzt", day, 15, 16, g.target_calendar_id, ("Oll",))
    svc = _svc(store, [{"name": "calendar_delete", "arguments": {"title": "Arzt"}}])
    d = svc.prepare("Lösch Arzt", g)
    assert svc.confirm(d.proposal["token"], g)["ok"]
    assert store.get(ev_g.id) is None
    assert len(store.events_between(NOW, NOW + timedelta(days=7),
                                    calendar_ids=[store.calendar_of_person(
                                        g.actor_person_id)])) == 1


def test_find_slot_failure_returns_no_widget_data(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Oll", owner=True)
    out = execute_call(store, "calendar_find_slot", {"persons": ""}, "", scope=ctx)
    assert not out["ok"] and not out.get("resolved", {}).get("slots")


def test_availability_render_uses_resolved_day(tmp_path):
    from local_calendar import render
    store = _store(tmp_path)
    resolved = {"persons": ["Oll"], "first_day": "2026-09-29",
                "last_day": "2026-09-29", "window_start": "07:00",
                "window_end": "21:00",
                "slots": [["2026-09-29 09:00", "2026-09-29 10:00"]]}
    png = render.render_availability_png(store, ["Oll"], None, resolved)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 500


def test_homonyms_not_silently_resolved(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Oll", owner=True)
    store.ensure_person("Lisa", None)
    with store._conn() as c:  # a second, distinct homonym row
        c.execute("INSERT INTO people (display_name, color_key) VALUES ('Lisa','#000')")
    ids, status = resolve_read_person(store, ctx, "Lisa")
    assert status == "ambiguous" and ids is None
