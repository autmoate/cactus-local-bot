#!/usr/bin/env python3
"""Render-regression gallery (plan §33, dev-only).

Builds SYNTHETIC fixtures (no real calendar data) and writes PNGs to
artifacts/render-regression/ so the UI can be eyeballed before deployment.

Usage:
    python scripts/render_regression_gallery.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_calendar import render, views  # noqa: E402
from local_calendar.calendar import CalendarEvent, CalendarStore  # noqa: E402
from local_calendar.identity import resolve_group, resolve_private  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "artifacts" / "render-regression"
D = date(2026, 9, 24)  # Thursday


def _add(store, title, start, end, cal_id, people, all_day=False, kind="appointment"):
    store.add(CalendarEvent(title=title, kind=kind, start=start, end=end,
                            all_day=all_day, participants=people),
              calendar_id=cal_id)


def _dt(day, h, m=0):
    return datetime.combine(day, time(h, m))


def _fixtures(store):
    priv = resolve_private(store, 111, 111, "Oll", owner=True)
    store.ensure_personal_calendar(priv.actor_person_id)
    _add(store, "Urlaub", _dt(D, 0), datetime(2026, 9, 28, 0, 0),
         priv.target_calendar_id, ["Oll"], all_day=True, kind="absence")
    _add(store, "Frühstück", _dt(D, 8), _dt(D, 9), priv.target_calendar_id, ["Oll"])
    _add(store, "Zahnarzt", _dt(D, 11, 30), _dt(D, 12), priv.target_calendar_id, ["Oll"])
    _add(store, "Büro", _dt(date(2026, 9, 29), 9), _dt(date(2026, 9, 29), 16),
         priv.target_calendar_id, ["Oll"])

    grp = resolve_group(store, 500, 111, "Oll", owner=True)
    store.ensure_personal_calendar(grp.actor_person_id)
    for uid, name in ((222, "Lisa"), (333, "Max")):
        pid = store.ensure_person(name, uid)
        store.ensure_personal_calendar(pid)
        store.add_member(grp.target_calendar_id, pid)
    grp.member_person_ids = store.members_of(grp.target_calendar_id)
    _add(store, "Teammeeting", _dt(D, 13), _dt(D, 14), grp.target_calendar_id,
         ["Oll", "Lisa"])
    _add(store, "Privatarzt", _dt(D, 10), _dt(D, 11),
         store.calendar_of_person(store.ensure_person("Lisa")), ["Lisa"])
    _add(store, "Sport", _dt(D + date.resolution, 17),
         _dt(D + date.resolution, 18),
         store.calendar_of_person(store.ensure_person("Max")), ["Max"])
    return priv, grp


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        store = CalendarStore(Path(td) / "gallery.db")
        priv, grp = _fixtures(store)
        outputs = {
            "private_today.png": render.render_day_view(
                views.build_day_view(store, priv, D)),
            "private_week.png": render.render_week_view(
                views.build_week_view(store, priv, date(2026, 9, 21))),
            "group_today.png": render.render_day_view(
                views.build_day_view(store, grp, D)),
            "group_week.png": render.render_week_view(
                views.build_week_view(store, grp, date(2026, 9, 21))),
            "absence_plus_events.png": render.render_day_view(
                views.build_day_view(store, priv, D)),
            "office_single_day.png": render.render_day_view(
                views.build_day_view(store, priv, date(2026, 9, 29))),
        }
    for name, png in outputs.items():
        (OUT / name).write_bytes(png)
        assert png[:8] == b"\x89PNG\r\n\x1a\n", name
        print(f"wrote {OUT / name} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
