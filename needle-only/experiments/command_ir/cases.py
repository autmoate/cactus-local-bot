"""Frozen command_ir evaluation cases (§8/§9).

~80 cases across add / change / remove / show / availability / independent_multi
/ negative. Gold is authored with ABSOLUTE ISO values (today = 2026-09-24,
Thursday) and is NOT derived from the compiler under test.

Every case carries both gold_surface_A and gold_surface_B so both contracts are
scored on exactly the same user goals.
"""

from __future__ import annotations

TODAY = "2026-09-24"


def _dt(m, d, h=0, mi=0):
    return f"2026-{m:02d}-{d:02d}T{h:02d}:{mi:02d}:00"


def add(id_, query, when, title, start, end, all_day=False, people=None,
        fixture=None, surface_change=None):
    """One calendar_add case."""
    surf = {"name": "calendar_add",
            "arguments": {"title": title, "when": when, "people": people or ""}}
    return {
        "id": id_, "family": "add", "query": query,
        "fixture": fixture or {"kind": "private", "actor": "Ada", "events": []},
        "expected_ops": [{"op": "add", "title": title, "start": start, "end": end,
                          "all_day": all_day}],
        "expected_status": "none",
        "gold_surface_A": [surf], "gold_surface_B": [dict(surf)],
    }


def change(id_, query, fixture_events, target, new_start, new_end, all_day,
           gold_A_change, gold_B_new_when="", gold_B_new_title="",
           expected_status="unique", note=None):
    ops = [{"op": "change", "target": target, "start": new_start, "end": new_end,
            "all_day": all_day}]
    if gold_B_new_title:
        ops[0]["new_title"] = gold_B_new_title
    a = {"name": "calendar_change",
         "arguments": {"target": target, "change": gold_A_change}}
    b = {"name": "calendar_change",
         "arguments": {"target": target, "new_when": gold_B_new_when,
                       "new_title": gold_B_new_title}}
    return {"id": id_, "family": "change", "query": query,
            "fixture": {"kind": "private", "actor": "Ada", "events": fixture_events},
            "expected_ops": ops if expected_status == "unique" else [],
            "expected_status": expected_status,
            "gold_surface_A": [a], "gold_surface_B": [b], "note": note}


def remove(id_, query, fixture_events, target, removed_titles,
           expected_status="unique", note=None, group=False, members=None,
           expected_ops=None):
    ops = expected_ops if expected_ops is not None else \
        [{"op": "remove", "target": t} for t in removed_titles]
    a = {"name": "calendar_remove", "arguments": {"target": target}}
    return {"id": id_, "family": "remove", "query": query,
            "fixture": {"kind": "group" if group else "private", "actor": "Ada",
                        "members": members or [], "events": fixture_events},
            "expected_ops": ops if expected_status == "unique" else [],
            "expected_status": expected_status,
            "gold_surface_A": [a], "gold_surface_B": [dict(a)], "note": note}


def show(id_, query, first, last, gold_A_query, gold_B_when="", gold_B_person="",
         person_ids=None, fixture=None, note=None):
    a = {"name": "calendar_show", "arguments": {"query": gold_A_query}}
    b = {"name": "calendar_show",
         "arguments": {"when": gold_B_when, "person": gold_B_person}}
    return {"id": id_, "family": "show", "query": query,
            "fixture": fixture or {"kind": "private", "actor": "Ada", "events": []},
            "expected_ops": [], "expected_status": "none",
            "expected_window": [first, last], "expected_person_ids": person_ids,
            "gold_surface_A": [a], "gold_surface_B": [b], "note": note}


def availability(id_, query, first, last, duration, person_ids, fixture=None,
                 gold_A_query="", gold_B_people="", gold_B_when="",
                 gold_B_duration="", note=None):
    a = {"name": "calendar_availability", "arguments": {"query": gold_A_query}}
    b = {"name": "calendar_availability",
         "arguments": {"people": gold_B_people, "when": gold_B_when,
                       "duration": gold_B_duration}}
    return {"id": id_, "family": "availability", "query": query,
            "fixture": fixture or {"kind": "private", "actor": "Ada", "events": []},
            "expected_ops": [], "expected_status": "none",
            "expected_window": [first, last], "expected_duration": duration,
            "expected_person_ids": person_ids,
            "gold_surface_A": [a], "gold_surface_B": [b], "note": note}


