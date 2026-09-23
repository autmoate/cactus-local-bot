"""Deterministic tests for the eval-mutation classifier (no model needed).

Guards the business metric used by native_agent_bench / arch_bench:
wrong_mutations must count real DB changes that contradict the goal, not
write-call attempts (user review Sep 24).
"""
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments" / "ft"))

from eval_mutations import (classify, db_diff, event_matches,  # noqa: E402
                            mutation_count, read_answer_ok, snapshot)
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402


def _store(tmp_path) -> CalendarStore:
    return CalendarStore(tmp_path / "m.db")


def _tomorrow() -> str:
    return (cal.now().date() + timedelta(days=1)).isoformat()


def test_db_diff_detects_created_deleted_modified(tmp_path):
    store = _store(tmp_path)
    store.add(cal.CalendarEvent(title="Zahnarzt", start=cal.now() + timedelta(days=1),
                                end=cal.now() + timedelta(days=1, hours=1)))
    before = snapshot(store)
    # create
    store.add(cal.CalendarEvent(title="Yoga", start=cal.now() + timedelta(days=2),
                                end=cal.now() + timedelta(days=2, hours=1)))
    # modify
    z = [e for e in store.events_between(cal.now(), cal.now() + timedelta(days=5))
         if e.title == "Zahnarzt"][0]
    cal.move_event(store, z, z.start + timedelta(hours=3), z.end + timedelta(hours=3))
    after = snapshot(store)
    diff = db_diff(before, after)
    assert len(diff["created"]) == 1 and diff["created"][0]["title"] == "Yoga"
    assert len(diff["modified"]) == 1 and diff["modified"][0]["title"] == "Zahnarzt"
    assert diff["deleted"] == []
    assert mutation_count(diff) == 2


def test_deleted_event_is_the_correct_mutation(tmp_path):
    store = _store(tmp_path)
    e = store.add(cal.CalendarEvent(title="Kegelabend", start=cal.now() + timedelta(days=1),
                                    end=cal.now() + timedelta(days=1, hours=1)))
    before = snapshot(store)
    cal.delete_event(store, e.id)
    diff = db_diff(before, snapshot(store))
    correct, wrong = classify(diff, present=[], absent=["Kegelabend"])
    assert (correct, wrong) == (1, 0)


def test_create_instead_of_delete_is_a_wrong_mutation(tmp_path):
    store = _store(tmp_path)
    e = store.add(cal.CalendarEvent(title="Training", start=cal.now() + timedelta(days=1),
                                    end=cal.now() + timedelta(days=1, hours=1)))
    before = snapshot(store)
    # model wrongly creates a second 'Training' instead of deleting
    store.add(cal.CalendarEvent(title="Training", start=cal.now() + timedelta(days=3),
                                end=cal.now() + timedelta(days=3, hours=1)))
    diff = db_diff(before, snapshot(store))
    correct, wrong = classify(diff, present=[], absent=["Training"])
    assert correct == 0 and wrong == 1  # one real wrong DB mutation


def test_no_mutation_gives_zero_wrong(tmp_path):
    store = _store(tmp_path)
    store.add(cal.CalendarEvent(title="Arztbesuch", start=cal.now() + timedelta(days=1),
                                end=cal.now() + timedelta(days=1, hours=1)))
    before = snapshot(store)
    diff = db_diff(before, snapshot(store))
    assert mutation_count(diff) == 0
    correct, wrong = classify(diff, present=[], absent=["Arztbesuch"])
    assert (correct, wrong) == (0, 0)


def test_event_matches_day_time_persons(tmp_path):
    ev = {"title": "Meeting", "start": "2026-09-25T16:00:00", "end": "2026-09-25T17:00:00",
          "all_day": False, "participants": ["Lisa", "Max"]}
    assert event_matches(ev, "meeting", "2026-09-25", "16:00", "Lisa")
    assert not event_matches(ev, "Meeting", "2026-09-25", "15:00", "")
    assert not event_matches(ev, "Meeting", "2026-09-26", "", "")


def test_read_answer_requires_real_content(tmp_path):
    day = _tomorrow()
    empty = [{"tool": "calendar_list", "result": "No entries."}]
    assert not read_answer_ok(empty, [("Zahnarzt", day, "10:00", "")])
    good = [{"tool": "calendar_list",
             "result": f"**Do {day[8:10]}.{day[5:7]}.**\n• 10:00–11:00 Zahnarzt"}]
    assert read_answer_ok(good, [("Zahnarzt", day, "10:00", "")])
    # a find_slot result carries no title -> fails the read-content check
    wrong_tool = [{"tool": "calendar_find_slot",
                   "result": "Free slots:\n• 10:00–17:00"}]
    assert not read_answer_ok(wrong_tool, [("Zahnarzt", day, "10:00", "")])
