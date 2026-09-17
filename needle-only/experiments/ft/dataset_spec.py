"""FT-Dataset v2 spec: pools, gold-call conventions, template families.

Principles (contract with build_dataset/validate_dataset):
- Tool schemas are NEVER duplicated here — they come from the production
  `build_tools()` via needle's own `fn._needle_tool` (build_schema).
- Gold calls are deterministic: input + family -> exact args, no LLM.
- Grounding: string argument values are literal substrings of the query,
  with documented exceptions (enums/ints; resolver-capability gaps listed
  in UNSUPPORTED_SPANS).
- Train/validation/test are split by TEMPLATE FAMILY (never by values
  alone); validation/test additionally use held-out value pools.
- Language mix per family: 60% short natural German, 25% short Gemma-style
  atomic instructions, 15% simple English (finding ac150d1: short/direct
  beats verbose canonical sentences).
"""

from __future__ import annotations

import random

SYSTEM_FACTS = "date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi"
REF_DATE = "2026-09-13"

# Gold-supervision convention (user review Sep 16, gold_ab measurement):
# SPARSE/evidenced-only — omit every argument the query does not evidence.
# Needle's own finetune generator prescribes exactly this ("only values
# evidenced in the query", needle/model/finetune.py) and Base Needle behaves
# the same way (it drops defaults ~99% of the time; gold_ab_report.json).
# Production handlers carry identical deterministic defaults, so omission is
# execution-safe.
EVIDENCE_TRIGGERS = {
    "horizon": r"heute|diese woche|nächste woche|diesen monat|this week|today|this month|kommende woche|next week",
    "duration_min": r"\d+\s*(minute|min\b|stunde|std\b|hour)",
    "days": r"\bdays?\b|tage",
}

# Temporal spans the production resolver cannot parse: never used as gold.
# ("diesen Freitag"/"am Wochenende" -> resolve_date None; "in drei Tagen"
# spelled out -> _REL_OFFSET needs digits; periods are dropped for
# move/find_slot because their handlers take a bare day span.)
UNSUPPORTED_SPANS = ["diesen Freitag", "am Wochenende", "in drei Tagen",
                     "in zwei Wochen", "am Freitag", "am Montag"]

# --------------------------------------------------------------------- pools

TITLES_TRAIN = ["Zahnarzt", "Teammeeting", "Physiotherapie", "Friseur",
                "Yoga", "Workshop", "Buchhaltung", "Sprachkurs", "Kegelabend",
                "Chorprobe", "Arzttermin", "Kino", "Training", "Fußball"]
TITLES_EVAL_ONLY = ["TÜV", "Elternabend", "Klettertraining", "Augenarzt",
                    "Steuerberatung", "Schwimmkurs", "Klavierunterricht",
                    "Nachhilfe", "Projektbesprechung", "Haarschnitt"]
ABSENCE_TITLES_TRAIN = ["Urlaub", "Dienstreise", "Fortbildung"]
ABSENCE_TITLES_EVAL_ONLY = ["Klassenfahrt", "Seminarwoche", "Elternzeit"]

NAMES_TRAIN = ["Lisa", "Max", "Anna", "Tim", "Jana", "Paul", "Lena", "Jonas"]
NAMES_EVAL_ONLY = ["Miriam", "Felix", "Nora", "Tom", "Emma", "Clara"]

DAY_ISO_TRAIN = ["17.9.", "23.09.", "7.9.", "5.10.", "14.9.", "2.10.",
                 "9.10.", "30.9.", "21.10."]
DAY_ISO_EVAL = ["21.11.", "3.12.", "18.12.", "5.5.", "9.6.", "28.10.",
                "30.11.", "12.12."]
DAY_ISO_YEAR_TRAIN = ["17.09.2026", "23.09.2026", "7.10.2026"]
DAY_ISO_YEAR_EVAL = ["12.05.2027", "01.11.2026", "18.06.2027"]
DAY_MONTH_TRAIN = ["17. September", "3. August", "1. Oktober", "20. Juni",
                   "5. Dezember"]
DAY_MONTH_EVAL = ["21. November", "12. Mai", "6. Januar", "28. Februar",
                  "14. Juli"]
WEEKDAY_PLAIN = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag",
                 "Samstag"]
WEEKDAY_REL_FUTURE = ["nächsten Montag", "nächsten Freitag",
                      "nächsten Donnerstag", "kommenden Montag"]
WEEKDAY_REL_PAST = ["letzten Dienstag", "letzten Freitag", "letzten Montag"]
OFFSET_DAYS_TRAIN = ["in 2 Tagen", "in 3 Tagen", "in 5 Tagen", "in 1 Woche"]
OFFSET_DAYS_EVAL = ["in 4 Tagen", "in 6 Tagen", "in 2 Wochen"]
DAYPART_TRAIN = ["morgen Nachmittag", "heute Abend", "morgen Vormittag",
                 "übermorgen Nachmittag", "heute Morgen"]