def multi(id_, query, fixture, ops, gold_a_calls, gold_b_calls, note=None):
    return {"id": id_, "family": "multi", "query": query, "fixture": fixture,
            "expected_ops": ops, "expected_status": "none",
            "gold_surface_A": gold_a_calls, "gold_surface_B": gold_b_calls,
            "note": note}


def negative(id_, query):
    return {"id": id_, "family": "negative", "query": query,
            "fixture": {"kind": "private", "actor": "Ada", "events": []},
            "expected_ops": [], "expected_status": "none",
            "gold_surface_A": [], "gold_surface_B": []}


def _ev(title, start, end, all_day=False, participants=None, busy=True):
    return {"title": title, "start": start, "end": end, "all_day": all_day,
            "participants": participants or [], "busy": busy}


CASES: list[dict] = []

# --------------------------------------------------------------------- ADD (15)
CASES += [
    add("add_zug", "Trag am 28.9. um 7:13 Uhr Zug nach Leipzig ein",
        "am 28.9. um 7:13 Uhr", "Zug nach Leipzig", _dt(9, 28, 7, 13),
        _dt(9, 28, 8, 13)),
    add("add_breakfast", "Trag morgen 8 Uhr Frühstück ein", "morgen 8 Uhr",
        "Frühstück", _dt(9, 25, 8), _dt(9, 25, 9)),
    add("add_dentist", "Zahnarzt am 29.9. um 14 Uhr", "am 29.9. um 14 Uhr",
        "Zahnarzt", _dt(9, 29, 14), _dt(9, 29, 15)),
    add("add_office_range", "Büro am 29.9. von 9 bis 16 Uhr",
        "am 29.9. von 9 bis 16 Uhr", "Büro", _dt(9, 29, 9), _dt(9, 29, 16)),
    add("add_allday_single", "Urlaub am 30.9.", "am 30.9.", "Urlaub",
        _dt(9, 30, 0), _dt(10, 1, 0), all_day=True),
    add("add_multiday", "Urlaub vom 24.9. bis 27.9.", "vom 24.9. bis 27.9.",
        "Urlaub", _dt(9, 24, 0), _dt(9, 28, 0), all_day=True),
    add("add_weekday", "Zahnarzt am Dienstag um 9 Uhr", "am Dienstag um 9 Uhr",
        "Zahnarzt", _dt(9, 29, 9), _dt(9, 29, 10)),
    add("add_period", "Kaffee morgen nachmittag", "morgen nachmittag", "Kaffee",
        _dt(9, 25, 14), _dt(9, 25, 15)),
    add("add_iso", "Termin am 2026-10-05 um 11 Uhr", "am 2026-10-05 um 11 Uhr",
        "Termin", _dt(10, 5, 11), _dt(10, 5, 12)),
    add("add_monthname", "TÜV am 3. Oktober um 9 Uhr", "am 3. Oktober um 9 Uhr",
        "TÜV", _dt(10, 3, 9), _dt(10, 3, 10)),
    add("add_range_time", "Workshop am 30.9. von 9 bis 12 Uhr",
        "am 30.9. von 9 bis 12 Uhr", "Workshop", _dt(9, 30, 9), _dt(9, 30, 12)),
    add("add_allday_messe", "Messe am 29.9.", "am 29.9.", "Messe",
        _dt(9, 29, 0), _dt(9, 30, 0), all_day=True),
    add("add_long_title",
        "Trag am 28.9. um 7:13 Uhr Zug nach Leipzig Hauptbahnhof ein",
        "am 28.9. um 7:13 Uhr", "Zug nach Leipzig Hauptbahnhof",
        _dt(9, 28, 7, 13), _dt(9, 28, 8, 13)),
    add("add_person", "Meeting mit Ben am 30.9. um 10 Uhr",
        "am 30.9. um 10 Uhr", "Meeting", _dt(9, 30, 10), _dt(9, 30, 11),
        people="Ben", fixture={"kind": "group", "actor": "Ada",
                               "members": ["Ben"], "events": []}),
    add("add_evening", "Sport am 1.10. um 18 Uhr", "am 1.10. um 18 Uhr", "Sport",
        _dt(10, 1, 18), _dt(10, 1, 19)),
]

