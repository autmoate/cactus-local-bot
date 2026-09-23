#!/usr/bin/env python3
"""Architektur-Bakeoff — Case-Set (deterministisch, produktionsnah).

Fällt pro Fall: goal, fixture (bekannte Termine), erwarteter Endzustand und ob
eine Rückfrage nötig wäre. Die Checker-Logik lebt in arch_bench.py.

expect:
  present   [(title_substr, "morgen"|"übermorgen"|ISO|"", "HH:MM"|"", persons|"")]
            → dieser Eintrag MUSS im Endzustand so existieren
  absent    [title_substr]           → darf NICHT (mehr) existieren
  no_writes True                     → es darf kein Write passieren (Reads/Off-topic)
  ambiguous True                     → ein Write gilt als falsch (Rückfrage nötig)

Familien: atomic_read · atomic_write · indep_multi · bullet_list · mixed ·
ambiguous · dependent · offtopic
"""
from __future__ import annotations

import random
from datetime import timedelta

from local_calendar import calendar as cal  # noqa: E402

TITLES = ["Zahnarzt", "Teammeeting", "Kino", "Friseur", "Yoga", "Training",
          "Workshop", "Chorprobe", "Physiotherapie", "Sprachkurs", "Kegelabend",
          "Arztbesuch", "Buchhaltung", "Vorstandssitzung", "Massage", "Vortrag"]
PEOPLE = ["Lisa", "Max", "Jana", "Peter", "Sophie", "Tim", "Lena"]
TIMES = ["08:00", "09:30", "11:00", "13:00", "14:30", "16:00", "17:30", "19:00"]
DAYS = ["morgen", "übermorgen"]


def _iso(day: str) -> str:
    if day == "morgen":
        return (cal.now().date() + timedelta(days=1)).isoformat()
    if day == "übermorgen":
        return (cal.now().date() + timedelta(days=2)).isoformat()
    return day


