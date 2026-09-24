"""Deterministic production tests (plan §22, §24, §25, §26): multi-user
isolation, authorization, atomic prepare/confirm. No model is loaded — the
Needle interpretation is injected, so these run in milliseconds."""
import threading
from datetime import timedelta

import pytest

from local_calendar import calendar as cal
from local_calendar.calendar import CalendarEvent, CalendarStore
from local_calendar.identity import AuthConfig, resolve_group, resolve_private
from local_calendar.service import AtomicService, Decision


def _store(tmp_path) -> CalendarStore:
    return CalendarStore(tmp_path / "c.db")


def _svc(store, calls):
    """AtomicService with an injected interpreter (no Needle engine)."""
    svc = AtomicService.__new__(AtomicService)
    svc.store = store
    svc.tools = {}
    svc.lock = threading.Lock()
    svc.interpret = lambda text: calls
    return svc


def _mk(store, title, day, hour, cal_id, people=("Ich",)):
    start = cal.datetime.combine(cal.now().date() + timedelta(days=day),
                                 cal.time(hour, 0))
    return store.add(CalendarEvent(title=title, start=start,
                                   end=start + timedelta(hours=1),
                                   participants=list(people)), calendar_id=cal_id)


# --------------------------------------------------------------- §22 isolation
def test_two_personal_calendars_are_isolated(tmp_path):
    store = _store(tmp_path)
    a = resolve_private(store, 111, 111, "Anna")
    b = resolve_private(store, 222, 222, "Ben")
    _mk(store, "Arzt", 1, 10, a.target_calendar_id, ("Anna",))
    # B does not see A's event
    b_events = store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                    calendar_ids=b.read_calendar_ids)
    assert b_events == []
    a_events = store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                    calendar_ids=a.read_calendar_ids)
    assert [e.title for e in a_events] == ["Arzt"]
    assert a.target_calendar_id != b.target_calendar_id


def test_group_shares_titles_but_not_private_ones(tmp_path):
    store = _store(tmp_path)
    a = resolve_private(store, 111, 111, "Anna")
    g = resolve_group(store, 500, 111, "Anna")
    assert g.actor_person_id == a.actor_person_id
    # private event of another member is not in the group read scope
    _mk(store, "Psychotherapie", 2, 9, a.target_calendar_id, ("Anna",))
    group_events = store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                        calendar_ids=g.read_calendar_ids)
    assert group_events == []
    # a shared group event is visible
    _mk(store, "Teammeeting", 2, 14, g.target_calendar_id, ("Anna", "Ben"))
    group_events = store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                        calendar_ids=g.read_calendar_ids)
    assert [e.title for e in group_events] == ["Teammeeting"]


def test_same_title_in_two_calendars_never_mixes(tmp_path):
    store = _store(tmp_path)
    a = resolve_private(store, 111, 111, "Anna")
    b = resolve_private(store, 222, 222, "Ben")
    _mk(store, "Zahnarzt", 1, 10, a.target_calendar_id, ("Anna",))
    _mk(store, "Zahnarzt", 1, 11, b.target_calendar_id, ("Ben",))
    a_ids = [e.id for e in store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                                calendar_ids=a.read_calendar_ids)]
    b_ids = [e.id for e in store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                                calendar_ids=b.read_calendar_ids)]
    assert len(a_ids) == 1 and len(b_ids) == 1 and a_ids != b_ids


# -------------------------------------------------------------------- §8 auth
def test_auth_private_and_group():
    cfg = AuthConfig(owner_user_id=1, allowed_user_ids={2}, allowed_chat_ids={500})
    assert cfg.is_authorized(1, 1, "private")
    assert cfg.is_authorized(2, 2, "private")
    assert not cfg.is_authorized(9, 9, "private")
    # group: chat AND user must be allowed
    assert cfg.is_authorized(2, 500, "group")
    assert not cfg.is_authorized(9, 500, "group")   # unknown sender
    assert not cfg.is_authorized(1, 999, "group")   # unknown chat


def test_legacy_owner_chat_backwards_compatible():
    cfg = AuthConfig(owner_chat_id=777)
    assert cfg.is_authorized(555, 777, "private")   # no user id configured
    assert not cfg.is_authorized(555, 888, "private")


# ------------------------------------------------------------ §25 atomic writes
def _create_call(title, day="morgen", time="14:00"):
    return [{"name": "calendar_create",
             "arguments": {"title": title, "date": day, "time": time}}]