DAYPART_EVAL = ["morgen Abend", "heute Vormittag", "übermorgen Vormittag"]

TIME_24H_TRAIN = ["14:00", "10:00", "13:00", "9:30", "15:00", "8:00",
                  "16:30", "11:00"]
TIME_24H_EVAL = ["11:15", "14:45", "9:00", "17:30", "12:20", "18:40"]
TIME_UHR_TRAIN = ["14 Uhr", "10 Uhr", "9 Uhr", "15 Uhr", "13 Uhr", "8 Uhr"]
TIME_UHR_EVAL = ["11 Uhr", "16 Uhr", "17 Uhr", "12 Uhr"]
DURATION_TRAIN = [30, 45, 60, 90, 120]
DURATION_EVAL = [20, 75, 150]

NEGATIVES_OFFTOPIC = [
    "Wie wird das Wetter morgen?", "Erzähl mir einen Witz.",
    "Wer hat den DSM erfunden?", "Wie spät ist es in Tokio?",
    "Wie viel ist 47 mal 13?", "Was kostet eine Briefmarke?",
    "Welche Filme laufen im Kino?", "Schreib mir ein Gedicht über den Herbst.",
    "What's the weather like tomorrow?", "Tell me a joke.",
    "Who invented the telephone?", "What time is it in London?",
    "Recommend a good book.", "What is the capital of Norway?",
    "Wie rechnet man Prozent aus?", "Nenne mir ein Rezept für Pfannkuchen.",
    "Welcher Tag ist heute?", "Wann ist Ostern?",
    "Erkläre mir Photosynthese.", "Wie hoch ist der Eiffelturm?",
    "Hast du Lust auf Sudoku?", "Was bedeutet ad hoc?",
]
_OFFTOPIC_SUBJECTS = ["ein Handyvertrag", "eine Bahnfahrt", "eine Tasse Kaffee",
                      "ein Bahnticket", "eine Steuererklärung",
                      "eine Comicsammlung", "ein Zeitungsabo"]
_OFFTOPIC_CITY = ["Rom", "Wien", "Zürich", "New York", "Amsterdam",
                  "Kopenhagen", "Lissabon", "Warschau", "Hamburg", "Prag"]
_OFFTOPIC_INVENT = ["die Schallplatte", "den Kühlschrank", "die Postleitzahl",
                    "den Fahrradreifen", "den Terminkalender", "das Fernglas"]
_NEG_FILLER = ["", "Sag mal, ", "Hey, ", "Weißt du, ", ""]
_NEG_TAIL = ["", "!", "?", ".", " Danke.", ""]

# ----------------------------------------------------------------- gold calls

def _args_full(schema_props: dict, filled: dict, tool: str) -> dict:
    """SPARSE gold: only evidenced arguments (overridden below by
    gold_args, which sees the query)."""
    return {k: v for k, v in filled.items()}


def _args_sparse(filled: dict, query: str) -> dict:
    """Evidenced-only gold (gold_ab decision): keep non-empty strings;
    enum/int fields only when the query evidences them (trigger regex).
    Runs BEFORE default handling — used by build_dataset for every tool."""
    import re as _re
    out = {}
    for k, v in filled.items():
        if v in ("", None):
            continue
        if k in EVIDENCE_TRIGGERS and not _re.search(
                EVIDENCE_TRIGGERS[k], query, _re.IGNORECASE):
            continue
        out[k] = v
    return out


def gold_reasoning(input_text: str, filled: dict) -> str:
    parts = [f"{k} span '{v}' from query" for k, v in filled.items()
             if v not in ("", None)]
    return "; ".join(parts) or "no tool arguments evidenced"

# -------------------------------------------------------------- slot sampling


def draw(rng: random.Random, pool: list, k: int = 1) -> list:
    return rng.sample(pool, k)


def draw_lang(rng: random.Random) -> str:
    """60/25/15 language mix (plan §4)."""
    r = rng.random()
    return "de" if r < 0.60 else ("gemma" if r < 0.85 else "en")