# ------------------------------------------------------------------ CHANGE (12)
CASES += [
    change("change_time", "Verschiebe Zahnarzt auf 16 Uhr",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))], "Zahnarzt",
           _dt(9, 29, 16), _dt(9, 29, 17), False, "16 Uhr", "16 Uhr"),
    change("change_day", "Verschiebe Zahnarzt auf den 30.9.",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))], "Zahnarzt",
           _dt(9, 30, 10), _dt(9, 30, 11), False, "der 30.9.", "der 30.9."),
    change("change_date_time", "Verschiebe Zahnarzt auf 30.9. 14 Uhr",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))], "Zahnarzt",
           _dt(9, 30, 14), _dt(9, 30, 15), False, "30.9. 14 Uhr", "30.9. 14 Uhr"),
    change("change_range_shrink",
           "Verschiebe Vacation von 23.9.–27.9. auf 24.9. bis 27.9.",
           [_ev("Vacation", _dt(9, 23, 0), _dt(9, 28, 0), all_day=True)], "Vacation",
           _dt(9, 24, 0), _dt(9, 28, 0), True, "24.9. bis 27.9.", "24.9. bis 27.9.",
           note="range replace: 23.-27. -> 24.-27."),
    change("change_rename", "Nenn Zahnarzt in Kontrolltermin um",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))], "Zahnarzt",
           _dt(9, 29, 10), _dt(9, 29, 11), False, "Kontrolltermin", "",
           gold_B_new_title="Kontrolltermin"),
    change("change_rename_time",
           "Nenn Zahnarzt in Kontrolltermin um und verschieb auf 16 Uhr",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))], "Zahnarzt",
           _dt(9, 29, 16), _dt(9, 29, 17), False, "Kontrolltermin 16 Uhr", "16 Uhr",
           gold_B_new_title="Kontrolltermin",
           note="Contract A cannot express rename+time in one span"),
    change("change_time_range", "Verschiebe Meeting auf 15 bis 17 Uhr",
           [_ev("Meeting", _dt(9, 30, 10), _dt(9, 30, 12))], "Meeting",
           _dt(9, 30, 15), _dt(9, 30, 17), False, "15 bis 17 Uhr", "15 bis 17 Uhr"),
    change("change_weekday_time",
           "Verschiebe Zahnarzt auf nächste Woche Dienstag um 9 Uhr",
           [_ev("Zahnarzt", _dt(9, 25, 10), _dt(9, 25, 11))], "Zahnarzt",
           _dt(9, 29, 9), _dt(9, 29, 10), False, "nächste Woche Dienstag um 9 Uhr",
           "nächste Woche Dienstag um 9 Uhr"),
    change("change_allday_day", "Verschiebe Urlaub auf den 30.9.",
           [_ev("Urlaub", _dt(9, 24, 0), _dt(9, 25, 0), all_day=True)], "Urlaub",
           _dt(9, 30, 0), _dt(10, 1, 0), True, "der 30.9.", "der 30.9."),
    change("change_ambiguous", "Verschiebe Meeting auf 16 Uhr",
           [_ev("Meeting", _dt(9, 29, 10), _dt(9, 29, 11)),
            _ev("Meeting", _dt(9, 30, 10), _dt(9, 30, 11))], "Meeting",
           _dt(9, 29, 16), _dt(9, 29, 17), False, "16 Uhr", "16 Uhr",
           expected_status="ambiguous"),
    change("change_notfound", "Verschiebe Kino auf 16 Uhr",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))], "Kino",
           _dt(9, 29, 16), _dt(9, 29, 17), False, "16 Uhr", "16 Uhr",
           expected_status="not_found"),
    change("change_duration_keep", "Verschiebe Zahnarzt auf den 1.10.",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11, 30))], "Zahnarzt",
           _dt(10, 1, 10), _dt(10, 1, 11, 30), False, "der 1.10.", "der 1.10."),
]

