"""Unit tests for the deterministic calendar core (no models needed)."""

import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import tempfile

from local_calendar import calendar as cal
from local_calendar.agent import Agent, execute_call
from local_calendar.calendar import (
    CalendarEvent, CalendarStore, _canonical_person, create_event, delete_event,
    event_overlaps, find_free_slots, list_events, move_event, parse_persons,
    render_events, resolve_date, resolve_time, resolve_timing,
)

import pytest


@pytest.fixture()
def store(tmp_path):
    return CalendarStore(tmp_path / "test.db")


def ev(title, start, end, **kw):
    return CalendarEvent(title=title, start=start, end=end, **kw)


def dt(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi)


# ------------------------------------------------------------------ date resolution

def test_resolve_relative_days():
    today = date(2026, 9, 13)  # Sunday
    assert resolve_date("today", today) == today
    assert resolve_date("heute", today) == today
    assert resolve_date("tomorrow", today) == date(2026, 9, 14)
    assert resolve_date("morgen", today) == date(2026, 9, 14)
    assert resolve_date("day after tomorrow", today) == date(2026, 9, 15)
    assert resolve_date("übermorgen", today) == date(2026, 9, 15)


def test_resolve_weekday_next_occurrence():
    today = date(2026, 9, 13)  # Sunday
    assert resolve_date("monday", today) == date(2026, 9, 14)
    assert resolve_date("dienstag", today) == date(2026, 9, 15)
    assert resolve_date("next friday", today) == date(2026, 9, 18)
    assert resolve_date("nächste woche", today) == date(2026, 9, 14)
    # same weekday -> next week, not today
    assert resolve_date("sunday", today) == date(2026, 9, 20)


def test_resolve_iso_and_german_dates():
    today = date(2026, 7, 20)
    assert resolve_date("2026-08-03", today) == date(2026, 8, 3)
    assert resolve_date("3.8.", today) == date(2026, 8, 3)  # future this year
    assert resolve_date("07.09.2026", today) == date(2026, 9, 7)
    # past day/month without year rolls to next year
    assert resolve_date("3.2.", today) == date(2027, 2, 3)
    assert resolve_date("3 august", today) == date(2026, 8, 3)
    assert resolve_date("august 3", date(2026, 9, 13)) == date(2027, 8, 3)
    assert resolve_date("3.8.2027", today) == date(2027, 8, 3)


def test_resolve_date_invalid_and_garbage():
    today = date(2026, 9, 13)
    assert resolve_date("29.2.2027", today) is None  # not a leap year
    assert resolve_date("29.2.2028", today) == date(2028, 2, 29)  # leap year
    assert resolve_date("", today) is None
    assert resolve_date("xyz", today) is None


# ------------------------------------------------------------------ time resolution

def test_resolve_time_forms():
    from local_calendar.calendar import resolve_time
    assert resolve_time("14:00") == time(14, 0)
    assert resolve_time("14.30") == time(14, 30)
    assert resolve_time("14 uhr") == time(14, 0)
    assert resolve_time("9") == time(9, 0)
    assert resolve_time("2 pm") == time(14, 0)
    assert resolve_time("12 am") == time(0, 0)
    assert resolve_time("noon") == time(12, 0)
    assert resolve_time("mittag") == time(12, 0)
    assert resolve_time("afternoon") == time(14, 0)
    assert resolve_time("nachmittag") == time(14, 0)
    assert resolve_time("evening") == time(18, 0)
    assert resolve_time("abend") == time(18, 0)
    assert resolve_time("25:00") is None
    assert resolve_time("xyz") is None
    assert resolve_time("") is None


def test_resolve_timing_timed():
    ref = datetime(2026, 9, 13, 10, 0)
    start, end, all_day = resolve_timing("tomorrow", "14:00", "", 60, ref)
    assert (start, end, all_day) == (datetime(2026, 9, 14, 14, 0),
                                     datetime(2026, 9, 14, 15, 0), False)


