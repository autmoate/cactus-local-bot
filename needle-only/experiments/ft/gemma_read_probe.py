#!/usr/bin/env python3
"""Gemma als Read-Handler (Prototyp): Python rechnet, Gemma formuliert.

Design: KEINE Mutationen durch Gemma. Python liefert deterministische Fakten
(Snapshot / Availability-Kernel) als kompakten Text; Gemma formuliert daraus eine
natürliche Antwort. Läuft nur, wenn ein lokales Gemma (CACTUS_BASE_URL / serve)
erreichbar ist — sonst wird sauber übersprungen.

Usage:
  PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/gemma_read_probe.py
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "src"))

from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import Gemma  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402
from availability_kernel import matrix_free_slots  # noqa: E402

QUESTIONS = [
    ("simple", "Was habe ich morgen?"),
    ("simple", "Zeig mir meine Termine diese Woche."),
    ("simple", "Was steht am Freitag an?"),
    ("simple", "Habe ich nächste Woche Termine?"),
    ("simple", "Wie viele Termine habe ich morgen?"),
    ("semantic", "Wie voll wird meine Woche?"),
    ("semantic", "Welcher Tag nächste Woche ist am vollsten?"),
    ("semantic", "Wo könnte ich noch ein 90-Minuten-Meeting unterbringen?"),
    ("semantic", "Wann habe ich diese Woche den längsten freien Block?"),
    ("semantic", "Was habe ich vor meinem Zahnarzttermin?"),
    ("semantic", "Wie stressig ist mein Donnerstag?"),
    ("group", "Wann haben Lisa und ich diese Woche gemeinsam Zeit?"),
    ("group", "Wann könnten Lisa, Max und ich nächste Woche 60 Minuten?"),
    ("group", "Sind Max und ich am Dienstag nachmittags beide frei?"),
    ("group", "Wann hat Lisa diese Woche frei?"),
    ("control", "Wie wird das Wetter morgen?"),
    ("control", "Erzähl mir einen Witz."),
    ("control", "Was kostet ein e-Bike?"),
    ("control", "Danke, das war hilfreich."),
    ("control", "Bist du ein Roboter?"),
]


def _fixture() -> CalendarStore:
    store = CalendarStore(Path(tempfile.mkdtemp()) / "read.db")
    today = cal.now().date()
    plan = [("Zahnarzt", 1, 10, 60, []), ("Teammeeting", 2, 9, 120, ["Lisa"]),
            ("Yoga", 3, 18, 60, ["Lisa"]), ("Workshop", 4, 14, 180, ["Max"]),
            ("Kino", 5, 20, 120, []), ("Friseur", 6, 11, 60, ["Max"]),
            ("Arztbesuch", 7, 9, 60, ["Lisa", "Max"])]
    for title, off, h, dur, parts in plan:
        t = cal.datetime.combine(today + timedelta(days=off), cal.time(h, 0))
        store.add(cal.CalendarEvent(title=title, start=t,
                                    end=t + timedelta(minutes=dur), participants=parts))
    return store


def facts_for(q: str, store: CalendarStore) -> str:
    """Deterministische Fakten (kompakt) — hier absichtlich einfach gehalten."""
    today = cal.now().date()
    persons = [p for p in ("Lisa", "Max") if p.lower() in q.lower()]
    if persons:
        slots = matrix_free_slots(store, persons, today, today + timedelta(days=7), 60)
        return "Gemeinsame freie Slots (60 min, Werktage 9-17): " + (
            "; ".join(f"{s:%a %d.%m. %H:%M}-{e:%H:%M}" for s, e in slots) or "keine")
    evs = store.events_between(cal.datetime.combine(today, cal.time(0, 0)),
                               cal.datetime.combine(today + timedelta(days=7), cal.time(0, 0)))
    return "Termine (7 Tage): " + "; ".join(
        f"{e.start:%a %d.%m. %H:%M} {e.title}" for e in evs)


def main() -> int:
    g = Gemma()
    if not g.available():
        print("Gemma/serve nicht erreichbar — Read-Prototyp übersprungen (Design steht).")
        print("Setup: `cactus serve <gemma-model> --host 127.0.0.1 --port 8080`")
        return 0
    store = _fixture()
    system = ("Du bist ein hilfreicher Kalender-Assistent. Du bekommst die FAKTEN "
              "von Python. Erfinde nichts, nutze nur diese Fakten. Antworte kurz. "
              "Wenn die Frage nichts mit dem Kalender zu tun hat, sage knapp, dass "
              "du nur für den Kalender zuständig bist.")
    rows = []
    for kind, q in QUESTIONS:
        facts = facts_for(q, store)
        t0 = time.perf_counter()
        try:
            answer = g.chat(system, f"Fakten:\n{facts}\n\nFrage: {q}", max_tokens=160)
        except Exception as exc:  # noqa: BLE001
            answer = f"ERROR {type(exc).__name__}: {exc}"
        ms = round((time.perf_counter() - t0) * 1000)
        rows.append({"kind": kind, "question": q, "facts": facts,
                     "answer": answer, "ms": ms})
        print(f"  [{kind:<8}] {q[:44]:46s} {ms:>5}ms | {answer[:70]}")
    lat = [r["ms"] for r in rows]
    print(f"\nGemma-Reads: n={len(rows)} · p50 {round(st.median(lat))}ms "
          f"· p95 {round(sorted(lat)[int(len(lat)*0.95)-1])}ms")
    (HERE / "reports" / "gemma_read_probe.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1))
    print("Hinweis: Qualität ist manuell zu bewerten (Fakten dürfen nicht erfunden werden).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