# ------------------------------------------------------------------ REMOVE (8)
CASES += [
    remove("remove_title", "Lösch Zahnarzt",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))], "Zahnarzt",
           ["Zahnarzt"]),
    remove("remove_title_date", "Lösch Zahnarzt am 30.9.",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11)),
            _ev("Zahnarzt", _dt(9, 30, 10), _dt(9, 30, 11))],
           "Zahnarzt am 30.9.", [], expected_status="unique",
           expected_ops=[{"op": "remove", "target": "Zahnarzt",
                          "match_start": _dt(9, 30, 10)}],
           note="date disambiguates two same-named events"),
    remove("remove_date_time", "Lösch den Termin am 29.9. um 10 Uhr",
           [_ev("Meeting", _dt(9, 29, 10), _dt(9, 29, 11)),
            _ev("Meeting", _dt(9, 29, 15), _dt(9, 29, 16))],
           "den Termin am 29.9. um 10 Uhr", [], expected_status="unique",
           expected_ops=[{"op": "remove", "target": "Meeting",
                          "match_start": _dt(9, 29, 10)}]),
    remove("remove_allday", "Lösch Urlaub",
           [_ev("Urlaub", _dt(9, 24, 0), _dt(9, 28, 0), all_day=True)], "Urlaub",
           ["Urlaub"]),
    remove("remove_allday_date", "Lösch die Abwesenheit am 26.9.",
           [_ev("Urlaub", _dt(9, 24, 0), _dt(9, 28, 0), all_day=True)],
           "die Abwesenheit am 26.9.", ["Urlaub"]),
    remove("remove_ambiguous", "Lösch Meeting",
           [_ev("Meeting", _dt(9, 29, 10), _dt(9, 29, 11)),
            _ev("Meeting", _dt(9, 30, 10), _dt(9, 30, 11))], "Meeting", [],
           expected_status="ambiguous"),
    remove("remove_notfound", "Lösch Kino am 30.9.",
           [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))], "Kino am 30.9.", [],
           expected_status="not_found"),
    remove("remove_time_only", "Lösch das Meeting um 15 Uhr",
           [_ev("Meeting", _dt(9, 29, 10), _dt(9, 29, 11)),
            _ev("Meeting", _dt(9, 29, 15), _dt(9, 29, 16))],
           "das Meeting um 15 Uhr", [], expected_status="unique",
           expected_ops=[{"op": "remove", "target": "Meeting",
                          "match_start": _dt(9, 29, 15)}]),
]

# -------------------------------------------------------------------- SHOW (8)
_G = {"kind": "group", "actor": "Ada", "members": ["Ben", "Cleo"], "events": []}
CASES += [
    show("show_today", "Was habe ich heute?", "2026-09-24", "2026-09-24",
         "heute", "heute"),
    show("show_day", "Zeig mir den 29.9.", "2026-09-29", "2026-09-29", "den 29.9.",
         "den 29.9."),
    show("show_next_week", "Zeig meine Termine nächste Woche",
         "2026-09-28", "2026-10-04", "nächste Woche", "nächste Woche"),
    show("show_this_week", "Was steht diese Woche an?", "2026-09-21", "2026-09-27",
         "diese Woche", "diese Woche"),
    show("show_office", "Wann gehe ich am 29.9. ins Büro?", "2026-09-29",
         "2026-09-29", "am 29.9.", "am 29.9."),
    show("show_iso", "Zeig den 2026-10-05", "2026-10-05", "2026-10-05",
         "den 2026-10-05", "den 2026-10-05"),
    show("show_weekday", "Was habe ich am Dienstag?", "2026-09-29", "2026-09-29",
         "am Dienstag", "am Dienstag"),
    show("show_person_range", "Was hat Ben am 30.9.?", "2026-09-30", "2026-09-30",
         "am 30.9. Ben", "am 30.9.", "Ben", person_ids=[101], fixture=_G),
]