def test_resolve_timing_periods():
    ref = datetime(2026, 9, 13, 10, 0)
    start, end, all_day = resolve_timing("morgen", "nachmittag", "", 90, ref)
    assert start == datetime(2026, 9, 14, 14, 0)
    assert (end - start).seconds // 60 == 90
    assert not all_day


def test_resolve_timing_all_day_single_and_multi():
    ref = datetime(2026, 7, 20, 10, 0)
    start, end, all_day = resolve_timing("morgen", "", "", 60, ref)
    assert all_day and start == datetime(2026, 7, 21) and end == datetime(2026, 7, 22)
    # inclusive end date -> exclusive stored end
    start, end, all_day = resolve_timing("3 august", "", "18 august", 60, ref)
    assert all_day and start == datetime(2026, 8, 3) and end == datetime(2026, 8, 19)


def test_resolve_timing_year_boundary_range():
    ref = datetime(2026, 12, 20, 10, 0)
    start, end, all_day = resolve_timing("28.12.", "", "3.1.", 60, ref)
    assert start == datetime(2026, 12, 28)
    assert end == datetime(2027, 1, 4)  # covers through Jan 3rd


def test_resolve_timing_relative_offsets():
    ref = datetime(2026, 9, 13, 10, 0)
    start, end, _ = resolve_timing("in 10 min", "", "", 60, ref)
    assert start == ref + timedelta(minutes=10)
    start, _, _ = resolve_timing("in 2 stunden", "", "", 60, ref)
    assert start == ref + timedelta(hours=2)
    assert resolve_timing("irgendwann", "", "", 60, ref) is None


def test_resolve_timing_dst_transition():
    # 2026-10-25 is the CEST->CET transition in Europe/Berlin; wall clock stays stable
    ref = datetime(2026, 10, 24, 12, 0)
    start, end, all_day = resolve_timing("tomorrow", "09:00", "", 60, ref)
    assert (start.hour, start.minute) == (9, 0)
    assert end - start == timedelta(hours=1)
    # all-day range across the transition keeps date semantics
    start, end, all_day = resolve_timing("24.10.", "", "26.10.", 60, ref)
    assert all_day and end - start == timedelta(days=3)


# ------------------------------------------------------------------ CRUD + overlap

def test_event_overlaps_half_open():
    a = ev("a", dt(2026, 9, 14, 14), dt(2026, 9, 14, 15))
    b = ev("b", dt(2026, 9, 14, 15), dt(2026, 9, 14, 16))
    assert not event_overlaps(a, b)  # touching intervals do not overlap
    c = ev("c", dt(2026, 9, 14, 14, 30), dt(2026, 9, 14, 16))
    assert event_overlaps(a, c) and event_overlaps(c, a)
    d = ev("d", dt(2026, 9, 14, 9), dt(2026, 9, 15))
    assert event_overlaps(a, d)  # all-day covers the timed event


def test_create_list_delete_roundtrip(store):
    e1 = create_event(store, "Zahnarzt", dt(2026, 9, 14, 14), dt(2026, 9, 14, 15),
                      False, ["Ich"])
    assert e1.id is not None
    e2 = create_event(store, "Urlaub", dt(2026, 9, 21), dt(2026, 9, 26),
                      True, ["Ich"], kind="absence")
    events = list_events(store, start=dt(2026, 9, 1), end=dt(2026, 10, 1))
    assert {e.title for e in events} == {"Zahnarzt", "Urlaub"}
    assert delete_event(store, e1.id)
    events = list_events(store, start=dt(2026, 9, 1), end=dt(2026, 10, 1))
    assert [e.title for e in events] == ["Urlaub"]
    assert not delete_event(store, 99999)


