#!/usr/bin/env python3
"""Deterministic, seedable dataset builder for calendar-only Needle fine-tuning.

Design:
  - Templates are slot-based format strings; slot values are drawn from
    value pools (names, titles, dates, times, locations).
  - Train and eval use DISJOINT template phrasings; eval additionally
    draws from held-out value pools (unseen names/titles/dates/times).
  - Negatives: off-topic + cross-task queries (rendered from other
    tasks' catalogs) with answers=[].
  - Every positive argument value is a literal substring of the query.
  - `--exclude <train.jsonl>` guarantees zero exact query duplicates
    between train and eval.

Usage:
  python build_dataset.py --task calendar_write --count 2000 --seed 42 \
      --out data/train/calendar_write.jsonl
  python build_dataset.py --task calendar_write --count 300 --seed 43 \
      --eval --exclude data/train/calendar_write.jsonl \
      --out data/eval/calendar_write.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
from itertools import combinations
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
SCHEMAS = FT_DIR / "schemas"

TOOL_NAMES = {
    "calendar_write": "calendar_write",
    "calendar_read": "calendar_read",
    "reminder": "reminder_parse",
}

# ---------------------------------------------------------------------------
# Value pools. Train pools are always available; eval additionally uses
# eval-only pools (unseen values).
# ---------------------------------------------------------------------------

NAMES_TRAIN = ["Lisa", "Jana", "Julia", "Max", "Peter", "Sophie",
               "Anna", "Tim", "Lena", "Jonas", "Marie", "Paul",
               "Sara", "Jan"]
NAMES_EVAL_ONLY = ["Miriam", "Tom", "Nora", "Emma", "Felix"]

TITLES_TRAIN = [
    "Zahnarzt", "Teammeeting", "Vorstandssitzung", "Kino",
    "Physiotherapie", "Arzttermin", "Workshop", "Training", "Friseur",
    "Buchhaltung", "Yoga", "Sprachkurs", "Gitarrenunterricht",
    "Fußballtraining", "Mitarbeitergespräch", "Betriebsausflug",
    "Zahnarzttermin", "Arztbesuch", "Betriebsversammlung", "Chorprobe",
    "Kegelabend", "Nachmittagstermin",
]
TITLES_EVAL_ONLY = [
    "Augenarzt", "Elternabend", "Projektbesprechung", "Schwimmkurs",
    "Klavierunterricht", "Steuerberatung", "Nachhilfe", "Haartermin",
]

DATES_TRAIN = [
    "morgen", "übermorgen", "heute Abend", "heute Nachmittag", "morgen früh",
    "nächsten Dienstag", "nächsten Freitag", "am Montag", "am Donnerstag",
    "am 18. September", "am 3. März", "diese Woche", "kommende Woche",
    "nächste Woche", "Dienstag", "Freitag", "morgen Mittag", "morgen Abend",
    "am 5. Dezember", "am 20. Juni", "in zwei Wochen",
    "am 7. April", "am 14. Februar", "am 30. November", "am 9. Oktober",
    "am Wochenende", "am Mittwoch", "am Samstag", "nächsten Montag",
    "nächsten Donnerstag", "heute Morgen", "in einer Woche",
    "Ende der Woche", "Anfang der Woche",
]
DATES_EVAL_ONLY = [
    "morgen Vormittag", "übernächste Woche",
    "am 21. Oktober", "am 12. Mai", "nächsten Mittwoch", "Samstag",
    "nächsten Montag", "in drei Tagen", "Ende des Monats",
    "am 1. November", "Anfang nächster Woche", "kommenden Samstag",
    "in vierzehn Tagen",
]

TIMES_TRAIN = [
    "14 Uhr", "9 Uhr", "10 Uhr", "15 Uhr", "8 Uhr", "17 Uhr", "11 Uhr",
    "13 Uhr", "12 Uhr", "16 Uhr", "18 Uhr", "19 Uhr",
    "halb drei", "halb neun", "halb elf", "halb zwei", "halb sechs",
    "halb sieben", "halb acht", "halb zehn", "halb zwölf", "halb eins",
    "14:30", "9:15", "8:00", "15:30", "10:30", "11:45", "14:15", "16:20",
    "18:30", "19:45", "12:30", "8:15",
    "Viertel vor acht", "Viertel nach fünf", "Viertel nach drei",
    "Viertel vor zehn", "Viertel nach sieben", "Viertel vor drei",
    "Viertel nach elf",
]
TIMES_EVAL_ONLY = [
    "20:15", "13:45", "kurz nach zehn", "Viertel vor zwölf", "16:50",
    "7:30", "kurz vor halb neun", "12:05", "21 Uhr", "6 Uhr",
    "kurz nach halb zwei", "17:05",
]

LOCATIONS_TRAIN = [
    "im Besprechungsraum", "im Meetingraum 2", "in der Praxis am Markt",
    "im Büro", "im Gemeindehaus", "im Fitnessstudio", "im Kino",
    "im Therapieraum", "im Seminargebäude", "im Erdgeschoss",
    "im Studio", "im Vereinsheim", "in der Bibliothek", "in der Altstadt",
]
LOCATIONS_EVAL_ONLY = [
    "im Hörsaal 3", "im Labor", "in der Schule", "im Rathaus",
    "im Kulturzentrum", "im Nachbarort", "im Stadtteilzentrum",
    "im Konferenzraum 4",
]

# Reminder anchor subjects — event titles used for reminders.
REMINDER_TARGETS_TRAIN = [
    "Zahnarzt", "Arzttermin", "Workshop", "Training", "Teammeeting",
    "Physiotherapie", "Vorstandssitzung", "Friseur", "Yoga", "Sprachkurs",
]
REMINDER_TARGETS_EVAL_ONLY = [
    "Augenarzt", "Schwimmkurs", "Elternabend", "Klavierunterricht",
    "Projektbesprechung", "Nachhilfe",
]

RELATIVE_OFFSETS = [
    "drei Stunden", "20 Minuten", "eine Stunde", "eine halbe Stunde",
    "45 Minuten", "zwei Stunden", "90 Minuten", "eine Viertelstunde",
    "eine Dreiviertelstunde", "zehn Minuten", "fünf Minuten",
    "30 Minuten",
]

# Off-topic pool for negatives (task-agnostic). Split in half:
# first half for train datasets, second half for eval datasets.
OFF_TOPIC = [
    # Chitchat / greetings
    "Hallo", "Hi", "Hey", "Guten Morgen", "Guten Tag", "Guten Abend",
    "Danke", "Bitte", "Tschüss", "Bis später",
    "Wie geht es dir?", "Wie läuft's?", "Alles klar bei dir?",
    # Knowledge questions
    "Was ist PostgreSQL?", "Was ist ein Vector-Embedding?",
    "Wer ist Bundeskanzler?", "Was ist die Hauptstadt von Frankreich?",
    "Wie tief ist der Marianengraben?", "Wer schrieb Faust?",
    "Was bedeutet Transformer?", "Erklär mir Quantencomputer.",
    "Was ist Entropie?", "Wer hat das Internet erfunden?",
    "Wann fiel die Berliner Mauer?", "Wie viele Bundesländer hat Deutschland?",
    "Was ist die Lichtgeschwindigkeit?", "Wer malte die Mona Lisa?",
    # Code requests
    "Schreib mir Python-Code.", "Gib mir ein SQL-Beispiel.",
    "Erstell eine Funktion, die eine Liste sortiert.",
    "Wie loope ich in Bash?", "Schreib mir eine Unit-Test-Vorlage.",
    # Other domains (weather, food, media, smart home)
    "Wie ist das Wetter in Berlin?", "Sag mir ein Rezept für Pfannkuchen.",
    "Erzähl mir einen Witz.", "Übersetze 'Guten Tag' ins Englische.",
    "Spiel mir ein Lied.", "Mach das Licht im Wohnzimmer an.",
    "Wie wird das Wetter am Wochenende?", "Wie spät ist es?",
    "Bestell mir eine Pizza.", "Rechne 234 mal 12.",
    "Wo ist der nächste Supermarkt?", "Ich mag Züge.",
]

TASK_NEAR_NEGATIVES = {
    "calendar_write": [
        "Was steht morgen an?",
        "Wann ist Zahnarzt?",
        "Wann hat Lisa Zeit?",
        "Wann sind Lisa und Max frei?",
        "Was habe ich diese Woche?",
    ],
    "calendar_read": [
        "Verschieb Zahnarzt auf 15 Uhr.",
        "Sag den Teammeeting-Termin ab.",
        "Trag Zahnarzt für morgen ein.",
        "Erinnere mich morgen um 8 Uhr.",
        "Lösch das Kino-Event.",
    ],
    "reminder": [
        "Trag Zahnarzt für morgen ein.",
        "Verschieb Zahnarzt auf 15 Uhr.",
        "Was steht morgen an?",
        "Wann ist Zahnarzt?",
    ],
}

CROSS_TASK_SOURCES = {
    "calendar_write": ["calendar_read", "reminder"],
    "calendar_read": ["calendar_write", "reminder"],
    "reminder": ["calendar_write", "calendar_read"],
}

# ---------------------------------------------------------------------------
# Template catalogs.
# Entry = (query_fmt, reasoning_fmt, arg_map, fixed_args)
#   arg_map: {argument_name: slot_name}
#   fixed_args: {argument_name: literal_value}
# A slot is any {name} in query_fmt; slots are sampled from pools below.
#
# TRAIN and EVAL catalogs use DISJOINT phrasings so eval measures
# generalisation across phrasings, not memorisation.
# ---------------------------------------------------------------------------

CAL_WRITE_TRAIN = {
    "create_imperative": [
        ("Trag {title} {date} um {time} ein.",
         "'Trag' gives the action span; '{title}' is the title; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "date": "date", "time": "time"},
         {"action_span": "Trag"}),
        ("Plane {title} {date} um {time}.",
         "'Plane' gives the action span; '{title}' is the title; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "date": "date", "time": "time"},
         {"action_span": "Plane"}),
        ("Erstelle {title} {date} um {time}.",
         "'Erstelle' gives the action span; '{title}' is the title; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "date": "date", "time": "time"},
         {"action_span": "Erstelle"}),
    ],
    "create_declarative": [
        ("{title} {date} um {time}.",
         "'{title}' is the title; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "date": "date", "time": "time"}, {}),
        ("Ich habe {date} um {time} {title}.",
         "'{date}' is the date; '{time}' is the time; '{title}' is the title.",
         {"title": "title", "date": "date", "time": "time"}, {}),
    ],
    "create_owner": [
        ("Plane für {person} {date} um {time} {title}.",
         "'Plane' gives the action span; '{person}' is the person; '{title}' is the title; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "date": "date", "time": "time", "person": "person"},
         {"action_span": "Plane"}),
        ("{title} für {person} {date} um {time}.",
         "'{title}' is the title; '{person}' is the person; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "date": "date", "time": "time", "person": "person"}, {}),
    ],
    "create_participants": [
        ("{title} mit {participants} {date} um {time}.",
         "'{title}' is the title; '{participants}' are the participants; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "participants": "participants", "date": "date", "time": "time"}, {}),
        ("Trag {title} mit {participants} {date} um {time} ein.",
         "'Trag' gives the action span; '{title}' is the title; '{participants}' are the participants; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "participants": "participants", "date": "date", "time": "time"},
         {"action_span": "Trag"}),
    ],
    "create_location": [
        ("{title} {date} um {time} {location}.",
         "'{title}' is the title; '{date}' is the date; '{time}' is the time; '{location}' is the location.",
         {"title": "title", "date": "date", "time": "time", "location": "location"}, {}),
    ],
    "create_endtime": [
        ("{title} {date} von {time} bis {end_time}.",
         "'{title}' is the title; '{date}' is the date; '{time}' is the time; '{end_time}' is the end time.",
         {"title": "title", "date": "date", "time": "time", "end_time": "end_time"}, {}),
    ],
    "edit_move": [
        ("Verschieb {title} auf {time}.",
         "'Verschieb' gives the action span; '{title}' is the title; '{time}' is the new time.",
         {"title": "title", "time": "time"}, {"action_span": "Verschieb"}),
        ("Verschiebe {title} auf {date}.",
         "'Verschiebe' gives the action span; '{title}' is the title; '{date}' is the new date.",
         {"title": "title", "date": "date"}, {"action_span": "Verschiebe"}),
        ("Verschiebe {title} auf {date} um {time}.",
         "'Verschiebe' gives the action span; '{title}' is the title; '{date}' is the new date; '{time}' is the new time.",
         {"title": "title", "date": "date", "time": "time"}, {"action_span": "Verschiebe"}),
    ],
    "edit_time_change": [
        ("Zieh {title} auf {time} vor.",
         "'Zieh' gives the action span; '{title}' is the title; '{time}' is the new time.",
         {"title": "title", "time": "time"}, {"action_span": "Zieh"}),
        ("Änder die Zeit von {title} auf {time}.",
         "'Änder' gives the action span; '{title}' is the title; '{time}' is the new time.",
         {"title": "title", "time": "time"}, {"action_span": "Änder"}),
    ],
    "cancel": [
        ("Sag {title} ab.",
         "'Sag' gives the action span; '{title}' is the title.",
         {"title": "title"}, {"action_span": "Sag"}),
        ("Lösch {title}.",
         "'Lösch' gives the action span; '{title}' is the title.",
         {"title": "title"}, {"action_span": "Lösch"}),
        ("Streich {title}.",
         "'Streich' gives the action span; '{title}' is the title.",
         {"title": "title"}, {"action_span": "Streich"}),
        ("Lösch den Termin {title}.",
         "'Lösch' gives the action span; '{title}' is the title.",
         {"title": "title"}, {"action_span": "Lösch"}),
        ("Sag den Termin {title} ab.",
         "'Sag' gives the action span; '{title}' is the title.",
         {"title": "title"}, {"action_span": "Sag"}),
        ("Lösch {title} {date}.",
         "'Lösch' gives the action span; '{title}' is the title; '{date}' is the date.",
         {"title": "title", "date": "date"}, {"action_span": "Lösch"}),
    ],
    "participants_change": [
        ("Nimm {person} mit zu {title}.",
         "'Nimm' gives the action span; '{person}' is the person; '{title}' is the title.",
         {"person": "person", "title": "title"}, {"action_span": "Nimm"}),
        ("Nimm {person} zusätzlich mit zu {title}.",
         "'Nimm' gives the action span; '{person}' is the person; '{title}' is the title.",
         {"person": "person", "title": "title"}, {"action_span": "Nimm"}),
    ],
    "modifier_move": [
        ("Verschieb {title} doch auf {time}.",
         "'Verschieb' gives the action span; '{title}' is the title; '{time}' is the new time; 'doch' is the modifier.",
         {"title": "title", "time": "time"}, {"action_span": "Verschieb", "modifier": "doch"}),
        ("{title} doch erst {time}.",
         "'{title}' is the title; '{time}' is the new time; 'doch' is the modifier.",
         {"title": "title", "time": "time"}, {"modifier": "doch"}),
    ],
}

CAL_WRITE_EVAL = {
    "create_imperative": [
        ("Setz {title} {date} um {time} an.",
         "'Setz' gives the action span; '{title}' is the title; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "date": "date", "time": "time"},
         {"action_span": "Setz"}),
        ("Trag bitte {title} {date} um {time} ein.",
         "'Trag' gives the action span; '{title}' is the title; '{date}' is the date; '{time}' is the time.",
         {"title": "title", "date": "date", "time": "time"},
         {"action_span": "Trag"}),
    ],
    "create_declarative": [
        ("{date} um {time} {title}.",
         "'{date}' is the date; '{time}' is the time; '{title}' is the title.",
         {"title": "title", "date": "date", "time": "time"}, {}),
    ],
    "create_owner": [
        ("Für {person} {date} um {time} {title}.",
         "'{person}' is the person; '{date}' is the date; '{time}' is the time; '{title}' is the title.",
         {"title": "title", "date": "date", "time": "time", "person": "person"}, {}),
    ],
    "create_participants": [
        ("Meeting mit {participants} {date} um {time}.",
         "'Meeting' is the title; '{participants}' are the participants; '{date}' is the date; '{time}' is the time.",
         {"participants": "participants", "date": "date", "time": "time"},
         {"title": "Meeting"}),
    ],
    "create_location": [
        ("Trag {title} {date} um {time} {location} ein.",
         "'Trag' gives the action span; '{title}' is the title; '{date}' is the date; '{time}' is the time; '{location}' is the location.",
         {"title": "title", "date": "date", "time": "time", "location": "location"},
         {"action_span": "Trag"}),
    ],
    "create_endtime": [
        ("Plane {title} {date} von {time} bis {end_time}.",
         "'Plane' gives the action span; '{title}' is the title; '{date}' is the date; '{time}' is the time; '{end_time}' is the end time.",
         {"title": "title", "date": "date", "time": "time", "end_time": "end_time"},
         {"action_span": "Plane"}),
    ],
    "edit_move": [
        ("{title} ist jetzt erst {time}.",
         "'{title}' is the title; '{time}' is the new time; 'jetzt' is the modifier.",
         {"title": "title", "time": "time"}, {"modifier": "jetzt"}),
        ("Verschieb den Termin {title} auf {date}.",
         "'Verschieb' gives the action span; '{title}' is the title; '{date}' is the new date.",
         {"title": "title", "date": "date"}, {"action_span": "Verschieb"}),
    ],
    "edit_time_change": [
        ("Verschieb die Zeit von {title} auf {time}.",
         "'Verschieb' gives the action span; '{title}' is the title; '{time}' is the new time.",
         {"title": "title", "time": "time"}, {"action_span": "Verschieb"}),
    ],
    "cancel": [
        ("Lösche {title}.",
         "'Lösche' gives the action span; '{title}' is the title.",
         {"title": "title"}, {"action_span": "Lösche"}),
        ("Der Termin {title} fällt aus.",
         "'fällt aus' signals cancellation; '{title}' is the title.",
         {"title": "title"}, {}),
    ],
    "participants_change": [
        ("Nimm noch {person} mit zu {title}.",
         "'Nimm' gives the action span; '{person}' is the person; '{title}' is the title.",
         {"person": "person", "title": "title"}, {"action_span": "Nimm"}),
    ],
    "modifier_move": [
        ("Verschieb {title} halt auf {time}.",
         "'Verschieb' gives the action span; '{title}' is the title; '{time}' is the new time; 'halt' is the modifier.",
         {"title": "title", "time": "time"}, {"action_span": "Verschieb", "modifier": "halt"}),
    ],
}

CAL_READ_TRAIN = {
    "list_when": [
        ("Was steht {when} an?",
         "'Was steht' opens the question; '{when}' is the time scope.",
         {"when": "when"}, {"query_span": "Was steht"}),
        ("Welche Termine habe ich {when}?",
         "'Welche Termine' opens the question; '{when}' is the time scope.",
         {"when": "when"}, {"query_span": "Welche Termine"}),
        ("Was habe ich {when}?",
         "'{when}' is the time scope.",
         {"when": "when"}, {"query_span": "Was habe ich"}),
        ("Was steht {when} Vormittag an?",
         "'Was steht' opens the question; '{when}' is the time scope.",
         {"when": "when"}, {"query_span": "Was steht"}),
        ("Zeig mir meine Termine {when}.",
         "'Zeig' opens the request; '{when}' is the time scope.",
         {"when": "when"}, {"query_span": "Zeig mir"}),
    ],
    "find_target": [
        ("Wann ist {target}?",
         "'{target}' is the target.",
         {"target": "target"}, {"query_span": "Wann ist"}),
        ("Wann habe ich {target}?",
         "'{target}' is the target.",
         {"target": "target"}, {"query_span": "Wann habe ich"}),
        ("Wann findet {target} statt?",
         "'{target}' is the target.",
         {"target": "target"}, {"query_span": "Wann findet"}),
        ("Wann ist der Termin {target}?",
         "'{target}' is the target.",
         {"target": "target"}, {"query_span": "Wann ist"}),
    ],
    "person_list": [
        ("Wann hat {person} Termine?",
         "'{person}' is the person.",
         {"person": "person"}, {"query_span": "Wann hat"}),
        ("Zeig mir die Termine von {person}.",
         "'{person}' is the person.",
         {"person": "person"}, {"query_span": "Zeig mir"}),
        ("Was hat {person} {when}?",
         "'{person}' is the person; '{when}' is the time scope.",
         {"person": "person", "when": "when"}, {"query_span": "Was hat"}),
        ("Wann hat {person} {when} Termine?",
         "'{person}' is the person; '{when}' is the time scope.",
         {"person": "person", "when": "when"}, {"query_span": "Wann hat"}),
        ("Termine von {person} ausgeben.",
         "'{person}' is the person.",
         {"person": "person"}, {"query_span": "Termine von"}),
    ],
    "persons_free": [
        ("Wann sind {persons} frei?",
         "'{persons}' are the persons.",
         {"persons": "persons"}, {"query_span": "Wann sind"}),
        ("Wann haben {persons} gemeinsam Zeit?",
         "'{persons}' are the persons.",
         {"persons": "persons"}, {"query_span": "Wann haben"}),
        ("Wann haben {persons} beide Zeit?",
         "'{persons}' are the persons.",
         {"persons": "persons"}, {"query_span": "Wann haben"}),
        ("Zeig gemeinsame freie Zeiten von {persons}.",
         "'{persons}' are the persons.",
         {"persons": "persons"}, {"query_span": "Zeig gemeinsame"}),
    ],
}

CAL_READ_EVAL = {
    "list_when": [
        ("Zeig mir, was {when} ansteht.",
         "'{when}' is the time scope.",
         {"when": "when"}, {"query_span": "Zeig mir"}),
        ("Was ist {when} geplant?",
         "'{when}' is the time scope.",
         {"when": "when"}, {"query_span": "Was ist"}),
        ("Welche Termine sind {when}?",
         "'{when}' is the time scope.",
         {"when": "when"}, {"query_span": "Welche Termine"}),
    ],
    "find_target": [
        ("Wann läuft {target}?",
         "'{target}' is the target.",
         {"target": "target"}, {"query_span": "Wann läuft"}),
        ("Wann ist wieder {target}?",
         "'{target}' is the target.",
         {"target": "target"}, {"query_span": "Wann ist"}),
    ],
    "person_list": [
        ("Kalender von {person} anzeigen.",
         "'{person}' is the person.",
         {"person": "person"}, {"query_span": "Kalender von"}),
        ("Was hat {person} {when} vor?",
         "'{person}' is the person; '{when}' is the time scope.",
         {"person": "person", "when": "when"}, {"query_span": "Was hat"}),
    ],
    "persons_free": [
        ("Wann können {persons} gleichzeitig?",
         "'{persons}' are the persons.",
         {"persons": "persons"}, {"query_span": "Wann können"}),
        ("Finde einen Termin, wo {persons} beide Zeit haben.",
         "'{persons}' are the persons.",
         {"persons": "persons"}, {"query_span": "Finde"}),
    ],
}

REMINDER_TRAIN = {
    "absolute_when": [
        ("Erinnere mich {when} an {target}.",
         "'{when}' is the time; '{target}' is the target.",
         {"when": "when", "target": "target"}, {}),
        ("Erinner mich {when} an {target}.",
         "'{when}' is the time; '{target}' is the target.",
         {"when": "when", "target": "target"}, {}),
    ],
    "relative_event": [
        ("Erinnere mich {relative} vor dem {target}.",
         "'{relative}' is the relative offset; '{target}' is the target.",
         {"relative": "relative", "target": "target"}, {}),
        ("Erinner mich {relative} vor dem {target}.",
         "'{relative}' is the relative offset; '{target}' is the target.",
         {"relative": "relative", "target": "target"}, {}),
    ],
    "relative_only": [
        ("Erinnere mich {relative} vorher.",
         "'{relative}' is the relative offset.",
         {"relative": "relative"}, {}),
        ("Erinner mich {relative} vorher.",
         "'{relative}' is the relative offset.",
         {"relative": "relative"}, {}),
        ("Erinnere mich {relative} davor.",
         "'{relative}' is the relative offset.",
         {"relative": "relative"}, {}),
    ],
    "with_person": [
        ("Erinnere {person} {when} an {target}.",
         "'{person}' is the person; '{when}' is the time; '{target}' is the target.",
         {"person": "person", "when": "when", "target": "target"}, {}),
        ("Erinner {person} {relative} vorher an {target}.",
         "'{person}' is the person; '{relative}' is the relative offset; '{target}' is the target.",
         {"person": "person", "relative": "relative", "target": "target"}, {}),
    ],
}

REMINDER_EVAL = {
    "absolute_when": [
        ("Stell mir {when} eine Erinnerung für {target}.",
         "'{when}' is the time; '{target}' is the target.",
         {"when": "when", "target": "target"}, {}),
        ("Erinner mich {when} daran, {target} zu machen.",
         "'{when}' is the time; '{target}' is the target.",
         {"when": "when", "target": "target"}, {}),
    ],
    "relative_event": [
        ("Erinner mich {relative} vorher an {target}.",
         "'{relative}' is the relative offset; '{target}' is the target.",
         {"relative": "relative", "target": "target"}, {}),
        ("Erinnere mich {relative} vor {target}.",
         "'{relative}' is the relative offset; '{target}' is the target.",
         {"relative": "relative", "target": "target"}, {}),
    ],
    "relative_only": [
        ("Erinner mich {relative} vorher.",
         "'{relative}' is the relative offset.",
         {"relative": "relative"}, {}),
        ("Erinner mich {relative} davor.",
         "'{relative}' is the relative offset.",
         {"relative": "relative"}, {}),
    ],
    "with_person": [
        ("Erinnere {person} {when} an {target}.",
         "'{person}' is the person; '{when}' is the time; '{target}' is the target.",
         {"person": "person", "when": "when", "target": "target"}, {}),
        ("Erinner {person} {relative} vorher an {target}.",
         "'{person}' is the person; '{relative}' is the relative offset; '{target}' is the target.",
         {"person": "person", "relative": "relative", "target": "target"}, {}),
    ],
}

CATALOGS = {
    "calendar_write": (CAL_WRITE_TRAIN, CAL_WRITE_EVAL),
    "calendar_read": (CAL_READ_TRAIN, CAL_READ_EVAL),
    "reminder": (REMINDER_TRAIN, REMINDER_EVAL),
}

# ---------------------------------------------------------------------------
# Pool construction per task/split.
# ---------------------------------------------------------------------------


def build_pools(task: str, split: str) -> dict:
    is_eval = split == "eval"
    names = NAMES_TRAIN + (NAMES_EVAL_ONLY if is_eval else [])
    titles = TITLES_TRAIN + (TITLES_EVAL_ONLY if is_eval else [])
    dates = DATES_TRAIN + (DATES_EVAL_ONLY if is_eval else [])
    times = TIMES_TRAIN + (TIMES_EVAL_ONLY if is_eval else [])
    locations = LOCATIONS_TRAIN + (LOCATIONS_EVAL_ONLY if is_eval else [])
    rt = REMINDER_TARGETS_TRAIN + (REMINDER_TARGETS_EVAL_ONLY if is_eval else [])
    participants = [f"{a} und {b}" for a, b in combinations(names, 2)]
    persons = [f"{a} und {b}" for a, b in combinations(names, 2)]

    pools = {
        "title": titles,
        "date": dates,
        "time": times,
        "end_time": times,
        "person": names,
        "location": locations,
        "participants": participants,
        "persons": persons,
        "when": dates,
        "target": titles,
        "relative": RELATIVE_OFFSETS,
        "reminder_target": rt,
    }
    # reminder needs 'when' as a compound "date um time" phrase
    if task == "reminder":
        pools["when"] = [f"{d} um {t}" for d in dates for t in times]
        pools["target"] = rt
    return pools


def _slots_of(fmt: str) -> list[str]:
    out, i = [], 0
    while i < len(fmt):
        if fmt[i] == "{":
            j = fmt.index("}", i)
            out.append(fmt[i + 1:j])
            i = j + 1
        else:
            i += 1
    return out


def _sample(entry, pools, rng):
    """Sample a concrete value for each slot in the template."""
    query_fmt, reasoning_fmt, arg_map, fixed_args = entry
    values = {}
    for slot in _slots_of(query_fmt):
        pool = pools.get(slot)
        values[slot] = rng.choice(pool) if pool else slot
    return query_fmt, reasoning_fmt, values


def _remap_end_time(query_fmt, values, rng, pools):
    """Ensure end_time is later in the day than time (semantic sanity)."""
    if "{end_time}" in query_fmt and "time" in values:
        start_hour = _hour_of(values.get("time", "8 Uhr"))
        later = [t for t in pools["end_time"] if _hour_of(t) > start_hour]
        values["end_time"] = rng.choice(later) if later else rng.choice(pools["end_time"])
    return values


def _hour_of(time_str: str) -> int:
    """Extract the hour from a time phrase like '14 Uhr' or 'halb drei'."""
    s = time_str.lower().strip()
    if "halb" in s:
        words = {"eins": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4,
                 "fünf": 5, "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8,
                 "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "zwoelf": 12}
        for w, h in words.items():
            if w in s:
                return h - 1  # "halb drei" = 2:30
        return 2
    if ":" in s:
        try:
            return int(s.split(":")[0])
        except ValueError:
            return 8
    import re
    m = re.search(r"(\d{1,2})", s)
    return int(m.group(1)) if m else 8


def _render(entry, values):
    """Render query and reasoning from the template and sampled values."""
    query_fmt, reasoning_fmt, arg_map, fixed_args = entry
    return query_fmt.format(**values), reasoning_fmt.format(**values)


def _build_arguments(entry, values):
    """Build the arguments dict from the template's arg_map and fixed_args."""
    query_fmt, reasoning_fmt, arg_map, fixed_args = entry
    arguments = {}
    for arg_name, slot_name in arg_map.items():
        if slot_name in values:
            arguments[arg_name] = values[slot_name]
    for arg_name, literal in fixed_args.items():
        arguments[arg_name] = literal
    return arguments