# ----------------------------------------------------------- AVAILABILITY (7)
CASES += [
    availability("avail_one_day", "Wann habe ich am 29.9. Zeit?", "2026-09-29",
                 "2026-09-29", 60, [101], gold_A_query="am 29.9. ich",
                 gold_B_people="ich", gold_B_when="am 29.9."),
    availability("avail_two", "Wann haben Ben und Cleo am 30.9. Zeit?",
                 "2026-09-30", "2026-09-30", 60, [101, 102],
                 gold_A_query="Ben Cleo am 30.9.", gold_B_people="Ben und Cleo",
                 gold_B_when="am 30.9.", fixture=_G),
    availability("avail_range", "Wann habe ich nächste Woche Zeit?",
                 "2026-09-28", "2026-10-04", 60, [101],
                 gold_A_query="nächste Woche ich", gold_B_people="ich",
                 gold_B_when="nächste Woche"),
    availability("avail_duration_hour", "Wann habe ich am 29.9. eine Stunde Zeit?",
                 "2026-09-29", "2026-09-29", 60, [101],
                 gold_A_query="am 29.9. ich eine Stunde", gold_B_people="ich",
                 gold_B_when="am 29.9.", gold_B_duration="eine Stunde"),
    availability("avail_duration_min", "Wann habe ich am 29.9. 30 Minuten Zeit?",
                 "2026-09-29", "2026-09-29", 30, [101],
                 gold_A_query="am 29.9. ich 30 Minuten", gold_B_people="ich",
                 gold_B_when="am 29.9.", gold_B_duration="30 Minuten"),
    availability("avail_default_window", "Wann haben Ben und Cleo Zeit?",
                 "2026-09-24", "2026-09-30", 60, [101, 102],
                 gold_A_query="Ben Cleo", gold_B_people="Ben und Cleo",
                 fixture=_G),
    availability("avail_allday_busy", "Wann habe ich am 29.9. Zeit?",
                 "2026-09-29", "2026-09-29", 60, [101],
                 gold_A_query="am 29.9. ich", gold_B_people="ich",
                 gold_B_when="am 29.9.",
                 fixture={"kind": "private", "actor": "Ada",
                          "events": [_ev("Urlaub", _dt(9, 29, 0), _dt(9, 30, 0),
                                         all_day=True)]}),
]

# ------------------------------------------------------------------- NEGATIVE
_OFFTOPIC = [
    "Wie wird das Wetter morgen?",
    "Erzähl mir einen Witz.",
    "Wie heißt die Hauptstadt von Frankreich?",
    "Danke, das war alles.",
    "Was ist 2 plus 2?",
    "Wer hat die letzte WM gewonnen?",
    "Spiel mir ein Lied.",
    "Wie geht es dir?",
    "Bestell mir eine Pizza.",
    "Schalte das Licht im Wohnzimmer an.",
]
for i, q in enumerate(_OFFTOPIC):
    CASES.append(negative(f"negative_{i}", q))

# --------------------------------------------------- INDEPENDENT MULTI (20)
def _grp(events):
    return {"kind": "group", "actor": "Ada", "members": ["Ben", "Cleo"],
            "events": events}


def _add_call(when, title, people=""):
    return {"name": "calendar_add",
            "arguments": {"title": title, "when": when, "people": people}}


def _rm_call(target):
    return {"name": "calendar_remove", "arguments": {"target": target}}


def _ch_call_A(target, change):
    return {"name": "calendar_change", "arguments": {"target": target, "change": change}}


def _ch_call_B(target, new_when="", new_title=""):
    return {"name": "calendar_change",
            "arguments": {"target": target, "new_when": new_when,
                          "new_title": new_title}}


def _sh_call_A(query):
    return {"name": "calendar_show", "arguments": {"query": query}}


def _sh_call_B(when="", person=""):
    return {"name": "calendar_show", "arguments": {"when": when, "person": person}}


_Z = _ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))
_M = _ev("Meeting", _dt(9, 30, 10), _dt(9, 30, 11))