def test_participant_filtering(store):
    create_event(store, "Meeting", dt(2026, 9, 14, 10), dt(2026, 9, 14, 11),
                 False, ["Ich", "Lisa"])
    create_event(store, "Zahnarzt Lisa", dt(2026, 9, 15, 9), dt(2026, 9, 15, 10),
                 False, ["Lisa"])
    mine = list_events(store, person="Ich", start=dt(2026, 9, 1), end=dt(2026, 10, 1))
    assert [e.title for e in mine] == ["Meeting"]
    lisas = list_events(store, person="lisa", start=dt(2026, 9, 1), end=dt(2026, 10, 1))
    assert {e.title for e in lisas} == {"Meeting", "Zahnarzt Lisa"}


def test_find_by_title_substring_both_ways(store):
    from datetime import datetime as _dt
    create_event(store, "Zahnarzt", dt(2026, 9, 14, 14), dt(2026, 9, 14, 15), False, ["Ich"])
    create_event(store, "Zahnarzt Kontrolle", dt(2026, 10, 1, 9), dt(2026, 10, 1, 10),
                 False, ["Ich"])
    ref = dt(2026, 9, 14, 10)  # deterministic 'now' — no date-dependent flake
    # query longer than stored title
    assert store.find_by_title("Zahnarzttermin", near=ref).title == "Zahnarzt"
    # query shorter: prefer upcoming relative to ref
    found = store.find_by_title("zahn", near=ref)
    assert found.title == "Zahnarzt"  # earliest upcoming wins


def test_move_event_preserves_duration_and_all_day_span(store):
    e = create_event(store, "Meeting", dt(2026, 9, 14, 14), dt(2026, 9, 14, 15, 30),
                     False, ["Ich"])
    moved = move_event(store, e, dt(2026, 9, 16, 10))
    assert (moved.start, moved.end) == (dt(2026, 9, 16, 10), dt(2026, 9, 16, 11, 30))
    v = create_event(store, "Urlaub", dt(2026, 9, 21), dt(2026, 9, 26), True,
                     ["Ich"], kind="absence")  # 5 days
    moved = move_event(store, v, dt(2026, 10, 5))
    assert moved.all_day and moved.end == dt(2026, 10, 10)


def test_collision_participants_and_absences(store):
    # appointment vs appointment WITHOUT shared participants: no collision
    create_event(store, "Lisa Zahnarzt", dt(2026, 9, 14, 10), dt(2026, 9, 14, 11),
                 False, ["Lisa"])
    b = ev("Max Meeting", dt(2026, 9, 14, 10, 30), dt(2026, 9, 14, 11, 30),
           participants=["Max"])
    assert store.collision(b) is None
    # appointment vs appointment WITH shared participant: overlap is reported
    # as a warning source (never a hard reject, plan §6)
    c = ev("Ich Meeting", dt(2026, 9, 14, 10, 30), dt(2026, 9, 14, 11, 30),
           participants=["Ich", "Lisa"])
    assert store.collision(c).title == "Lisa Zahnarzt"
    # appointment inside one's own vacation: NO collision — an absence never
    # blocks a manual timed appointment (plan §6), but it stays availability-busy
    vac = create_event(store, "Urlaub", dt(2026, 9, 21), dt(2026, 9, 26), True,
                       ["Ich"], kind="absence")
    appt = ev("Meeting", dt(2026, 9, 22, 10), dt(2026, 9, 22, 11),
              participants=["Ich"])
    assert store.collision(appt) is None
    assert store.is_absent([store.ensure_person("Ich")],
                           appt.start, appt.end) is True
    # creating an absence itself never collides
    assert store.collision(vac) is None
    # Lisa's vacation does not block Max
    appt2 = ev("Max Meeting", dt(2026, 9, 22, 10), dt(2026, 9, 22, 11),
               participants=["Max"])
    assert store.collision(appt2) is None


def test_collision_appointment_appointment(store):
    a = create_event(store, "A", dt(2026, 9, 14, 10), dt(2026, 9, 14, 11), False, ["Ich"])
    b = ev("B", dt(2026, 9, 14, 10, 30), dt(2026, 9, 14, 11, 30), participants=["Ich"])
    assert store.collision(b).title == "A"
    c = ev("C", dt(2026, 9, 14, 16), dt(2026, 9, 14, 17), participants=["Ich"])
    assert store.collision(c) is None
    d = ev("D", dt(2026, 9, 14, 12), dt(2026, 9, 14, 13), participants=["Ich"])
    assert store.collision(d) is None  # inside tolerance? 12:00 vs 10-11 -> gap of 1h