def _render_query(entry, pools, rng):
    """Render ONLY the query string of a template (for negatives)."""
    query_fmt, _, values = _sample(entry, pools, rng)
    values = _remap_end_time(query_fmt, values, rng, pools)
    return query_fmt.format(**values)


def generate_negatives(task: str, split: str, count: int, rng: random.Random,
                       seen_queries: set) -> list[dict]:
    """Generate negatives: cross-task + off-topic + static task-near pool.

    Train and eval draw from DISJOINT negative pools so that
    train/eval exact query duplicates == 0:
      - train: static task-near pool + first half of OFF_TOPIC
      - eval:  cross-task rendered queries + second half of OFF_TOPIC
    """
    neg_examples, neg_seen = [], set()

    def add_neg(query, reasoning):
        q = query.strip()
        key = q.lower()
        if not q or key in neg_seen or key in seen_queries:
            return False
        neg_seen.add(key)
        seen_queries.add(key)
        neg_examples.append({"query": q, "answers": [], "reasoning": reasoning})
        return True

    # 1) Static task-near pool (only for train split).
    if split == "train":
        for q in TASK_NEAR_NEGATIVES[task]:
            if len(neg_examples) >= count:
                break
            add_neg(q, "query belongs to a different task")

    # 2) Cross-task negatives rendered from other tasks' catalogs.
    cross_sources = CROSS_TASK_SOURCES[task]
    cross_quota = max(0, int(count * 0.55) - len(neg_examples))
    cross_made = 0
    attempts = 0
    while cross_made < cross_quota and attempts < cross_quota * 40 + 100:
        attempts += 1
        other_task = rng.choice(cross_sources)
        other_catalog = CATALOGS[other_task][0 if split == "train" else 1]
        family = rng.choice(list(other_catalog.keys()))
        entry = rng.choice(other_catalog[family])
        other_pools = build_pools(other_task, split)
        query = _render_query(entry, other_pools, rng)
        if add_neg(query, "query belongs to a different task"):
            cross_made += 1

    # 3) Off-topic negatives — split-disjoint halves of OFF_TOPIC.
    half = len(OFF_TOPIC) // 2
    off_pool = list(OFF_TOPIC[:half]) if split == "train" else list(OFF_TOPIC[half:])
    rng.shuffle(off_pool)
    idx = 0
    while len(neg_examples) < count and idx < len(off_pool):
        q = off_pool[idx]
        idx += 1
        add_neg(q, "off-topic, no calendar intent")

    # 4) If still short, accept fewer negatives.
    return neg_examples[:count]