def draw_temporal(rng: random.Random, split: str) -> tuple[str, str]:
    """Weighted temporal-class draw; returns (span, class).
    Heavy classes (plan §5): ranges, weekday-relative, daypart, offsets."""
    train = [
        ("17.9.", "explicit_iso_dmy", 3), ("23.09.", "explicit_iso_dmy", 2),
        ("7.9.", "explicit_iso_dmy", 2), ("5.10.", "explicit_iso_dmy", 2),
        ("17.09.2026", "explicit_iso_dmy_year", 1),
        ("17. September", "explicit_month_name", 2),
        ("3. August", "explicit_month_name", 1),
        ("nächsten Freitag", "weekday_relative", 3),
        ("nächsten Montag", "weekday_relative", 2),
        ("letzten Dienstag", "weekday_relative_past", 2),
        ("Montag", "weekday_plain", 1), ("Freitag", "weekday_plain", 2),
        ("in 3 Tagen", "relative_offset_days", 2),
        ("in 5 Tagen", "relative_offset_days", 1),
        ("morgen", "relative_next_day", 3), ("übermorgen", "relative_next_day", 1),
        ("heute", "relative_today", 2),
    ]
    evalp = [
        ("21.11.", "explicit_iso_dmy", 2), ("3.12.", "explicit_iso_dmy", 2),
        ("12. Mai", "explicit_month_name", 2), ("21. November", "explicit_month_name", 2),
        ("01.11.2026", "explicit_iso_dmy_year", 1),
        ("nächsten Donnerstag", "weekday_relative", 2),
        ("letzten Freitag", "weekday_relative_past", 1),
        ("in 4 Tagen", "relative_offset_days", 2),
        ("Samstag", "weekday_plain", 1),
    ]
    pool = train if split == "train" else evalp
    spans = [p for p in pool]
    total = sum(w for _, _, w in spans)
    r = rng.uniform(0, total)
    acc = 0
    for span, cls, w in spans:
        acc += w
        if r <= acc:
            return span, cls
    return spans[-1][0], spans[-1][1]


def draw_time(rng: random.Random, split: str) -> str:
    pool = TIME_24H_TRAIN + TIME_UHR_TRAIN if split == "train" \
        else TIME_24H_EVAL + TIME_UHR_EVAL
    return rng.choice(pool)


def draw_persons(rng: random.Random, split: str, k: int | None = None) -> tuple[str, int]:
    pool = NAMES_TRAIN if split == "train" else NAMES_EVAL_ONLY
    k = k or rng.choice([1, 1, 2, 2, 3])
    return ", ".join(draw(rng, pool, k)), k


def draw_title(rng: random.Random, split: str) -> str:
    pool = TITLES_TRAIN if split == "train" else TITLES_EVAL_ONLY
    return rng.choice(pool)


_MONTHS = ("januar", "februar", "märz", "april", "mai", "juni", "juli",
           "august", "september", "oktober", "november", "dezember")
_WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag",
             "samstag", "sonntag")


def temporal_class_of(span: str) -> str:
    """Deterministic temporal-class tag for one argument span (plan §5)."""
    s = (span or "").strip().lower()
    if not s:
        return "none"
    if any(m in s for m in _WEEKDAYS):
        if s.startswith(("nächsten", "kommenden")):
            return "weekday_relative"
        if s.startswith("letzten"):
            return "weekday_relative_past"
        return "weekday_plain"
    if s.startswith("in ") and s.split()[1].isdigit():
        return "relative_offset_days"
    if s in ("morgen", "übermorgen", "heute"):
        return {"morgen": "relative_next_day", "übermorgen": "relative_next_day",
                "heute": "relative_today"}[s]
    if any(m in s for m in _MONTHS):
        return "explicit_month_name"
    if "20" in s and s.count(".") >= 2:
        return "explicit_iso_dmy_year"
    if "." in s:
        return "explicit_iso_dmy"
    return "other"


# --------------------------------------------------------- template families
# Each family: id, tool, split, weight, build(rng, props) -> (input, filled)
# build() draws slots and composes the input; gold args derive deterministi-
# cally from the drawn slots (plan §12). Families are split-exclusive (§10).

def _t_date(rng, split, cls_hint=None):
    return draw_temporal(rng, split)