def test_month_and_year_boundary_events(store):
    create_event(store, "Monatswechsel", dt(2026, 9, 30, 23), dt(2026, 10, 1, 1),
                 False, ["Ich"])
    events = list_events(store, start=dt(2026, 9, 30), end=dt(2026, 10, 2))
    assert len(events) == 1
    create_event(store, "Jahreswechsel", dt(2026, 12, 31, 18), dt(2027, 1, 1, 2),
                 False, ["Ich"])
    events = list_events(store, start=dt(2026, 12, 31), end=dt(2027, 1, 2))
    assert len(events) == 1


def test_find_free_slots_intersection(store):
    create_event(store, "Lisa busy", dt(2026, 9, 14, 10), dt(2026, 9, 14, 12),
                 False, ["Lisa"])
    create_event(store, "Max busy", dt(2026, 9, 14, 11), dt(2026, 9, 14, 13),
                 False, ["Max"])
    slots = find_free_slots(store, ["Lisa", "Max"],
                            date(2026, 9, 14), date(2026, 9, 14), 60)
    assert slots  # busy 10-13 -> free 09:00-10:00 and 13:00-17:00
    assert slots[0] == (dt(2026, 9, 14, 9), dt(2026, 9, 14, 10))
    assert slots[1] == (dt(2026, 9, 14, 13), dt(2026, 9, 14, 17))
    # absence blocks the whole person range
    create_event(store, "Urlaub Max", dt(2026, 9, 15), dt(2026, 9, 18), True,
                 ["Max"], kind="absence")
    slots = find_free_slots(store, ["Lisa", "Max"],
                            date(2026, 9, 16), date(2026, 9, 16), 60)
    assert slots == []


def test_find_free_slots_weekend_skipped(store):
    sat = date(2026, 9, 19)
    assert find_free_slots(store, ["Ich"], sat, date(2026, 9, 20), 60) == []
    # explicit single weekend day IS searched
    assert find_free_slots(store, ["Ich"], sat, sat, 60)


def test_parse_persons_variants():
    assert parse_persons("") == ["Ich"]
    assert parse_persons(None) == ["Ich"]
    assert parse_persons("Lisa") == ["Lisa"]
    assert parse_persons("ich und max") == ["Ich", "Max"]
    assert parse_persons("Lisa, Max") == ["Lisa", "Max"]
    assert parse_persons(["lisa", " max "]) == ["Lisa", "Max"]


def test_render_events_markdown(store):
    create_event(store, "Urlaub", dt(2026, 9, 21), dt(2026, 9, 23), True,
                 ["Ich"], kind="absence")
    create_event(store, "Zahnarzt", dt(2026, 9, 21, 14), dt(2026, 9, 21, 15),
                 False, ["Ich", "Lisa"])
    text = render_events(list_events(store, start=dt(2026, 9, 1), end=dt(2026, 10, 1)))
    assert "Urlaub" in text and "Mo 21.09." in text
    assert "14:00–15:00 Zahnarzt (Ich, Lisa)" in text


# --------------------------------------------------- text-date precedence fixes

def test_extract_dates_no_roll_and_roll():
    from local_calendar.calendar import extract_dates_from_text as ex
    today = date(2026, 9, 13)
    # move/delete matching: no roll
    assert ex("Move Einkaufen on 8.9. to 9.9.", today) == [date(2026, 9, 8), date(2026, 9, 9)]
    # create semantics: past explicit dates roll to next year
    assert ex("Termin am 9.9. um 14 Uhr", today, roll=True) == [date(2027, 9, 9)]
    assert ex("Termin am 19.9.", today, roll=True) == [date(2026, 9, 19)]
    assert ex("kein datum hier", today) == []