def generate_task_dataset(task: str, split: str, count: int, seed: int,
                          negative_fraction: float = 0.12,
                          exclude_queries: set | None = None) -> list[dict]:
    """Generate a dataset for a given task and split."""
    rng = random.Random(seed)
    tool_file = {"calendar_write": "calendar_write.json",
                 "calendar_read": "calendar_read.json",
                 "reminder": "reminder.json"}[task]
    tool_schema = json.loads((SCHEMAS / tool_file).read_text())
    catalog = CATALOGS[task][0 if split == "train" else 1]
    pools = build_pools(task, split)

    neg_count = int(count * negative_fraction)
    pos_count = count - neg_count

    # Distribute positives across families proportionally to family template count.
    family_weights = [(fam, len(entries)) for fam, entries in catalog.items()]
    total_weight = sum(w for _, w in family_weights)

    examples, seen_queries = [], set()
    if exclude_queries:
        seen_queries |= {q.strip().lower() for q in exclude_queries}

    def add_example(ex):
        q = ex["query"].strip().lower()
        if q in seen_queries:
            return False
        seen_queries.add(q)
        examples.append(ex)
        return True

    # Generate positives family by family.
    all_family_names = [fam for fam, _ in family_weights]

    def make_positive(entry):
        query_fmt, _, values = _sample(entry, pools, rng)
        values = _remap_end_time(query_fmt, values, rng, pools)
        query = query_fmt.format(**values)
        reasoning = entry[1].format(**values)
        arguments = _build_arguments(entry, values)
        return {
            "query": query,
            "answers": [{"name": TOOL_NAMES[task], "arguments": arguments}],
            "reasoning": reasoning,
        }

    # Exact family quotas: floor first, then distribute the remainder
    # round-robin so the counts sum to pos_count exactly.
    quotas = {}
    allocated = 0
    for family, weight in family_weights:
        c = pos_count * weight // total_weight
        quotas[family] = c
        allocated += c
    i = 0
    while allocated < pos_count:
        fam = family_weights[i % len(family_weights)][0]
        quotas[fam] += 1
        allocated += 1
        i += 1

    for family, family_count in quotas.items():
        entries = catalog[family]
        made, attempts = 0, 0
        while made < family_count and attempts < family_count * 50 + 50:
            attempts += 1
            entry = rng.choice(entries)
            ex = make_positive(entry)
            if add_example(ex):
                made += 1

    # Fill any shortfall from random families until pos_count is reached
    # (or pools are exhausted).
    fill_attempts = 0
    while len(examples) < pos_count and fill_attempts < pos_count * 60:
        fill_attempts += 1
        family = rng.choice(all_family_names)
        entries = catalog[family]
        entry = rng.choice(entries)
        ex = make_positive(entry)
        if add_example(ex):
            pass

    # Negatives: cross-task queries (rendered from other tasks' catalogs)
    # + off-topic queries. Each FT must stay task-specific.
    neg_examples = generate_negatives(task, split, neg_count, rng,
                                      seen_queries)

    all_examples = examples + neg_examples[:neg_count]
    # Deterministic shuffle for mixed ordering.
    rng.shuffle(all_examples)

    for ex in all_examples:
        ex["tools"] = [tool_schema]

    return all_examples


