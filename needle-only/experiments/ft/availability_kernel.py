#!/usr/bin/env python3
"""Availability-Kernel: Bitset-/Slot-Matrix als Projektion des Event-Stores.

Idee (Nutzer): SQLite-Events mit halboffenen Intervallen bleiben die Wahrheit;
daraus wird eine billige Availability-Repräsentation (Tage × Slots × Personen)
abgeleitet. Überlappungen/Lücken werden dann zu OR/AND auf Bitsets.

Dieses Skript liefert:
  --selftest  gleiche Ergebnisse wie die bestehende intervallbasierte
              `find_free_slots` (Identität auf randomisierten Fixtures)
  --bench     CPU-Zeit-Vergleich (Interval vs. Bitset) über Personen/Horizonte

Kein Produktivpfad — reiner Prototyp/Ablation. Es wird nichts migriert.

Usage:
  PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/availability_kernel.py --selftest
  PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/availability_kernel.py --bench
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
import tempfile
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from local_calendar import calendar as cal  # noqa: E402

SLOT_MIN = 15
WORK_START, WORK_END = time(9, 0), time(17, 0)


def build_busy_matrix(store, persons: list[str], first_day: date, last_day: date,
                      slot_minutes: int = SLOT_MIN) -> tuple[list[list[int]], int, int]:
    """Bitsets: busy[person][day] = int-Bitmaske über die Slots des Arbeitstags."""
    ndays = (last_day - first_day).days + 1
    per_day = ((WORK_END.hour * 60 + WORK_END.minute)
               - (WORK_START.hour * 60 + WORK_START.minute)) // slot_minutes
    busy = [[0] * ndays for _ in persons]
    d0 = datetime.combine(first_day, WORK_START)
    d1 = datetime.combine(last_day + timedelta(days=1), time(0, 0))
    base_min = WORK_START.hour * 60 + WORK_START.minute
    for i, p in enumerate(persons):
        for ev in store.events_between(d0, d1, person=p):
            s = max(ev.start, d0)
            e = min(ev.end, d1)
            day = (s.date() - first_day).days
            while s < e and day < ndays:
                day_start = datetime.combine(first_day + timedelta(days=day), WORK_START)
                day_end = datetime.combine(first_day + timedelta(days=day), WORK_END)
                a = max(s, day_start)
                b = min(e, day_end)
                if a < b:
                    m0 = max(0, (a.hour * 60 + a.minute - base_min) // slot_minutes)
                    m1 = min(per_day, -(-(b.hour * 60 + b.minute - base_min) // slot_minutes))
                    for m in range(m0, m1):
                        busy[i][day] |= (1 << m)
                s = day_end if e > day_end else e
                day += 1
    return busy, ndays, per_day


def matrix_free_slots(store, persons: list[str], first_day: date, last_day: date,
                      duration_min: int = 60, slot_minutes: int = SLOT_MIN
                      ) -> list[tuple[datetime, datetime]]:
    """Gleiche Semantik wie cal.find_free_slots: Arbeitstag, Werktage (außer einzelner
    Tag), freie Läufe >= duration, chronologisch, max 5."""
    busy, ndays, per_day = build_busy_matrix(store, persons, first_day, last_day, slot_minutes)
    all_busy = [0] * ndays
    for i in range(len(persons)):
        for d in range(ndays):
            all_busy[d] |= busy[i][d]
    free_mask = [((1 << per_day) - 1) & ~all_busy[d] for d in range(ndays)]
    need = max(1, -(-duration_min // slot_minutes))
    slots: list[tuple[datetime, datetime]] = []
    for d in range(ndays):
        day = first_day + timedelta(days=d)
        if day.weekday() >= 5 and first_day != last_day:
            continue
        m = 0
        while m < per_day and len(slots) < 5:
            if free_mask[d] >> m & 1:
                start_m = m
                while m < per_day and (free_mask[d] >> m & 1):
                    m += 1
                if m - start_m >= need:
                    base = WORK_START.hour * 60 + WORK_START.minute
                    s = datetime.combine(day, time(0, 0)) + timedelta(minutes=base + start_m * slot_minutes)
                    e = datetime.combine(day, time(0, 0)) + timedelta(minutes=base + m * slot_minutes)
                    slots.append((s, e))
            else:
                m += 1
    return slots


def _fixture(seed: int, persons: int, days: int, offgrid: bool = False):
    """Random fixtures. offgrid=True adds non-15min times, odd durations and
    day-boundary/all-day/multi-day/weekend/overlap cases."""
    rng = random.Random(seed)
    store = cal.CalendarStore(Path(tempfile.mkdtemp()) / "avail.db")
    names = ["Lisa", "Max", "Jana", "Peter", "Sophie", "Tim", "Lena", "Jonas"][:persons]
    today = cal.now().date()
    for _ in range(rng.randint(3, 12)):
        p = rng.choice(names)
        day = today + timedelta(days=rng.randint(0, days - 1))
        h0 = rng.choice([9, 10, 11, 13, 14, 15, 16])
        if offgrid:
            minute = rng.choice([0, 5, 10, 17, 23, 41, 50])
            dur = rng.choice([20, 35, 45, 70, 110])
        else:
            minute, dur = 0, rng.choice([30, 60, 90, 120])
        t = datetime.combine(day, time(h0, minute))
        store.add(cal.CalendarEvent(title=f"T{rng.randint(0, 9)}", start=t,
                                    end=t + timedelta(minutes=dur), participants=[p]))
    if offgrid:
        d0 = today
        edge = [
            ("edge_early", datetime.combine(d0, time(8, 50)),
             datetime.combine(d0, time(9, 20)), [names[0]]),
            ("edge_late", datetime.combine(d0, time(16, 40)),
             datetime.combine(d0, time(17, 10)), [names[0]]),
            ("all_day", datetime.combine(d0 + timedelta(days=1), time(0, 0)),
             datetime.combine(d0 + timedelta(days=2), time(0, 0)), [names[0]]),
            ("multi_day", datetime.combine(d0 + timedelta(days=2), time(9, 7)),
             datetime.combine(d0 + timedelta(days=4), time(12, 3)),
             [names[min(1, persons - 1)]]),
            ("weekend", datetime.combine(d0 + timedelta(days=5), time(10, 13)),
             datetime.combine(d0 + timedelta(days=5), time(11, 47)),
             [names[0]]),
            ("overlap", datetime.combine(d0, time(11, 5)),
             datetime.combine(d0, time(12, 50)),
             [names[min(1, persons - 1)]]),
        ]
        for title, s, e, parts in edge:
            store.add(cal.CalendarEvent(title=title, start=s, end=e, participants=parts))
    return store, names


def _slot_is_genuinely_free(store, persons, slots) -> bool:
    """Safety property: a slot the matrix calls free must not overlap any
    real event of any person inside the work window."""
    for s, e in slots:
        for p in persons:
            for ev in store.events_between(s - timedelta(days=1), e + timedelta(days=1),
                                           person=p):
                if ev.start < e and ev.end > s:
                    return False
    return True


def _free_minutes(slots) -> int:
    return sum(int((e - s).total_seconds() // 60) for s, e in slots)


def selftest(n: int = 60) -> int:
    # 1) on-grid: must be IDENTICAL to the interval logic
    grid_mism = 0
    for seed in range(n):
        rng = random.Random(seed)
        persons = rng.randint(1, 4)
        days = rng.choice([1, 3, 5, 7, 14])
        store, names = _fixture(seed, persons, days)
        first, last = cal.now().date(), cal.now().date() + timedelta(days=days - 1)
        a = [(s.isoformat(), e.isoformat()) for s, e in
             cal.find_free_slots(store, names, first, last, duration_min=60)]
        b = [(s.isoformat(), e.isoformat()) for s, e in
             matrix_free_slots(store, names, first, last, duration_min=60)]
        if a != b:
            grid_mism += 1
            if grid_mism <= 3:
                print(f"  GRID MISMATCH seed={seed} persons={names} days={days}")
    print(f"on-grid  : {n - grid_mism}/{n} identisch zur Intervall-Logik"
          + (" ✓" if grid_mism == 0 else " ✗"))

    # 2) off-grid/edge: the matrix may be MORE conservative (round up busy),
    #    but must NEVER invent freedom. Report how often it differs.
    unsafe = 0
    conservative = 0
    conservative_5 = 0
    for seed in range(n):
        rng = random.Random(seed)
        persons = rng.randint(1, 4)
        days = rng.choice([3, 5, 7, 14])
        store, names = _fixture(seed, persons, days, offgrid=True)
        first, last = cal.now().date(), cal.now().date() + timedelta(days=days - 1)
        a = cal.find_free_slots(store, names, first, last, duration_min=60)
        b = matrix_free_slots(store, names, first, last, duration_min=60)
        b5 = matrix_free_slots(store, names, first, last, duration_min=60,
                               slot_minutes=5)
        if not _slot_is_genuinely_free(store, names, b):
            unsafe += 1
        if _free_minutes(a) > _free_minutes(b):
            conservative += 1
        if _free_minutes(a) > _free_minutes(b5):
            conservative_5 += 1
    print(f"off-grid : {n} Fixtures · {unsafe} unsichere Freiräume (muss 0 sein)"
          + (" ✓" if unsafe == 0 else " ✗")
          + f" · konservativer ggü. exakt: 15-min {conservative}/{n}, 5-min {conservative_5}/{n}")
    ok = grid_mism == 0 and unsafe == 0
    print("Hinweis: 15-min-Raster ist bewusst konservativ; beliebige Minuten "
          "brauchen ein feineres Raster (z. B. 5 min) oder die Intervall-Logik.")
    return 0 if ok else 1


def bench(reps: int = 40) -> int:
    report = {}
    for persons in (1, 2, 4, 8):
        for days in (7, 30):
            ti, tm = [], []
            for seed in range(reps):
                store, names = _fixture(seed, persons, days)
                first, last = cal.now().date(), cal.now().date() + timedelta(days=days - 1)
                t0 = _time.perf_counter()
                cal.find_free_slots(store, names, first, last)
                ti.append((_time.perf_counter() - t0) * 1000)
                t0 = _time.perf_counter()
                matrix_free_slots(store, names, first, last)
                tm.append((_time.perf_counter() - t0) * 1000)
            report[f"p{persons}_d{days}"] = {
                "interval_ms_median": round(st.median(ti), 3),
                "matrix_ms_median": round(st.median(tm), 3),
                "speedup": round(st.median(ti) / max(st.median(tm), 1e-6), 2)}
    out = HERE / "reports" / "availability_kernel_bench.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"{'case':<10} {'interval ms':>12} {'matrix ms':>10} {'speedup':>8}")
    for k, v in report.items():
        print(f"{k:<10} {v['interval_ms_median']:>12} {v['matrix_ms_median']:>10} {v['speedup']:>8}x")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--bench", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.bench:
        raise SystemExit(bench())
    ap.print_help()