def test_extract_time_from_text():
    from local_calendar.calendar import extract_time_from_text as ext
    assert ext("Verschiebe X am 8.9. auf den 9.9.") is None  # 9.9 is a date, not 9:00
    assert ext("auf den 20.9. um 10 Uhr").hour == 10
    assert ext("Termin 17Uhr").hour == 17
    assert ext("meeting at 14:30") == time(14, 30)


def test_find_by_title_near_accepts_date(store):
    create_event(store, "Einkaufen", dt(2026, 9, 8, 9), dt(2026, 9, 8, 10),
                 False, ["Ich"])
    found = store.find_by_title("Einkaufen", near=date(2026, 9, 8))  # no TypeError
    assert found is not None and found.title == "Einkaufen"


def test_move_target_last_text_date_wins(store):
    e = create_event(store, "Einkaufen", dt(2026, 9, 8, 9), dt(2026, 9, 8, 10),
                     False, ["Ich"])
    # model repeated the source day; the canonical names source AND target
    moved = move_event(store, e, dt(2026, 9, 8, 9))  # simulate model's wrong target
    assert moved.start.date() == date(2026, 9, 8)  # solver stays literal
    # the correction lives in agent._do_move; verified via execute_call:
    from local_calendar.agent import execute_call
    out = execute_call(store, "calendar_move",
                       {"title": "Einkaufen", "date": "2026-09-08", "time": "09:00"},
                       "Move Einkaufen on 8.9. to 9.9.")
    assert out["ok"]
    assert store.events_between(dt(2026, 9, 1), dt(2026, 10, 1))[0].start.date() \
        == date(2026, 9, 9)


# -------------------------------------------------------- telegram-phase units

def test_resolve_date_last_weekday():
    today = date(2026, 9, 13)  # Sunday
    assert resolve_date("letzten dienstag", today) == date(2026, 9, 8)
    assert resolve_date("last friday", today) == date(2026, 9, 11)
    assert resolve_date("letzten sonntag", today) == date(2026, 9, 6)


def test_extract_dates_day_only_range():
    from local_calendar.calendar import extract_dates_from_text as ex
    today = date(2026, 9, 13)
    assert ex("Ich bin vom 23. bis 27. komplett weg.", today, roll=True) == [
        date(2026, 9, 23), date(2026, 9, 27)]
    assert ex("Meeting von 13 bis 16 Uhr", today) == []  # time range, not dates


def test_render_week_png(tmp_path):
    from local_calendar.render import render_week_png
    from PIL import Image
    import io
    store = CalendarStore(tmp_path / "r.db")
    create_event(store, "Zahnarzt", dt(2026, 9, 17, 10), dt(2026, 9, 17, 11),
                 False, ["Ich"])
    create_event(store, "Urlaub", dt(2026, 9, 21), dt(2026, 9, 25), True,
                 ["Ich"], kind="absence")
    png = render_week_png(store, date(2026, 9, 14))
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.width > 800


def test_render_availability_png(tmp_path):
    from local_calendar.render import render_availability_png
    from PIL import Image
    import io
    store = CalendarStore(tmp_path / "a.db")
    create_event(store, "Meeting", dt(2026, 9, 17, 10), dt(2026, 9, 17, 11),
                 False, ["Lisa"])
    png = render_availability_png(store, ["Lisa", "Max"], date(2026, 9, 17))
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.height > 100


def test_agent_session_ids_isolate_pending():
    agent = Agent(CalendarStore(tempfile.mktemp(suffix=".db")), mode="needle")
    agent.pending = {"s1": {"goal": "g1", "observations": [], "question": "q?"}}
    # a message from another session must not consume s1's pending state
    final = None
    for trace in agent.handle("Termin Test morgen um 9 Uhr", session_id="s2"):
        final = trace
    assert "s1" in agent.pending  # untouched
    agent.pending.clear()


# ------------------------------------- real Telegram delete regressions (§30)