def test_create_needs_confirm(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Anna")
    svc = _svc(store, _create_call("Zahnarzt"))
    d = svc.prepare("Trag morgen 14 Uhr Zahnarzt ein", ctx)
    assert d.kind == "write_proposal" and d.proposal["token"]
    # DB unchanged before confirm
    assert store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                calendar_ids=ctx.read_calendar_ids) == []
    out = svc.confirm(d.proposal["token"], ctx)
    assert out["ok"]
    assert len(store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                    calendar_ids=ctx.read_calendar_ids)) == 1
    # token can only be used once
    assert not svc.confirm(d.proposal["token"], ctx)["ok"]


def test_cancel_leaves_db_untouched(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Anna")
    svc = _svc(store, _create_call("Zahnarzt"))
    d = svc.prepare("Trag morgen Zahnarzt ein", ctx)
    svc.cancel(d.proposal["token"], ctx)
    assert not svc.confirm(d.proposal["token"], ctx)["ok"]
    assert store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                calendar_ids=ctx.read_calendar_ids) == []


def test_confirm_bound_to_actor_and_chat(tmp_path):
    store = _store(tmp_path)
    a = resolve_private(store, 111, 111, "Anna")
    b = resolve_private(store, 222, 222, "Ben")
    svc = _svc(store, _create_call("Zahnarzt"))
    d = svc.prepare("Trag morgen Zahnarzt ein", a)
    assert not svc.confirm(d.proposal["token"], b)["ok"]  # other actor
    assert svc.confirm(d.proposal["token"], a)["ok"]