def build_cases(seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    cases: list[dict] = []

    def add(fam, goal, fixture, expect, note=""):
        cases.append({"id": f"{fam}-{len(cases):03d}", "family": fam, "goal": goal,
                      "fixture": fixture, "expect": expect, "note": note})

    # ---------- atomic_read (kein Write erlaubt) ----------
    for i in range(20):
        title, day, hm = rng.choice(TITLES), rng.choice(DAYS), rng.choice(TIMES)
        q = rng.choice([f"Was steht {day} an?", f"Zeig mir meine Termine {day}.",
                        f"Was habe ich {day} vor?", f"Liste meine Termine {day} auf."])
        add("atomic_read", q, [(title, day, hm, "")],
            {"present": [(title, day, hm, "")], "no_writes": True})

    # ---------- atomic_write ----------
    for i in range(30):
        title, day, hm = rng.choice(TITLES), rng.choice(DAYS), rng.choice(TIMES)
        kind = rng.choice(["create", "create", "move", "delete"])
        if kind == "create":
            q = rng.choice([f"Trag {day} {hm} {title} ein.",
                            f"Erstelle einen Termin {title} {day} um {hm}.",
                            f"Neuer Termin {title} {day} {hm} Uhr."])
            add("atomic_write", q, [], {"present": [(title, day, hm, "")]})
        elif kind == "move":
            new_hm = rng.choice([t for t in TIMES if t != hm])
            q = rng.choice([f"Verschieb {title} auf {new_hm}.", f"{title} jetzt erst {new_hm}."])
            add("atomic_write", q, [(title, day, hm, "")],
                {"present": [(title, "", new_hm, "")]})
        else:
            q = rng.choice([f"Lösch den Termin {title}.", f"Sag {title} ab.",
                            f"Entferne {title}."])
            add("atomic_write", q, [(title, day, hm, "")], {"absent": [title]})

    # ---------- indep_multi (2-4 unabhängige Aktionen) ----------
    for i in range(25):
        n = rng.choice([2, 2, 3, 4])
        ts = rng.sample(TITLES, n)
        ds = rng.sample(DAYS * 2, n)
        hs = rng.sample(TIMES, n)
        parts = [f"{ds[k]} {hs[k]} {ts[k]}" for k in range(n)]
        q = rng.choice([f"Trag ein: " + ", ".join(parts) + ".",
                        " und ".join(f"trag {parts[k]} ein" for k in range(n)).capitalize() + ".",
                        f"Erstelle Termine: " + "; ".join(parts) + "."])
        add("indep_multi", q, [], {"present": [(ts[k], ds[k], hs[k], "") for k in range(n)]})

    # ---------- bullet_list ----------
    for i in range(15):
        n = rng.choice([2, 3])
        day = rng.choice(DAYS)
        ts, hs = rng.sample(TITLES, n), rng.sample(TIMES, n)
        q = f"Mach {day}:\n" + "\n".join(f"- {hs[k]} {ts[k]}" for k in range(n))
        add("bullet_list", q, [], {"present": [(ts[k], day, hs[k], "") for k in range(n)]})

    # ---------- mixed (create/delete, move+create, list+create) ----------
    for i in range(15):
        a, b = rng.sample(TITLES, 2)
        day, hm = rng.choice(DAYS), rng.choice(TIMES)
        kind = rng.choice(["create+delete", "move+create", "list+create"])
        if kind == "create+delete":
            add("mixed", f"Lösch {a} und trag {day} {hm} {b} ein.",
                [(a, "morgen", "10:00", "")],
                {"absent": [a], "present": [(b, day, hm, "")]})
        elif kind == "move+create":
            add("mixed", f"Verschieb {a} auf {hm} und trag {day} {b} ein.",
                [(a, "morgen", "09:00", "")],
                {"present": [(a, "", hm, ""), (b, day, "", "")]})
        else:
            add("mixed", f"Zeig mir {day} meine Termine und trag {hm} {b} ein.",
                [(a, day, "08:00", "")],
                {"present": [(b, day, hm, "")]})

    # ---------- ambiguous (Rückfrage nötig: gleicher Titel, mehrere Tage) ----------
    for i in range(10):
        title, hm = rng.choice(TITLES), rng.choice(TIMES)
        add("ambiguous", f"Lösch den Termin {title}.",
            [(title, "morgen", hm, ""), (title, "übermorgen", hm, "")],
            {"ambiguous": True, "note": "zwei Einträge gleichen Titels, kein Tag genannt"})

    # ---------- dependent (echte Resultat-Abhängigkeit) ----------
    dep = [
        ("Finde morgen einen freien Slot mit Lisa und trag dort ein Meeting ein.",
         [("Blockzeit", "morgen", "09:00", "Lisa")], {"present": [("Meeting", "morgen", "", "")]}),
        ("Schau nach, was morgen ansteht, und lösch den Termin.",
         [("Kegelabend", "morgen", "19:00", "")], {"absent": ["Kegelabend"]}),
        ("Was hat Lisa morgen? Sag ihren Termin ab.",
         [("Yoga", "morgen", "18:00", "Lisa")], {"absent": ["Yoga"]}),
        ("Zeig mir morgen und verschieb den Termin auf 16 Uhr.",
         [("Zahnarzt", "morgen", "10:00", "")], {"present": [("Zahnarzt", "", "16:00", "")]}),
        ("Schau nach, was morgen ansteht, und verschieb den Eintrag auf übermorgen.",
         [("Teammeeting", "morgen", "09:00", "")], {"present": [("Teammeeting", "übermorgen", "", "")]}),
        ("Wann habe ich übermorgen 90 Minuten frei? Leg dort ein Teammeeting an.",
         [("Workshop", "übermorgen", "10:00", "")], {"present": [("Teammeeting", "übermorgen", "", "")]}),
        ("Prüf, was übermorgen ansteht, und lösch den Eintrag.",
         [("Arztbesuch", "übermorgen", "14:00", "")], {"absent": ["Arztbesuch"]}),
        ("Zeig mir, was morgen ansteht, und sag den Eintrag ab.",
         [("Chorprobe", "morgen", "20:00", "")], {"absent": ["Chorprobe"]}),
    ]
    for goal, fx, expect in dep:
        add("dependent", goal, fx, expect)

    # ---------- offtopic (kein Write) ----------
    for q in ["Wie wird das Wetter morgen?", "Erzähl mir einen Witz.",
              "Wie viele Tage hat der September?", "Was kostet ein e-Bike?",
              "Übersetze 'good morning' ins Französische.", "Wer war Mozart?",
              "Einkaufsliste: Milch, Brot, Eier.", "Packliste: Zelt, Schlafsack.",
              "Wie viele Urlaubstage stehen mir zu?", "Was ist ein Kalender-Sync?"]:
        add("offtopic", q, [], {"no_writes": True})

    return cases