def test_delete_generic_title_time_window(store):
    """Real trace: 'Lösche bitte den Termin am 15.9. 10Uhr!' — generic title
    'Termin' matches nothing; the explicit date+time window resolves it."""
    from local_calendar.agent import execute_call
    create_event(store, "Frisör appointment", dt(2026, 9, 15, 10), dt(2026, 9, 15, 11),
                 False, ["Ich"])
    create_event(store, "Meetup", dt(2026, 9, 15, 14), dt(2026, 9, 15, 15),
                 False, ["Ich"])
    out = execute_call(store, "calendar_delete",
                       {"title": "Termin", "date": "15.9. 10:00"},
                       "Lösche den Termin am 15.9. 10Uhr.")
    assert out["ok"], out["message"]
    remaining = store.events_between(dt(2026, 9, 1), dt(2026, 10, 1))
    assert [e.title for e in remaining] == ["Meetup"]


def test_delete_time_window_multi_candidates_asks(store):
    """Two events inside the time window -> ambiguity, nothing deleted."""
    from local_calendar.agent import execute_call
    create_event(store, "A Termin", dt(2026, 9, 15, 9, 45), dt(2026, 9, 15, 10, 15),
                 False, ["Ich"])
    create_event(store, "B Termin", dt(2026, 9, 15, 10, 10), dt(2026, 9, 15, 11),
                 False, ["Ich"])
    out = execute_call(store, "calendar_delete",
                       {"title": "Termin", "date": "15.9. 10Uhr"},
                       "Lösche den Termin am 15.9. 10Uhr!")
    assert not out["ok"] and "Mehrere" in out["message"]
    assert len(store.events_between(dt(2026, 9, 1), dt(2026, 10, 1))) == 2


def test_delete_hard_date_filter_regression(store):
    """Review case: Meeting 09.09. + Meeting 17.09., delete with 12.9.
    -> nothing deleted (hard filter, no ranking fallback)."""
    from local_calendar.agent import execute_call
    create_event(store, "Meeting", dt(2026, 9, 9, 10), dt(2026, 9, 9, 11),
                 False, ["Ich"])
    create_event(store, "Meeting", dt(2026, 9, 17, 10), dt(2026, 9, 17, 11),
                 False, ["Ich"])
    out = execute_call(store, "calendar_delete",
                       {"title": "Meeting", "date": "12.9."},
                       "Lösch Meeting am 12.9.")
    assert not out["ok"]
    assert len(store.events_between(dt(2026, 9, 1), dt(2026, 10, 1))) == 2


def test_delete_ambiguous_titles_asks(store):
    from local_calendar.agent import execute_call
    create_event(store, "Meeting Lisa", dt(2026, 9, 18, 10), dt(2026, 9, 18, 11),
                 False, ["Ich", "Lisa"])
    create_event(store, "Meeting Lisa", dt(2026, 9, 20, 14), dt(2026, 9, 20, 15),
                 False, ["Ich", "Lisa"])
    out = execute_call(store, "calendar_delete", {"title": "Meeting Lisa"},
                       "Lösch das Meeting mit Lisa.")
    assert not out["ok"] and "Mehrere" in out["message"]
    assert len(store.events_between(dt(2026, 9, 1), dt(2026, 10, 1))) == 2


def test_delete_partial_match_both_directions(store):
    """Real trace: 'Lösch Zahnarzttermin' vs stored 'Zahnarzt' — bidirectional
    substring semantics, no regex."""
    from local_calendar.agent import execute_call
    create_event(store, "Zahnarzt", dt(2026, 9, 18, 10), dt(2026, 9, 18, 11),
                 False, ["Ich"])
    out = execute_call(store, "calendar_delete", {"title": "Zahnarzttermin"},
                       "Lösch Zahnarzttermin.")
    assert out["ok"]
    assert store.events_between(dt(2026, 9, 1), dt(2026, 10, 1)) == []


# --------------------------------------------- widget data selection (Phase 4)