def test_expired_proposal_rejected(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Anna")
    svc = _svc(store, _create_call("Zahnarzt"))
    d = svc.prepare("Trag morgen Zahnarzt ein", ctx)
    with store._conn() as c:  # push expiry into the past
        c.execute("UPDATE action_proposals SET expires_at=? WHERE token=?",
                  (store._iso(cal.now() - timedelta(minutes=1)), d.proposal["token"]))
    assert not svc.confirm(d.proposal["token"], ctx)["ok"]


def test_stale_move_rejected(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Anna")
    ev = _mk(store, "Zahnarzt", 1, 14, ctx.target_calendar_id, ("Anna",))
    svc = _svc(store, [{"name": "calendar_move",
                        "arguments": {"title": "Zahnarzt", "time": "16:00"}}])
    d = svc.prepare("Verschieb Zahnarzt auf 16 Uhr", ctx)
    assert d.kind == "write_proposal"
    # someone else changes the event between preview and confirm
    ev2 = store.get(ev.id).model_copy(update={"title": "Zahnarzt (geändert)"})
    store.update(ev2)
    out = svc.confirm(d.proposal["token"], ctx)
    assert not out["ok"] and "geändert" in out["message"].lower()


def test_multi_intent_rejected_no_mutation(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Anna")
    svc = _svc(store, _create_call("Sport") + [{"name": "calendar_delete",
                                               "arguments": {"title": "Zahnarzt"}}])
    d = svc.prepare("Trag morgen Sport ein und lösch Zahnarzt", ctx)
    assert d.kind == "multi_rejected"
    assert store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                calendar_ids=ctx.read_calendar_ids) == []


def test_ambiguous_delete_no_proposal(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Anna")
    _mk(store, "Zahnarzt", 1, 14, ctx.target_calendar_id, ("Anna",))
    _mk(store, "Zahnarzt", 2, 14, ctx.target_calendar_id, ("Anna",))
    svc = _svc(store, [{"name": "calendar_delete", "arguments": {"title": "Zahnarzt"}}])
    d = svc.prepare("Lösch Zahnarzt", ctx)
    assert d.kind == "ambiguous"
    assert store.events_between(cal.now(), cal.now() + timedelta(days=7),
                                calendar_ids=ctx.read_calendar_ids) != []


def test_read_executes_immediately(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Anna")
    _mk(store, "Zahnarzt", 1, 14, ctx.target_calendar_id, ("Anna",))
    svc = _svc(store, [{"name": "calendar_list", "arguments": {"date": "morgen"}}])
    d = svc.prepare("Was habe ich morgen?", ctx)
    assert d.kind == "read" and "Zahnarzt" in d.message


def test_offtopic_no_action(tmp_path):
    store = _store(tmp_path)
    ctx = resolve_private(store, 111, 111, "Anna")
    svc = _svc(store, [])
    d = svc.prepare("Wie wird das Wetter morgen?", ctx)
    assert d.kind == "no_action"


# ------------------------------------------------------------- §26 concurrency
def test_interpret_serialized_by_lock(tmp_path):
    """Concurrent interpret() calls must not interleave reset/complete (§15)."""
    import time

    class FakeNeedle:
        def __init__(self):
            self.order: list[str] = []

        def reset(self):
            pass

        def complete(self, text):
            self.order.append("start")
            time.sleep(0.02)
            self.order.append("end")
            return {"function_calls": []}

    store = _store(tmp_path)
    svc = AtomicService.__new__(AtomicService)
    svc.store = store
    svc.lock = threading.Lock()
    svc.needle = FakeNeedle()
    ts = [threading.Thread(target=svc.interpret, args=("x",)) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # every start is immediately followed by its own end — fully serialized
    assert svc.needle.order == ["start", "end"] * 4


# --------------------------------------------------------- §23 availability
def _group_with_members(store):
    a = resolve_group(store, 500, 111, "Anna")
    store.add_member(a.target_calendar_id, store.ensure_person("Ben", 222))
    store.add_member(a.target_calendar_id, store.ensure_person("Cara", 333))
    a.member_person_ids = store.members_of(a.target_calendar_id)
    return a


def test_group_lanes_hide_private_titles(tmp_path):
    from local_calendar import views
    store = _store(tmp_path)
    a = _group_with_members(store)
    anna = store.ensure_person("Anna"); ben = store.ensure_person("Ben")
    cara = store.ensure_person("Cara")
    for pid, hour, title in ((anna, 10, "Arzt"), (ben, 11, "Sport"),
                             (cara, 14, "Kino")):
        store.add(CalendarEvent(title=title, start=cal.datetime.combine(
            cal.now().date() + timedelta(days=1), cal.time(hour, 0)),
            end=cal.datetime.combine(cal.now().date() + timedelta(days=1),
                                     cal.time(hour + 1, 0)),
            participants=[store.person(pid)["display_name"]]),
            calendar_id=store.calendar_of_person(pid))
    v = views.build_day_view(store, a, cal.now().date() + timedelta(days=1))
    assert len(v.lanes) == 3
    by_name = {l.name: l for l in v.lanes}
    assert by_name["Anna"].blocks[0].title == "Arzt"      # own title visible
    assert by_name["Ben"].blocks[0].title is None         # other member masked
    assert by_name["Cara"].blocks[0].title is None


def test_group_shared_event_blocks_participants_only(tmp_path):
    from local_calendar import views
    store = _store(tmp_path)
    a = _group_with_members(store)
    anna = store.ensure_person("Anna"); ben = store.ensure_person("Ben")
    day = cal.now().date() + timedelta(days=1)
    store.add(CalendarEvent(title="Teammeeting",
                            start=cal.datetime.combine(day, cal.time(13, 0)),
                            end=cal.datetime.combine(day, cal.time(14, 0)),
                            participants=["Anna", "Ben"]),
              calendar_id=a.target_calendar_id)
    v = views.build_day_view(store, a, day)
    by_name = {l.name: l for l in v.lanes}
    assert [c.title for c in v.shared] == ["Teammeeting"]
    assert by_name["Anna"].blocks and by_name["Anna"].blocks[0].shared
    assert by_name["Ben"].blocks and by_name["Ben"].blocks[0].shared
    assert by_name["Cara"].blocks == []                    # not a participant


def test_common_free_slots(tmp_path):
    from local_calendar import views
    store = _store(tmp_path)
    a = _group_with_members(store)
    day = cal.now().date() + timedelta(days=1)
    for name, hour in (("Anna", 10), ("Ben", 11), ("Cara", 14)):
        store.add(CalendarEvent(title="X", start=cal.datetime.combine(day, cal.time(hour)),
                                end=cal.datetime.combine(day, cal.time(hour + 1)),
                                participants=[name]),
                  calendar_id=store.calendar_of_person(store.ensure_person(name)))
    v = views.build_day_view(store, a, day)
    # 9-10 and 12-14 and 15-17 are common free (work 9-17)
    assert v.free, "expected common free slots"


def test_view_renderers_smoke(tmp_path):
    from local_calendar import render, views
    store = _store(tmp_path)
    a = _group_with_members(store)
    day = cal.now().date()
    dv = views.build_day_view(store, a, day)
    wv = views.build_week_view(store, a, day - timedelta(days=day.weekday()))
    for png in (render.render_day_view(dv), render.render_week_view(wv)):
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 500
    # empty views must not crash (plan §23: 0 events)
    empty = resolve_private(store, 999, 999, "Zoe")
    dv0 = views.build_day_view(store, empty, day)
    assert render.render_day_view(dv0)[:4] == b"\x89PNG"