def main():
    ap = argparse.ArgumentParser(description="Calendar-FT dataset builder")
    ap.add_argument("--task", required=True,
                    choices=["calendar_write", "calendar_read", "reminder"])
    ap.add_argument("--count", type=int, required=True,
                    help="total number of examples to generate")
    ap.add_argument("--seed", type=int, default=42,
                    help="random seed for reproducibility")
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--eval", action="store_true",
                    help="generate eval split (held-out values and phrasings)")
    ap.add_argument("--exclude", default=None,
                    help="path to a JSONL dataset whose queries must not "
                         "appear in the output (e.g. the train split)")
    ap.add_argument("--negative-fraction", type=float, default=0.12,
                    help="fraction of negative examples (default 0.12)")

    args = ap.parse_args()
    split = "eval" if args.eval else "train"

    exclude_queries = set()
    if args.exclude:
        with open(args.exclude) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    exclude_queries.add(json.loads(line)["query"])

    examples = generate_task_dataset(
        args.task, split, args.count, args.seed,
        negative_fraction=args.negative_fraction,
        exclude_queries=exclude_queries)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as handle:
        for ex in examples:
            handle.write(json.dumps(ex, ensure_ascii=False) + "\n")

    pos = sum(1 for e in examples if e["answers"])
    neg = len(examples) - pos
    print(f"wrote {len(examples)} examples ({pos} positive, {neg} negative) -> {args.out}")


if __name__ == "__main__":
    main()