def test_week_widget_data_selection(store):
    """Phase 4: the renderer must draw exactly what the store holds — test the
    selection layer (store -> events in week window) for every widget case."""
    from datetime import date as _d
    week = _d(2026, 9, 14)  # Mo 14.09. – So 20.09.
    rng = (dt(2026, 9, 14), dt(2026, 9, 21))
    # 1) normal appointment
    create_event(store, "Zahnarzt", dt(2026, 9, 16, 10), dt(2026, 9, 16, 11),
                 False, ["Ich"])
    # 2) two events on the same day
    create_event(store, "Meeting", dt(2026, 9, 17, 13), dt(2026, 9, 17, 16),
                 False, ["Ich"])
    create_event(store, "Kaffee", dt(2026, 9, 17, 16, 30),
                 dt(2026, 9, 17, 17), False, ["Ich"])
    # 3) multi-day absence + appointment of ANOTHER person in it
    create_event(store, "Urlaub", dt(2026, 9, 14), dt(2026, 9, 19), True,
                 ["Lisa"], kind="absence")
    create_event(store, "Lisas Termin", dt(2026, 9, 15, 9), dt(2026, 9, 15, 10),
                 False, ["Lisa"])
    # 4) long title
    create_event(store, "Sehr langer Titel der ueber die Spalte laufen koennte",
                 dt(2026, 9, 18, 8), dt(2026, 9, 18, 9), False, ["Ich"])
    # 5) event exactly on week start and one at midnight boundary
    create_event(store, "Wochenstart", dt(2026, 9, 14, 7), dt(2026, 9, 14, 8),
                 False, ["Ich"])
    create_event(store, "Ueber Mitternacht", dt(2026, 9, 19, 23),
                 dt(2026, 9, 20, 1), False, ["Ich"])
    # 6) event outside the week (next Sunday)
    create_event(store, "Ausserhalb", dt(2026, 9, 22, 10), dt(2026, 9, 22, 11),
                 False, ["Ich"])

    from local_calendar.render import _events_for
    all_events = _events_for(store, [week + cal.timedelta(days=i)
                                     for i in range(7)], ["all"])
    titles = sorted(e.title for e in all_events)
    assert "Ausserhalb" not in titles  # next week's event is not in view
    assert titles.count("Meeting") == 1  # 2) both events present, no duplicates
    assert "Kaffee" in titles and "Wochenstart" in titles
    assert "Sehr langer Titel der ueber die Spalte laufen koennte" in titles
    assert titles.count("Ueber Mitternacht") == 1
    # 7) participant filter: only Lisa's view
    lisas = _events_for(store, [week + cal.timedelta(days=i) for i in range(7)],
                        ["Lisa"])
    assert {e.title for e in lisas} == {"Urlaub", "Lisas Termin"}
    # Sunday event inside the week range:
    assert "Ueber Mitternacht" in titles


def test_week_widget_after_mutation(store):
    """Phase 4: widget data after create/move/delete reflects the store —
    the renderer draws the fresh truth (auto-refresh data path)."""
    from datetime import date as _d
    from local_calendar.agent import execute_call
    from local_calendar.render import _events_for
    week = _d(2026, 9, 14)
    days = [week + cal.timedelta(days=i) for i in range(7)]
    out = execute_call(store, "calendar_create",
                       {"title": "Neu", "date": "2026-09-17", "time": "10:00"},
                       "Termin Neu am 17.9. um 10 Uhr")
    assert out["ok"]
    assert [e.title for e in _events_for(store, days, ["all"])] == ["Neu"]
    execute_call(store, "calendar_move", {"title": "Neu", "date": "18.9."},
                 "Verschiebe Neu auf den 18.9.")
    assert all(e.start.date() != _d(2026, 9, 17)
               for e in _events_for(store, days, ["all"]))
    execute_call(store, "calendar_delete", {"title": "Neu", "date": "18.9."},
                 "Lösch Neu am 18.9.")
    assert _events_for(store, days, ["all"]) == []
