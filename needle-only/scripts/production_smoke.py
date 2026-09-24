#!/usr/bin/env python3
"""Production smoke for Kalender-Pin (plan §27), real N2-FT, no research eval.

Verifies the frozen V1 contract end to end against the loaded fine-tune:
create/list/move/delete/find_slot/off-topic/ambiguous/multi, and above all that
NOTHING mutates without a confirm. No Gemma, no N3.

Usage:
  PYTHONPATH=src .venv/bin/python scripts/production_smoke.py \
      --weights experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from local_calendar import calendar as cal                     # noqa: E402
from local_calendar.calendar import CalendarEvent, CalendarStore  # noqa: E402
from local_calendar.identity import resolve_private            # noqa: E402
from local_calendar.service import AtomicService               # noqa: E402


def _mk(store, title, day, hour, cal_id):
    s = cal.datetime.combine(cal.now().date() + timedelta(days=day), cal.time(hour, 0))
    return store.add(CalendarEvent(title=title, start=s, end=s + timedelta(hours=1),
                                   participants=["Ich"]), calendar_id=cal_id)


def _count(store, ctx):
    return len(store.events_between(cal.now(), cal.now() + timedelta(days=30),
                                    calendar_ids=ctx.read_calendar_ids))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=None)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    db = args.db or str(Path(tempfile.mkdtemp()) / "smoke.db")
    store = CalendarStore(db)
    ctx = resolve_private(store, 1, 1, "Ich")
    svc = AtomicService(store, weights=args.weights)
    print(f"  weights: {args.weights or 'base'}  ·  db: {db}")

    results = []

    def check(name, cond, detail=""):
        results.append(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name:<34} {detail}")

    # 1) create needs confirm
    before = _count(store, ctx)
    d = svc.prepare("Trag morgen 14 Uhr Zahnarzt ein.", ctx)
    check("create -> proposal", d.kind == "write_proposal", d.kind)
    check("create no mutation before confirm", _count(store, ctx) == before)
    if d.kind == "write_proposal":
        out = svc.confirm(d.proposal["token"], ctx)
        check("confirm create -> one mutation", out.get("ok") and _count(store, ctx) == before + 1)

    # 2) list is a read
    d = svc.prepare("Was habe ich morgen?", ctx)
    check("list -> read (immediate)", d.kind == "read" and "Zahnarzt" in d.message, d.kind)

    # 3) move needs confirm
    d = svc.prepare("Verschieb Zahnarzt auf 16 Uhr.", ctx)
    check("move -> proposal", d.kind == "write_proposal", d.kind)
    if d.kind == "write_proposal":
        check("confirm move -> ok", svc.confirm(d.proposal["token"], ctx).get("ok"))

    # 4) delete needs confirm
    d = svc.prepare("Lösch Zahnarzt morgen.", ctx)
    check("delete -> proposal", d.kind == "write_proposal", d.kind)
    if d.kind == "write_proposal":
        check("confirm delete -> ok", svc.confirm(d.proposal["token"], ctx).get("ok"))

    # 5) find_slot is a read (fixture created directly)
    _mk(store, "Block", 2, 9, ctx.target_calendar_id)
    d = svc.prepare("Wann habe ich übermorgen frei?", ctx)
    check("find_slot -> read", d.kind == "read", d.kind)

    # 6) off-topic: must never mutate (a read misclassification is harmless)
    c0 = _count(store, ctx)
    d = svc.prepare("Wie wird das Wetter morgen?", ctx)
    check("off-topic -> no mutation", _count(store, ctx) == c0,
          f"kind={d.kind}")

    # 7) ambiguous delete (two same titles)
    _mk(store, "Kino", 2, 18, ctx.target_calendar_id)
    _mk(store, "Kino", 3, 18, ctx.target_calendar_id)
    c0 = _count(store, ctx)
    d = svc.prepare("Lösch Kino.", ctx)
    check("ambiguous delete -> ambiguous", d.kind in ("ambiguous", "error"), d.kind)
    check("ambiguous -> no mutation", _count(store, ctx) == c0)

    # 8) multi-intent: never several writes; a single call stays a proposal
    c0 = _count(store, ctx)
    d = svc.prepare("Trag morgen Sport ein und lösch Kino.", ctx)
    check("multi -> not auto-executed", d.kind != "read", d.kind)
    check("multi -> no mutation without confirm", _count(store, ctx) == c0)

    ok = all(results)
    print(f"\n  SMOKE {'PASS' if ok else 'FAIL'} ({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