def fam_create_time(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    t = draw_time(rng, split)
    title = draw_title(rng, split)
    filled = {"title": title, "date": d, "time": t}
    if lang == "de":
        inp = rng.choice([f"Termin {title} {d} um {t}",
                          f"Trag {title} für {d} um {t} ein",
                          f"{title} {d} {t}"])
    elif lang == "gemma":
        inp = rng.choice([f"Create a {title} appointment on {d} at {t}.",
                          f"Neuer Termin {title}, {d}, {t}."])
    else:
        inp = f"Add {title} on {d} at {t}"
    return inp, filled


def fam_create_time_range(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    t1, t2 = sorted(draw(rng, ["10:00", "13:00", "16:00", "9:00", "13:30",
                               "17:00", "11:00", "15:00"], 2))
    title = draw_title(rng, split)
    filled = {"title": title, "date": d, "time": t1, "end_time": t2}
    if lang == "de":
        inp = rng.choice([f"{title} {d} von {t1} bis {t2}",
                          f"Meeting {title} {d} {t1} bis {t2}"])
    elif lang == "gemma":
        inp = f"Create a {title} appointment on {d} from {t1} to {t2}."
    else:
        inp = f"{title} on {d} {t1} to {t2}"
    return inp, filled


def fam_create_day_only(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    title = draw_title(rng, split)
    filled = {"title": title, "date": d}
    if lang == "de":
        inp = rng.choice([f"Trag {title} für {d} ein",
                          f"{title} {d} ganztägig",
                          f"Notier {title} {d}"])
    elif lang == "gemma":
        inp = f"Create a {title} all-day appointment on {d}."
    else:
        inp = f"Add {title} on {d}"
    return inp, filled


def fam_create_absence(rng, split, lang):
    pool = ABSENCE_TITLES_TRAIN if split == "train" else ABSENCE_TITLES_EVAL_ONLY
    title = rng.choice(pool)
    pair = _range_pair(rng, split)
    if pair is not None and rng.random() < 0.55:
        d1, d2 = pair
        filled = {"title": title, "date": d1, "until": d2}
        if lang == "gemma":
            inp = f"Create a {title} absence from {d1} to {d2}."
        elif lang == "de":
            inp = rng.choice([f"{title} vom {d1} bis {d2}",
                              f"Ich bin {d1} bis {d2} wegen {title} weg",
                              f"Trag {title} für {d1} bis {d2} ein"])
        else:
            inp = rng.choice([f"{title} from {d1} to {d2}",
                              f"I am on {title} {d1} until {d2}",
                              f"Book {title} starting {d1} until {d2}"])
        return inp, filled
    d, cls = draw_temporal(rng, split)  # single-day absence (contrast §8)
    if lang == "gemma":
        inp = f"Create a {title} all-day absence on {d}."
    elif lang == "de":
        inp = rng.choice([f"{title} ab {d}", f"{title} {d}",
                          f"Ich habe {d} {title}"])
    else:
        inp = f"{title} starting {d}"
    return inp, {"title": title, "date": d}


def fam_create_participants(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    t = draw_time(rng, split)
    title = draw_title(rng, split)
    persons, k = draw_persons(rng, split)
    filled = {"title": title, "date": d, "time": t, "participants": persons}
    if lang == "de":
        inp = rng.choice([f"Termin {title} {d} um {t} mit {persons}",
                          f"{title} {d} {t} mit {persons}"])
    elif lang == "gemma":
        inp = f"Create a {title} appointment on {d} at {t} with {persons}."
    else:
        inp = f"Set up {title} with {persons} on {d} at {t}"
    return inp, filled


def fam_create_polite(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    t = draw_time(rng, split)
    title = draw_title(rng, split)
    filled = {"title": title, "date": d, "time": t}
    inp = rng.choice([f"Stellst du bitte für {d} um {t} einen Termin "
                      f"„{title}“ ein?",
                      f"Kannst du {title} am {d} um {t} eintragen?"])
    return inp, filled


def fam_move_date(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    title = draw_title(rng, split)
    filled = {"title": title, "date": d}
    if lang == "de":
        inp = rng.choice([f"Verschieb {title} auf {d}",
                          f"Schieb {title} auf {d}"])
    elif lang == "gemma":
        inp = f"Move the {title} appointment to {d}."
    else:
        inp = f"Move my {title} appointment to {d}"
    return inp, filled


def fam_move_time(rng, split, lang):
    t = draw_time(rng, split)
    title = draw_title(rng, split)
    filled = {"title": title, "time": t}
    if lang == "de":
        inp = rng.choice([f"Verschieb {title} auf {t}",
                          f"Setz {title} auf {t}"])
    elif lang == "gemma":
        inp = f"Move the {title} appointment to {t}."
    else:
        inp = f"Move my {title} to {t}"
    return inp, filled


def fam_move_date_time(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    t = draw_time(rng, split)
    title = draw_title(rng, split)
    filled = {"title": title, "date": d, "time": t}
    if lang == "de":
        inp = rng.choice([f"Verschieb {title} auf {d} um {t}",
                          f"{title} auf {d}, {t} verschieben"])
    elif lang == "gemma":
        inp = f"Move the {title} appointment to {d} at {t}."
    else:
        inp = f"Move {title} to {d} at {t}"
    return inp, filled


def fam_delete_title(rng, split, lang):
    title = draw_title(rng, split)
    filled = {"title": title}
    if lang == "de":
        inp = rng.choice([f"Lösch {title}", f"{title} löschen",
                          f"Nimm {title} raus"])
    elif lang == "gemma":
        inp = f"Delete the {title} appointment."
    else:
        inp = f"Delete my {title} appointment"
    return inp, filled


def fam_delete_title_date(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    title = draw_title(rng, split)
    filled = {"title": title, "date": d}
    if lang == "de":
        inp = rng.choice([f"Lösch {title} am {d}", f"{title} am {d} löschen"])
    elif lang == "gemma":
        inp = f"Delete the {title} appointment on {d}."
    else:
        inp = f"Delete my {title} on {d}"
    return inp, filled


def fam_delete_generic(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    filled = {"title": "Termin", "date": d}
    if lang == "de":
        inp = rng.choice([f"Lösch den Termin am {d}!",
                          f"Termin am {d} löschen",
                          f"Lösch den Termin am {d} um {draw_time(rng, split)}!"])
    else:
        inp = rng.choice([f"Delete the „Termin“ appointment on {d}.",
                          f"Delete the „Termin“ on {d} at {draw_time(rng, split)}"])
    return inp, filled


def fam_list_horizon(rng, split, lang):
    h, horizon = rng.choice([("heute", "today"), ("diese Woche", "week"),
                             ("diesen Monat", "month"),
                             ("heute", "today"), ("diese Woche", "week")])
    filled = {"person": "", "date": "", "until": "", "horizon": horizon}
    if lang == "de":
        inp = rng.choice([f"Was steht {h} an?", f"Zeig meine Termine {h}",
                          f"Was habe ich {h} vor?", f"Termine {h} bitte",
                          f"Mein Kalender {h}", f"Kalender {h}",
                          f"Was ist {h} los?", f"Zeig {h}"])
    elif lang == "gemma":
        inp = rng.choice([f"Show my appointments for {h}.",
                          f"List my calendar {h}.",
                          f"Show my calendar {h}."])
    else:
        inp = rng.choice(["Show my appointments today",
                          "Show my appointments this week",
                          "Show my calendar for today",
                          "Show my calendar for this week",
                          "What's on my calendar today?",
                          "What's on this week?"])
        filled["horizon"] = "today" if "today" in inp else "week"
    return inp, filled


def fam_list_date(rng, split, lang):
    d, cls = draw_temporal(rng, split)
    filled = {"person": "", "date": d, "until": ""}
    if lang == "de":
        inp = rng.choice([f"Was ist am {d}?", f"Zeig {d}",
                          f"Was steht am {d} an?"])
    elif lang == "gemma":
        inp = f"Show my calendar on {d}."
    else:
        inp = f"What's on my calendar on {d}?"
    return inp, filled


def fam_list_range(rng, split, lang):
    pair = _range_pair(rng, split) or (rng.choice(["17.9.", "7.9."]), "9.9.")
    d1, d2 = pair
    filled = {"person": "", "date": d1, "until": d2}
    if lang == "de":
        inp = rng.choice([f"Zeig vom {d1} bis {d2}",
                          f"Welche Termine vom {d1} bis {d2}?"])
    elif lang == "gemma":
        inp = f"Show my events from {d1} to {d2}."
    else:
        inp = f"Show my events {d1} to {d2}"
    return inp, filled


def fam_list_person(rng, split, lang):
    h, horizon = rng.choice([("diese Woche", "week"), ("nächste Woche", "week"),
                             ("heute", "today")])
    persons, k = draw_persons(rng, split, k=1)
    filled = {"person": persons, "date": "", "until": "", "horizon": horizon}
    if lang == "de":
        inp = rng.choice([f"Wann hat {persons} {h} Termine?",
                          f"Zeig den Kalender von {persons} {h}",
                          f"Was hat {persons} {h} vor?"])
    elif lang == "gemma":
        inp = rng.choice([f"Show {persons}'s appointments {h}.",
                          f"List {persons}'s calendar {h}."])
    else:
        inp = rng.choice([f"Show me {persons}'s calendar this week",
                          f"When does {persons} have events {h}?"])
    return inp, filled


def fam_list_person_range(rng, split, lang):
    persons, k = draw_persons(rng, split, k=1)
    pair = _range_pair(rng, split) or (rng.choice(["17.9.", "7.9."]), "9.9.")
    d1, d2 = pair
    filled = {"person": persons, "date": d1, "until": d2, "horizon": "week"}
    if lang == "de":
        inp = rng.choice([f"Was hat {persons} zwischen {d1} und {d2} vor?",
                          f"Termine von {persons} vom {d1} bis {d2}",
                          f"Zeig {persons} {d1} bis {d2}"])
    elif lang == "gemma":
        inp = f"Show {persons}'s events from {d1} to {d2}."
    else:
        inp = f"Show {persons}'s calendar {d1} to {d2}"
    return inp, filled


def fam_list_question(rng, split, lang):
    """Near-negative positives (plan §14): questions that must NOT create."""
    title = draw_title(rng, split)
    filled = {"person": "", "date": "", "until": "", "horizon": "week"}
    if lang == "de":
        inp = rng.choice([f"Wann ist mein {title}-Termin?",
                          f"Habe ich einen Termin {title}?"])
    else:
        inp = f"When is my {title} appointment?"
    return inp, filled


def fam_find_persons(rng, split, lang):
    persons, k = draw_persons(rng, split)
    filled = {"persons": persons, "duration_min": 60,
              "date": "", "until": "", "days": 7}
    if lang == "de":
        inp = rng.choice([f"Wann haben {persons} gemeinsam Zeit?",
                          f"Such freie Slots für {persons}"])
    elif lang == "gemma":
        inp = f"Find common free slots for {persons}."
    else:
        inp = f"When are {persons} free?"
    return inp, filled


def fam_find_duration(rng, split, lang):
    persons, k = draw_persons(rng, split)
    dur = rng.choice(DURATION_TRAIN if split == "train" else DURATION_EVAL)
    d, cls = draw_temporal(rng, split)
    filled = {"persons": persons, "duration_min": dur,
              "date": d, "until": "", "days": 7}
    if lang == "de":
        inp = rng.choice([f"{dur} Minuten frei für {persons} {d}",
                          f"Wann passt {dur} Minuten bei {persons} {d}?"])
    elif lang == "gemma":
        inp = f"Find a {dur}-minute slot for {persons} on {d}."
    else:
        inp = f"Find {dur} minutes for {persons} on {d}"
    return inp, filled


def fam_find_daypart(rng, split, lang):
    persons, k = draw_persons(rng, split)
    base = rng.choice(["morgen", "heute", "übermorgen"])
    filled = {"persons": persons, "duration_min": 60,
              "date": base, "until": "", "days": 7}
    if lang == "de":
        inp = rng.choice([f"Wann passt bei {persons} {base} Nachmittag was?",
                          f"Freie Zeit {base} Nachmittag für {persons}"])
    elif lang == "gemma":
        inp = f"Free slots for {persons} on {base} afternoon."
    else:
        inp = f"When are {persons} free {base} afternoon?"
    return inp, filled


def fam_find_range(rng, split, lang):
    persons, k = draw_persons(rng, split)
    pair = _range_pair(rng, split) or (rng.choice(["17.9.", "7.9."]), "9.9.")
    d1, d2 = pair
    filled = {"persons": persons, "duration_min": 60,
              "date": d1, "until": d2, "days": 7}
    if lang == "de":
        inp = f"Wann haben {persons} zwischen {d1} und {d2} gemeinsam Zeit?"
    elif lang == "gemma":
        inp = f"Find free slots for {persons} from {d1} to {d2}."
    else:
        inp = f"Common free time for {persons} {d1} to {d2}"
    return inp, filled


_ADJ_TEMPLATES = ["Was kostet ein {t}?", "Wie lange dauert {t}?",
                  "Was ist ein {t}?", "Brauche ich für {t} einen Termin?"]


def _neg_pool() -> dict[str, list[str]]:
    """All negative query combinations, deterministically ordered; splits
    draw from disjoint slices so no exact query repeats across splits."""
    global _NEG_CACHE
    if _NEG_CACHE is None:
        import itertools
        pool = {}
        off = [f"{f}{q}{t}" for f, q, t in itertools.product(
            _NEG_FILLER, NEGATIVES_OFFTOPIC, _NEG_TAIL)]
        subj = [f"{f}Wie viel kostet {s}?{t}" for f, s, t in itertools.product(
            _NEG_FILLER, _OFFTOPIC_SUBJECTS, _NEG_TAIL)]
        city = [f"{f}Wie spät ist es in {c}?{t}"
                for f, c, t in itertools.product(
                    _NEG_FILLER, _OFFTOPIC_CITY, _NEG_TAIL)]
        inv = [f"{f}Wer hat {i} erfunden?{t}" for f, i, t in itertools.product(
            _NEG_FILLER, _OFFTOPIC_INVENT, _NEG_TAIL)]
        pool["offtopic"] = sorted(set(off + subj + city + inv))
        adj = [f"{f}{tpl}{t}" for f, tpl, t in itertools.product(
            _NEG_FILLER, _ADJ_TEMPLATES, _NEG_TAIL)]
        adj_titles = [f"{f}{tpl.format(t=title)}{t}" for f, tpl, title, t in
                      itertools.product(_NEG_FILLER, _ADJ_TEMPLATES,
                                        TITLES_TRAIN + ABSENCE_TITLES_TRAIN,
                                        _NEG_TAIL)]
        pool["adjacent"] = sorted(set(adj + adj_titles))
        rng = random.Random(999)
        for v in pool.values():
            rng.shuffle(v)
        _NEG_CACHE = pool
    return _NEG_CACHE


_NEG_CACHE = None


def negative_slice(split: str, n: int) -> list[tuple[str, str]]:
    """n deterministic, split-disjoint negative queries; kind in
    {offtopic, adjacent} with 2:1 mix."""
    global _NEG_CACHE
    if _NEG_CACHE is None:
        import itertools
        off = [f"{f}{q}{t}" for f, q, t in itertools.product(
            _NEG_FILLER, NEGATIVES_OFFTOPIC, _NEG_TAIL)]
        off += [f"{f}Wie viel kostet {s}?{t}" for f, s, t in itertools.product(
            _NEG_FILLER, _OFFTOPIC_SUBJECTS, _NEG_TAIL)]
        off += [f"{f}Wie spät ist es in {c}?{t}" for f, c, t in itertools.product(
            _NEG_FILLER, _OFFTOPIC_CITY, _NEG_TAIL)]
        off += [f"{f}Wer hat {i} erfunden?{t}" for f, i, t in itertools.product(
            _NEG_FILLER, _OFFTOPIC_INVENT, _NEG_TAIL)]
        adj = [f"{f}{tpl.format(t=title)}{t}" for f, tpl, title, t in
               itertools.product(_NEG_FILLER, _ADJ_TEMPLATES,
                                 TITLES_TRAIN + ABSENCE_TITLES_TRAIN,
                                 _NEG_TAIL)]
        rng = random.Random(999)
        off, adj = sorted(set(off)), sorted(set(adj))
        rng.shuffle(off)
        rng.shuffle(adj)
        _NEG_CACHE = {"offtopic": off, "adjacent": adj}
    span = {"train": 0.6, "validation": 0.2, "test": 0.2}[split]
    out = []
    for kind in ("offtopic", "adjacent"):
        pool = _NEG_CACHE[kind]
        start = int(len(pool) * (0.0 if split == "train" else
                                 0.6 if split == "validation" else 0.8))
        end = start + int(len(pool) * span)
        k = round(n * (0.55 if kind == "offtopic" else 0.45))
        if k > end - start:
            raise ValueError(f"negative {kind} pool too small for split "
                             f"{split}: need {k}, slice has {end - start}")
        for i in range(k):
            out.append((pool[start + i], kind))
    return out


def _range_pair(rng: random.Random, split: str) -> tuple[str, str] | None:
    """Start/end day pair for a range (end 2-14 days after start), same
    expression family; None when the start span is not resolvable (then
    callers fall back to single-day gold)."""
    import datetime as dt
    from local_calendar import calendar as cal
    d1, _ = draw_temporal(rng, split)
    start = cal.resolve_date(d1, cal.now().date(), roll=True)
    if start is None:
        return None
    end = start + dt.timedelta(days=rng.choice([2, 4, 7, 14]))
    s = d1.lower()
    if s.count(".") == 2 or "20" in s:
        return d1, f"{end.day:02d}.{end.month:02d}.{end.year}"
    if "." in s:
        return d1, f"{end.day}.{end.month}."
    months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
              "August", "September", "Oktober", "November", "Dezember"]
    return d1, f"{end.day}. {months[end.month - 1]}"


# ------------------------------------------------------------------ registry
# weight = relative share within its tool; target_rates = tool distribution
# incl. negatives (plan §6). contrast groups pair near-minimal inputs (§8).

FAMILIES = [
    # calendar_create 30%
    {"id": "create_time", "tool": "calendar_create", "split": "train", "weight": 4, "fn": fam_create_time},
    {"id": "create_time_range", "tool": "calendar_create", "split": "train", "weight": 3, "fn": fam_create_time_range},
    {"id": "create_day_only", "tool": "calendar_create", "split": "train", "weight": 3, "fn": fam_create_day_only},
    {"id": "create_absence", "tool": "calendar_create", "split": "train", "weight": 4, "fn": fam_create_absence},
    {"id": "create_participants", "tool": "calendar_create", "split": "train", "weight": 3, "fn": fam_create_participants},
    {"id": "create_polite", "tool": "calendar_create", "split": "train", "weight": 2, "fn": fam_create_polite},
    {"id": "create_time_val", "tool": "calendar_create", "split": "validation", "weight": 5, "fn": fam_create_time},
    {"id": "create_absence_val", "tool": "calendar_create", "split": "validation", "weight": 3, "fn": fam_create_absence},
    {"id": "create_time_test", "tool": "calendar_create", "split": "test", "weight": 5, "fn": fam_create_time},
    {"id": "create_absence_test", "tool": "calendar_create", "split": "test", "weight": 3, "fn": fam_create_absence},
    {"id": "create_participants_test", "tool": "calendar_create", "split": "test", "weight": 2, "fn": fam_create_participants},
    # calendar_move 20%
    {"id": "move_date", "tool": "calendar_move", "split": "train", "weight": 4, "fn": fam_move_date},
    {"id": "move_time", "tool": "calendar_move", "split": "train", "weight": 3, "fn": fam_move_time},
    {"id": "move_date_time", "tool": "calendar_move", "split": "train", "weight": 3, "fn": fam_move_date_time},
    {"id": "move_date_val", "tool": "calendar_move", "split": "validation", "weight": 5, "fn": fam_move_date},
    {"id": "move_dt_test", "tool": "calendar_move", "split": "test", "weight": 4, "fn": fam_move_date_time},
    {"id": "move_time_test", "tool": "calendar_move", "split": "test", "weight": 2, "fn": fam_move_time},
    # calendar_delete 12%
    {"id": "delete_title", "tool": "calendar_delete", "split": "train", "weight": 4, "fn": fam_delete_title},
    {"id": "delete_title_date", "tool": "calendar_delete", "split": "train", "weight": 4, "fn": fam_delete_title_date},
    {"id": "delete_generic", "tool": "calendar_delete", "split": "train", "weight": 3, "fn": fam_delete_generic},
    {"id": "delete_td_val", "tool": "calendar_delete", "split": "validation", "weight": 5, "fn": fam_delete_title_date},
    {"id": "delete_generic_test", "tool": "calendar_delete", "split": "test", "weight": 3, "fn": fam_delete_generic},
    {"id": "delete_title_test", "tool": "calendar_delete", "split": "test", "weight": 3, "fn": fam_delete_title},
    # calendar_list 18%
    {"id": "list_horizon", "tool": "calendar_list", "split": "train", "weight": 1, "fn": fam_list_horizon},
    {"id": "list_date", "tool": "calendar_list", "split": "train", "weight": 4, "fn": fam_list_date},
    {"id": "list_range", "tool": "calendar_list", "split": "train", "weight": 3, "fn": fam_list_range},
    {"id": "list_person", "tool": "calendar_list", "split": "train", "weight": 3, "fn": fam_list_person},
    {"id": "list_person_range", "tool": "calendar_list", "split": "train", "weight": 3, "fn": fam_list_person_range},
    {"id": "list_question", "tool": "calendar_list", "split": "train", "weight": 1, "fn": fam_list_question},
    {"id": "list_date_val", "tool": "calendar_list", "split": "validation", "weight": 4, "fn": fam_list_date},
    {"id": "list_person_val", "tool": "calendar_list", "split": "validation", "weight": 3, "fn": fam_list_person},
    {"id": "list_person_range_val", "tool": "calendar_list", "split": "validation", "weight": 3, "fn": fam_list_person_range},
    {"id": "list_horizon_test", "tool": "calendar_list", "split": "test", "weight": 2, "fn": fam_list_horizon},
    {"id": "list_range_test", "tool": "calendar_list", "split": "test", "weight": 3, "fn": fam_list_range},
    {"id": "list_person_range_test", "tool": "calendar_list", "split": "test", "weight": 3, "fn": fam_list_person_range},
    {"id": "list_question_test", "tool": "calendar_list", "split": "test", "weight": 3, "fn": fam_list_question},
    # calendar_find_slot 20%
    {"id": "find_persons", "tool": "calendar_find_slot", "split": "train", "weight": 3, "fn": fam_find_persons},
    {"id": "find_duration", "tool": "calendar_find_slot", "split": "train", "weight": 4, "fn": fam_find_duration},
    {"id": "find_daypart", "tool": "calendar_find_slot", "split": "train", "weight": 3, "fn": fam_find_daypart},
    {"id": "find_range", "tool": "calendar_find_slot", "split": "train", "weight": 3, "fn": fam_find_range},
    {"id": "find_duration_val", "tool": "calendar_find_slot", "split": "validation", "weight": 5, "fn": fam_find_duration},
    {"id": "find_persons_test", "tool": "calendar_find_slot", "split": "test", "weight": 3, "fn": fam_find_persons},
    {"id": "find_range_test", "tool": "calendar_find_slot", "split": "test", "weight": 3, "fn": fam_find_range},
    {"id": "find_daypart_test", "tool": "calendar_find_slot", "split": "test", "weight": 2, "fn": fam_find_daypart},
    # negative registry entries (queries come from negative_slice, not fn)
    {"id": "neg_offtopic", "tool": "none", "split": "train", "weight": 0, "fn": None},
    {"id": "neg_adjacent", "tool": "none", "split": "train", "weight": 0, "fn": None},
    {"id": "neg_offtopic_val", "tool": "none", "split": "validation", "weight": 0, "fn": None},
    {"id": "neg_offtopic_test", "tool": "none", "split": "test", "weight": 0, "fn": None},
    {"id": "neg_adjacent_val", "tool": "none", "split": "validation", "weight": 0, "fn": None},
    {"id": "neg_adjacent_test", "tool": "none", "split": "test", "weight": 0, "fn": None},
]

TARGET_RATES = {  # plan §6, adjusted to realizable query spaces (§5/§7):
    # list's combinable space is small (short queries, few slots), so its
    # share sits below the 18% target; create/find/move carry the load.
    "calendar_create": 0.33, "calendar_move": 0.21, "calendar_delete": 0.10,
    "calendar_list": 0.13, "calendar_find_slot": 0.23,
}
NEGATIVE_SHARE = 0.09  # plan §14: 8-10%
