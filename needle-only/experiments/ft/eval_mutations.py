#!/usr/bin/env python3
"""Shared mutation classification for the business metrics (eval hardening).

Safety core metric is `wrong_mutations`: actual DB changes that do not match
the goal — NOT `wrong_writes` (write-call attempts, which Python may reject and
which leave the DB untouched). Classification is a DB snapshot diff plus the
tool result, never a plain call count (user review Sep 24).

Used by `native_agent_bench.py` (Track B) and `arch_bench.py` (P0/P1/P2).
"""

from __future__ import annotations

from datetime import timedelta


def snapshot(store, days_back: int = 3, days_fwd: int = 70) -> dict[int, dict]:
    """id -> immutable event fingerprint over the benchmark horizon."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from local_calendar import calendar as cal
    now = cal.now()
    out = {}
    for e in store.events_between(now - timedelta(days=days_back),
                                  now + timedelta(days=days_fwd)):
        out[e.id] = {"title": e.title, "start": e.start.isoformat(),
                     "end": e.end.isoformat(), "all_day": e.all_day,
                     "participants": list(e.participants)}
    return out


def db_diff(before: dict, after: dict) -> dict[str, list]:
    """Actual DB changes between two snapshots (ground truth of mutation)."""
    created = [after[i] for i in after if i not in before]
    deleted = [before[i] for i in before if i not in after]
    modified = [after[i] for i in after
                if i in before and before[i] != after[i]]
    return {"created": created, "deleted": deleted, "modified": modified}


def mutation_count(diff: dict) -> int:
    return len(diff["created"]) + len(diff["deleted"]) + len(diff["modified"])


def event_matches(ev: dict, title: str = "", day_iso: str = "",
                  hm: str = "", persons: str = "") -> bool:
    """Does a snapshot event satisfy a (title, day, time, persons) spec?"""
    if title and title.lower() not in ev["title"].lower():
        return False
    if day_iso and not ev["start"].startswith(day_iso):
        return False
    if hm and ev["start"][11:16] != hm:
        return False
    if persons:
        have = [p.lower() for p in ev["participants"]]
        if not any(p.strip().lower() in have for p in persons.split(",")):
            return False
    return True


def classify(diff: dict, present: list, absent: list) -> tuple[int, int]:
    """Return (correct_mutations, wrong_mutations) against a goal spec.

    present: list of (title, day_iso, hm, persons) that must exist.
    absent:  list of title substrings that must be gone.
    A created/modified event is correct iff it matches a present spec; a
    deleted event is correct iff its title matches an absent spec. Everything
    else is a wrong mutation (e.g. create instead of delete).
    """
    absent_l = [a.lower() for a in absent]
    correct = wrong = 0
    for ev in diff["created"] + diff["modified"]:
        if any(event_matches(ev, *p) for p in present):
            correct += 1
        else:
            wrong += 1
    for ev in diff["deleted"]:
        if any(a in ev["title"].lower() for a in absent_l):
            correct += 1
        else:
            wrong += 1
    return correct, wrong


def read_answer_ok(trace: list[dict], present: list) -> bool:
    """Read correctness: a read tool result must name the expected entries.

    render_events lists every event of the requested range, so the expected
    title and its day ('dd.mm.') must appear in at least one read result.
    Guards against 'no write happened, therefore goal_ok' being enough.
    """
    results = [t.get("result", "") or "" for t in trace
               if t.get("tool") in ("calendar_list", "calendar_find_slot")]
    if not results:
        return False
    blob = "\n".join(results).lower()
    for (title, day_iso, _hm, _persons) in present:
        if title.lower() not in blob:
            return False
        if day_iso:
            dd, mm = day_iso[8:10], day_iso[5:7]
            if f"{dd}.{mm}." not in blob:
                return False
    return True