_MULTI = [
    ("Trag Dienstag Büro ein und lösch Zahnarzt.",
     [_Z], [_ev("Zahnarzt", _dt(9, 29, 10), _dt(9, 29, 11))],
     [{"op": "add", "title": "Büro", "start": _dt(9, 29, 0), "end": _dt(9, 30, 0),
       "all_day": True},
      {"op": "remove", "target": "Zahnarzt"}],
     [_add_call("Dienstag", "Büro"), _rm_call("Zahnarzt")],
     [_add_call("Dienstag", "Büro"), _rm_call("Zahnarzt")]),
    ("Trag Donnerstag 18 Uhr Sport ein und verschieb Meeting auf 16 Uhr.",
     [_M], [_ev("Meeting", _dt(9, 30, 10), _dt(9, 30, 11))],
     [{"op": "add", "title": "Sport", "start": _dt(10, 1, 18), "end": _dt(10, 1, 19),
       "all_day": False},
      {"op": "change", "target": "Meeting", "start": _dt(9, 30, 16),
       "end": _dt(9, 30, 17), "all_day": False}],
     [_add_call("Donnerstag 18 Uhr", "Sport"), _ch_call_A("Meeting", "16 Uhr")],
     [_add_call("Donnerstag 18 Uhr", "Sport"), _ch_call_B("Meeting", "16 Uhr")]),
    ("Zeig Mittwoch meinen Kalender und trag Donnerstag 18 Uhr Sport ein.",
     [], [],
     [{"op": "add", "title": "Sport", "start": _dt(10, 1, 18), "end": _dt(10, 1, 19),
       "all_day": False}],
     [_sh_call_A("Mittwoch"), _add_call("Donnerstag 18 Uhr", "Sport")],
     [_sh_call_B("Mittwoch"), _add_call("Donnerstag 18 Uhr", "Sport")]),
    ("Trag morgen 8 Uhr Frühstück ein und lösch Meeting.",
     [_M], [_M],
     [{"op": "add", "title": "Frühstück", "start": _dt(9, 25, 8),
       "end": _dt(9, 25, 9), "all_day": False},
      {"op": "remove", "target": "Meeting"}],
     [_add_call("morgen 8 Uhr", "Frühstück"), _rm_call("Meeting")],
     [_add_call("morgen 8 Uhr", "Frühstück"), _rm_call("Meeting")]),
    ("Verschieb Zahnarzt auf 16 Uhr und trag Urlaub am 30.9. ein.",
     [_Z], [_Z],
     [{"op": "change", "target": "Zahnarzt", "start": _dt(9, 29, 16),
       "end": _dt(9, 29, 17), "all_day": False},
      {"op": "add", "title": "Urlaub", "start": _dt(9, 30, 0), "end": _dt(10, 1, 0),
       "all_day": True}],
     [_ch_call_A("Zahnarzt", "16 Uhr"), _add_call("am 30.9.", "Urlaub")],
     [_ch_call_B("Zahnarzt", "16 Uhr"), _add_call("am 30.9.", "Urlaub")]),
    ("Lösch Zahnarzt und zeig den 30.9.", [_Z], [],
     [{"op": "remove", "target": "Zahnarzt"}],
     [_rm_call("Zahnarzt"), _sh_call_A("den 30.9.")],
     [_rm_call("Zahnarzt"), _sh_call_B("den 30.9.")]),
    ("Trag am 28.9. 7 Uhr Zug ein und trag am 29.9. 9 Uhr Büro ein.", [], [],
     [{"op": "add", "title": "Zug", "start": _dt(9, 28, 7), "end": _dt(9, 28, 8),
       "all_day": False},
      {"op": "add", "title": "Büro", "start": _dt(9, 29, 9), "end": _dt(9, 29, 10),
       "all_day": False}],
     [_add_call("am 28.9. 7 Uhr", "Zug"), _add_call("am 29.9. 9 Uhr", "Büro")],
     [_add_call("am 28.9. 7 Uhr", "Zug"), _add_call("am 29.9. 9 Uhr", "Büro")]),
    ("Verschieb Zahnarzt auf den 1.10. und lösch Urlaub.",
     [_Z, _ev("Urlaub", _dt(9, 24, 0), _dt(9, 28, 0), all_day=True)], [_Z],
     [{"op": "change", "target": "Zahnarzt", "start": _dt(10, 1, 10),
       "end": _dt(10, 1, 11), "all_day": False},
      {"op": "remove", "target": "Urlaub"}],
     [_ch_call_A("Zahnarzt", "der 1.10."), _rm_call("Urlaub")],
     [_ch_call_B("Zahnarzt", "der 1.10."), _rm_call("Urlaub")]),
    ("Nenn Zahnarzt in Kontrolltermin um und zeig den 30.9.", [_Z], [],
     [{"op": "change", "target": "Zahnarzt", "start": _dt(9, 29, 10),
       "end": _dt(9, 29, 11), "all_day": False, "new_title": "Kontrolltermin"}],
     [_ch_call_A("Zahnarzt", "Kontrolltermin"), _sh_call_A("den 30.9.")],
     [_ch_call_B("Zahnarzt", new_title="Kontrolltermin"), _sh_call_B("den 30.9.")]),
    ("Trag Mittwoch 14 Uhr Kaffee ein und lösch Zahnarzt.", [_Z], [_Z],
     [{"op": "add", "title": "Kaffee", "start": _dt(9, 30, 14),
       "end": _dt(9, 30, 15), "all_day": False},
      {"op": "remove", "target": "Zahnarzt"}],
     [_add_call("Mittwoch 14 Uhr", "Kaffee"), _rm_call("Zahnarzt")],
     [_add_call("Mittwoch 14 Uhr", "Kaffee"), _rm_call("Zahnarzt")]),
    ("Zeig Dienstag und lösch Meeting.", [_M], [_M],
     [{"op": "remove", "target": "Meeting"}],
     [_sh_call_A("Dienstag"), _rm_call("Meeting")],
     [_sh_call_B("Dienstag"), _rm_call("Meeting")]),
    ("Verschieb Meeting auf 16 Uhr und trag am 29.9. Büro ein.", [_M], [_M],
     [{"op": "change", "target": "Meeting", "start": _dt(9, 30, 16),
       "end": _dt(9, 30, 17), "all_day": False},
      {"op": "add", "title": "Büro", "start": _dt(9, 29, 0), "end": _dt(9, 30, 0),
       "all_day": True}],
     [_ch_call_A("Meeting", "16 Uhr"), _add_call("am 29.9.", "Büro")],
     [_ch_call_B("Meeting", "16 Uhr"), _add_call("am 29.9.", "Büro")]),
    ("Lösch Zahnarzt und trag Urlaub vom 24.9. bis 27.9. ein.", [_Z], [_Z],
     [{"op": "remove", "target": "Zahnarzt"},
      {"op": "add", "title": "Urlaub", "start": _dt(9, 24, 0), "end": _dt(9, 28, 0),
       "all_day": True}],
     [_rm_call("Zahnarzt"), _add_call("vom 24.9. bis 27.9.", "Urlaub")],
     [_rm_call("Zahnarzt"), _add_call("vom 24.9. bis 27.9.", "Urlaub")]),
    ("Trag am 30.9. Messe ein und zeig nächste Woche.", [], [],
     [{"op": "add", "title": "Messe", "start": _dt(9, 30, 0), "end": _dt(10, 1, 0),
       "all_day": True}],
     [_add_call("am 30.9.", "Messe"), _sh_call_A("nächste Woche")],
     [_add_call("am 30.9.", "Messe"), _sh_call_B("nächste Woche")]),
    ("Verschieb Zahnarzt auf den 30.9. und trag Sport am 1.10. 18 Uhr ein.",
     [_Z], [_Z],
     [{"op": "change", "target": "Zahnarzt", "start": _dt(9, 30, 10),
       "end": _dt(9, 30, 11), "all_day": False},
      {"op": "add", "title": "Sport", "start": _dt(10, 1, 18), "end": _dt(10, 1, 19),
       "all_day": False}],
     [_ch_call_A("Zahnarzt", "der 30.9."), _add_call("am 1.10. 18 Uhr", "Sport")],
     [_ch_call_B("Zahnarzt", "der 30.9."), _add_call("am 1.10. 18 Uhr", "Sport")]),
    ("Lösch Meeting und trag morgen 8 Uhr Frühstück ein.", [_M], [_M],
     [{"op": "remove", "target": "Meeting"},
      {"op": "add", "title": "Frühstück", "start": _dt(9, 25, 8),
       "end": _dt(9, 25, 9), "all_day": False}],
     [_rm_call("Meeting"), _add_call("morgen 8 Uhr", "Frühstück")],
     [_rm_call("Meeting"), _add_call("morgen 8 Uhr", "Frühstück")]),
    ("Zeig heute und trag Donnerstag 18 Uhr Sport ein.", [], [],
     [{"op": "add", "title": "Sport", "start": _dt(10, 1, 18), "end": _dt(10, 1, 19),
       "all_day": False}],
     [_sh_call_A("heute"), _add_call("Donnerstag 18 Uhr", "Sport")],
     [_sh_call_B("heute"), _add_call("Donnerstag 18 Uhr", "Sport")]),
    ("Verschieb Meeting auf 15 bis 17 Uhr und lösch Zahnarzt.", [_M, _Z], [_Z],
     [{"op": "change", "target": "Meeting", "start": _dt(9, 30, 15),
       "end": _dt(9, 30, 17), "all_day": False},
      {"op": "remove", "target": "Zahnarzt"}],
     [_ch_call_A("Meeting", "15 bis 17 Uhr"), _rm_call("Zahnarzt")],
     [_ch_call_B("Meeting", "15 bis 17 Uhr"), _rm_call("Zahnarzt")]),
    ("Trag am 28.9. Zug ein und verschieb Zahnarzt auf 16 Uhr.", [_Z], [_Z],
     [{"op": "add", "title": "Zug", "start": _dt(9, 28, 0), "end": _dt(9, 29, 0),
       "all_day": True},
      {"op": "change", "target": "Zahnarzt", "start": _dt(9, 29, 16),
       "end": _dt(9, 29, 17), "all_day": False}],
     [_add_call("am 28.9.", "Zug"), _ch_call_A("Zahnarzt", "16 Uhr")],
     [_add_call("am 28.9.", "Zug"), _ch_call_B("Zahnarzt", "16 Uhr")]),
    ("Lösch Urlaub und zeig den 30.9.",
     [_ev("Urlaub", _dt(9, 24, 0), _dt(9, 28, 0), all_day=True)], [],
     [{"op": "remove", "target": "Urlaub"}],
     [_rm_call("Urlaub"), _sh_call_A("den 30.9.")],
     [_rm_call("Urlaub"), _sh_call_B("den 30.9.")]),
    ("Trag am 29.9. 9 Uhr Büro ein und trag am 30.9. 10 Uhr Meeting ein.", [], [],
     [{"op": "add", "title": "Büro", "start": _dt(9, 29, 9), "end": _dt(9, 29, 10),
       "all_day": False},
      {"op": "add", "title": "Meeting", "start": _dt(9, 30, 10),
       "end": _dt(9, 30, 11), "all_day": False}],
     [_add_call("am 29.9. 9 Uhr", "Büro"), _add_call("am 30.9. 10 Uhr", "Meeting")],
     [_add_call("am 29.9. 9 Uhr", "Büro"), _add_call("am 30.9. 10 Uhr", "Meeting")]),
]

for i, (q, base, extra, ops, ga, gb) in enumerate(_MULTI):
    events = list(base) + [e for e in extra if e not in base]
    # remove-op targets that are not in the fixture must still exist for apply;
    # fixtures carry all referenced titles.
    CASES.append(multi(f"multi_{i:02d}", q, _grp(events) if events else
                       {"kind": "private", "actor": "Ada", "events": []},
                       ops, ga, gb))

# Contract expressiveness markers: cases only one contract can express are not
# scored against the other (reported as a structural limit, not a model error).
for _c in CASES:
    if _c["id"] == "change_rename_time":
        _c["contracts"] = ["B"]


def contracts_for(case: dict) -> list[str]:
    return case.get("contracts", ["A", "B"])


def by_family(family: str) -> list[dict]:
    return [c for c in CASES if c["family"] == family]


def by_id(cid: str) -> dict | None:
    return next((c for c in CASES if c["id"] == cid), None)
