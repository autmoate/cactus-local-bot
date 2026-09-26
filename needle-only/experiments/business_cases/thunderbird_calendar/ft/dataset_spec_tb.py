"""FT dataset spec for the Thunderbird calendar task (FT_PLAN.md §7).

Deterministic, seeded. Families are split-exclusive; validation/test draw from
held-out value pools. Gold is SPARSE: `when` is always the verbatim temporal
span, `title`/`location` only when they literally appear in the model input.
Negatives are the canonical empty call `[]` (never a `no_event` tool).

The model input is rendered by the builder exactly like inference:
  message   -> "Betreff: {subject}\n\n{body}"
  selection -> the selected text only (subject is never given to the model).

Target mix (FT_PLAN.md §7): ~55% positive message, ~22% positive selection,
~18% near-miss negative, ~5% off-topic. Near-miss is the important class.
"""
from __future__ import annotations

import random

SYSTEM_FACTS = "locale: de-DE"
TOOL_NAME = "extract_event"

TARGET = {"message_pos": 0.55, "selection_pos": 0.22,
          "near_miss": 0.18, "offtopic": 0.05}

# ------------------------------------------------------------------- pools
TITLES_TRAIN = ["Projektbesprechung", "Teammeeting", "Workshop", "Jahresgespräch",
                "Mittagessen", "Wochenplanung", "Kickoff", "Quartalsreview",
                "Fahrstunde", "Probe", "Steuerberatung", "Statusrunde"]
TITLES_EVAL = ["Strategieworkshop", "Kundenpräsentation", "Klettertraining",
               "Elternabend", "Augenarzt", "Onboarding", "Sprintplanung",
               "Jahresabschluss"]
PERSONS_TRAIN = ["Lisa", "Max", "Anna", "Tim", "Jana", "Paul", "Lena", "Jonas"]
PERSONS_EVAL = ["Miriam", "Felix", "Nora", "Tom", "Emma", "Clara"]
ROOMS_TRAIN = ["Raum 204", "Konferenzraum Berlin", "Besprechungsraum A",
               "Halle 2", "Studio 1"]
ROOMS_EVAL = ["Raum 3.14", "Konferenzraum Hamburg", "Seminarraum 5", "Lodge"]
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]
TZS = ["CET", "CEST", "GMT", "UTC", "BST", "EST", "PT", "Pacific"]
DAY_NUM_TRAIN = [2, 3, 5, 7, 9, 11, 14, 16, 18, 21, 23, 24, 27, 28]
DAY_NUM_EVAL = [1, 4, 6, 8, 12, 13, 15, 17, 19, 22, 25, 26]
TIMES_TRAIN = ["9", "10", "11", "13", "14", "15", "16", "17"]
TIMES_EVAL = ["8:30", "9:30", "12:00", "12:30", "13:30", "16:30", "18:00"]

EVENT_PHRASES = ["{title} {when}", "Der Termin {title} ist {when}",
                 "{title}: {when}", "Wir treffen uns {when}",
                 "Der {title} findet {when} statt"]
GREET = ["Hallo", "Hi", "Guten Tag", "Hallo zusammen", "Servus", ""]
SIGN = ["Viele Grüße", "Beste Grüße", "Liebe Grüße", "LG", "Gruß"]

OFFTOPIC = ["Wie wird das Wetter morgen?", "Erzähl mir einen Witz.",
            "Was kostet eine Briefmarke?", "Wie spät ist es in Tokio?",
            "Hast du ein Rezept für Pfannkuchen?", "Wer hat das Telefon erfunden?",
            "What's the weather like tomorrow?", "Recommend a good book."]


def draw(rng: random.Random, pool: list, k: int = 1):
    return rng.sample(pool, k)


def _day(rng, split):
    return rng.choice(DAY_NUM_TRAIN if split == "train" else DAY_NUM_EVAL)


def _time(rng, split):
    return rng.choice(TIMES_TRAIN if split == "train" else TIMES_EVAL)


def _title(rng, split):
    return rng.choice(TITLES_TRAIN if split == "train" else TITLES_EVAL)


def _person(rng, split):
    return rng.choice(PERSONS_TRAIN if split == "train" else PERSONS_EVAL)


def _room(rng, split):
    return rng.choice(ROOMS_TRAIN if split == "train" else ROOMS_EVAL)


def _env(rng, split, when, *, title="", location="", subject=None,
         body=None, selection=None, review=False, temporal_class="explicit",
         language="de"):
    """Assemble a case. `when` must be a verbatim substring of the model input."""
    if body is None:
        parts = []
        g = rng.choice(GREET)
        if g:
            parts.append(g + ("," if rng.random() < 0.5 else " ") + ("\n\n" if rng.random() < 0.6 else " "))
        phrase = rng.choice(EVENT_PHRASES).format(title=title or "Termin", when=when)
        if location:
            phrase += f" {location}"
        parts.append(phrase.rstrip(".") + rng.choice([".", "!", ""]))
        if rng.random() < 0.5:
            parts.append("\n\n" + rng.choice(SIGN) + "\n" + _person(rng, split))
        body = "".join(parts) if parts and parts[0].endswith("\n\n") else " ".join(p.strip() for p in parts)
    if subject is None:
        subject = (title or rng.choice(["Termin", "Kurze Info", "Planung"]))
        if rng.random() < 0.25:
            subject = rng.choice(["Re: ", "AW: "]) + subject
    return {"subject": subject, "body": body, "selection": selection,
            "when": when, "title": title, "location": location,
            "event": True, "review": review, "temporal_class": temporal_class,
            "language": language}


# ------------------------------------------------------------ positive fams
def fam_explicit_datetime(rng, split):
    d, t = _day(rng, split), _time(rng, split)
    when = rng.choice([f"am {d}.10. um {t} Uhr", f"am {d}.11. um {t} Uhr",
                       f"am {d}.{rng.choice([9,10,11])}. um {t} Uhr"])
    return _env(rng, split, when, title=_title(rng, split), temporal_class="explicit_dmy")


def fam_explicit_month(rng, split):
    d, t = _day(rng, split), _time(rng, split)
    m = rng.choice(MONTHS)
    when = f"am {d}. {m} um {t} Uhr"
    return _env(rng, split, when, title=_title(rng, split),
                temporal_class="explicit_month")


def fam_range(rng, split):
    d = _day(rng, split)
    t1, t2 = sorted(draw(rng, ["9", "10", "11", "13", "14", "15", "16"], 2),
                    key=lambda x: int(x))
    when = f"am {d}.10. von {t1} bis {t2} Uhr"
    return _env(rng, split, when, title=_title(rng, split),
                temporal_class="time_range")


def fam_date_only(rng, split):
    d = _day(rng, split)
    when = f"am {d}. {rng.choice(MONTHS)}"
    return _env(rng, split, when, title=_title(rng, split),
                temporal_class="date_only")


def fam_multi_day(rng, split):
    d1 = _day(rng, split)
    d2 = min(d1 + rng.choice([1, 2]), 28)
    m = rng.choice(MONTHS)
    when = f"am {d1}. und {d2}. {m}"
    return _env(rng, split, when, title=_title(rng, split),
                temporal_class="multi_day")


def fam_relative(rng, split):
    t = _time(rng, split)
    when = rng.choice([f"morgen um {t} Uhr", f"übermorgen um {t} Uhr",
                       f"nächsten {rng.choice(WEEKDAYS)} um {t} Uhr",
                       f"kommenden {rng.choice(WEEKDAYS)} um {t} Uhr"])
    return _env(rng, split, when, title=_title(rng, split),
                temporal_class="relative")


def fam_location(rng, split):
    d, t = _day(rng, split), _time(rng, split)
    loc = _room(rng, split) if rng.random() < 0.8 else "online via Meet"
    when = f"am {d}.10. um {t} Uhr"
    return _env(rng, split, when, title=_title(rng, split), location=loc,
                temporal_class="explicit_dmy")


def fam_timezone(rng, split):
    tz = rng.choice(TZS)
    when = rng.choice([f"Thursday at 3pm {tz}", f"Friday at 10am {tz}",
                       f"Monday at 9:00 {tz}", f"Tuesday at 14:00 {tz}"])
    return _env(rng, split, when, title=_title(rng, split), review=True,
                temporal_class="timezone", language="en")


def fam_fuzzy(rng, split):
    d = _day(rng, split)
    when = rng.choice([f"am {d}.10. gegen 14 Uhr",
                       f"am {d}.10. zwischen 14 und 15 Uhr",
                       f"am {d}.10. ca. 10 Uhr",
                       f"am {d}.10. nach dem Mittagessen",
                       "morgen Vormittag"])
    return _env(rng, split, when, title=_title(rng, split), review=True,
                temporal_class="fuzzy")


def fam_english(rng, split):
    d = _day(rng, split)
    when = f"on October {d} at 2pm"
    return _env(rng, split, when, title=_title(rng, split),
                temporal_class="explicit_month", language="en")


def fam_subject_fallback(rng, split):
    t = _time(rng, split)
    when = rng.choice([f"Dienstag {t} Uhr", f"morgen um {t} Uhr",
                       f"am {_day(rng, split)}.10. um {t} Uhr"])
    body = rng.choice([f"{when} passt mir gut.", f"Einverstanden, {when}.",
                       f"Bleibt es bei {when}?"])
    return _env(rng, split, when, title="", subject=_title(rng, split), body=body,
                temporal_class="verbatim_short")


def fam_reply_quote(rng, split):
    d, t = _day(rng, split), _time(rng, split)
    when = f"am {d}.10. um {t} Uhr"
    old1 = f"am {_day(rng, split)}.9. um 10 Uhr"
    old2 = f"am {_day(rng, split)}.9. um 14 Uhr"
    body = (f"Kurz: wir nehmen {when}.\n\nAm 24.09. schrieb {_person(rng, split)}:\n"
            f"> Wie wäre {old1} oder {old2}?\nAm 23.09. schrieb "
            f"{_person(rng, split)}:\n> Passt {when}?")
    return _env(rng, split, when, title=_title(rng, split), body=body,
                temporal_class="explicit_dmy")


def fam_long_signature(rng, split):
    d, t = _day(rng, split), _time(rng, split)
    when = f"am {d}.10. um {t} Uhr"
    person = _person(rng, split)
    body = (f"Hallo,\n\nwie besprochen {when}.\n\n{rng.choice(SIGN)}\n"
            f"{person}\n\n--\n{person} Beispiel\nMusterstr. 1\n"
            f"Tel: +49 123 456\nÖffnungszeiten: Mo 8-12")
    return _env(rng, split, when, title=_title(rng, split), body=body,
                temporal_class="explicit_dmy")


# ------------------------------------------------------------- selection fam
def _sel_span(rng, split) -> tuple[str, str, str]:
    """(when, explicit-title, explicit-location) for a selection sentence."""
    kind = rng.choice(["dt", "month", "range", "relative", "date_only",
                       "with_title", "with_location"])
    d, t = _day(rng, split), _time(rng, split)
    if kind == "dt":
        return f"am {d}.10. um {t} Uhr", "", ""
    if kind == "month":
        return f"am {d}. {rng.choice(MONTHS)} um {t} Uhr", "", ""
    if kind == "range":
        t1, t2 = sorted(draw(rng, ["9", "10", "11", "13", "14", "15", "16"], 2),
                        key=lambda x: int(x))
        return f"am {d}.10. von {t1} bis {t2} Uhr", "", ""
    if kind == "relative":
        return rng.choice([f"morgen um {t} Uhr", f"übermorgen um {t} Uhr",
                           f"nächsten {rng.choice(WEEKDAYS)} um {t} Uhr"]), "", ""
    if kind == "date_only":
        return f"am {d}. {rng.choice(MONTHS)}", "", ""
    if kind == "with_location":
        return f"am {d}.10. um {t} Uhr", "", _room(rng, split)
    return f"am {d}.10. um {t} Uhr", _title(rng, split), ""


def fam_selection(rng, split):
    when, title, loc = _sel_span(rng, split)
    frames = ["{w} passt mir.", "Einverstanden, {w}.", "Bleibt es bei {w}?",
              "Für mich gern {w}.", "{w} geht bei mir.", "Dann {w}.",
              "Notiert: {w}.", "{w} nehmen wir."]
    sentence = rng.choice(frames).format(w=when)
    if title:
        sentence = f"{title} {when}."
    if loc:
        sentence = f"{when} in {loc}."
    return {"subject": _title(rng, split), "body": "", "selection": sentence,
            "when": when, "title": title, "location": loc, "event": True,
            "review": False, "temporal_class": "selection",
            "language": "de"}


# ------------------------------------------------------------- near-miss negs
def _neg(rng, split, body, subject=None, temporal_class="near_miss"):
    prefix = rng.choice(["", "", "Hallo, ", "Hi, ", "Kurz: ", "Sag mal, ",
                         "FYI: ", "Hmm, ", "Guten Tag, "])
    suffix = rng.choice(["", "", " Danke.", " Viele Grüße", " LG",
                         " Bis später.", " Schönen Tag noch."])
    if prefix and body[:1].isupper():
        body = prefix + body[0].lower() + body[1:]
    else:
        body = prefix + body
    body = body + suffix
    subjects = [subject or "", "Kurze Frage", "Info", "Kurz", "Termin?",
                "Planung", "Rückfrage"]
    subj = rng.choice([s for s in subjects if s]) + rng.choice(
        ["", "", " (bitte lesen)", " - wichtig"])
    if rng.random() < 0.3:
        subj = rng.choice(["Re: ", "AW: "]) + subj
    return {"subject": subj, "body": body, "selection": None,
            "when": "", "title": "", "location": "", "event": False,
            "review": False, "temporal_class": temporal_class,
            "language": "de"}


def fam_tentative_single(rng, split):
    d, t = _day(rng, split), _time(rng, split)
    return _neg(rng, split, f"Passt dir am {d}.10. um {t} Uhr?", "Termin?")


def fam_tentative_multi(rng, split):
    d1, d2 = draw(rng, DAY_NUM_TRAIN if split == "train" else DAY_NUM_EVAL, 2)
    return _neg(rng, split, f"Passt dir am {d1}.10. um 14 Uhr oder am {d2}.10. "
                            f"um 10 Uhr?", "Terminabstimmung")


def fam_tentative_options(rng, split):
    w1, w2 = draw(rng, WEEKDAYS, 2)
    return _neg(rng, split, f"Ich könnte {w1} oder {w2} vormittags.", "Wann?")


def fam_offer_slot(rng, split):
    d, t = _day(rng, split), _time(rng, split)
    return _neg(rng, split, f"Am {d}.10. um {t} Uhr ist bei mir frei.",
                "Sprechstunde")


def fam_cancel(rng, split):
    w = rng.choice(WEEKDAYS)
    return _neg(rng, split, f"Der {_title(rng, split)} am {w} fällt leider aus.",
                "Absage")


def fam_reschedule(rng, split):
    w1, w2 = draw(rng, WEEKDAYS, 2)
    t = _time(rng, split)
    return _neg(rng, split, f"Wir verschieben {_title(rng, split)} von {w1} auf "
                            f"{w2} um {t} Uhr.", "Verschiebung")


def fam_deadline(rng, split):
    d = _day(rng, split)
    return _neg(rng, split, f"Bitte schick die Unterlagen bis zum {d}.10.",
                "Deadline")


def fam_past_reference(rng, split):
    d = _day(rng, split)
    return _neg(rng, split, f"Das {_title(rng, split)} vom {d}.9. war sehr "
                            f"produktiv.", "Bericht")


def fam_quoted_old(rng, split):
    old = f"am {_day(rng, split)}.9. um 10 Uhr"
    return _neg(rng, split,
                f"Danke für die Rückmeldung, ich melde mich.\n\nAm 20.09. schrieb "
                f"{_person(rng, split)}:\n> Treffen wir uns {old}?\n"
                f"> Oder {old}?", "Re: Termin")


def fam_offtopic(rng, split):
    return _neg(rng, split, rng.choice(OFFTOPIC), "Hallo",
                temporal_class="offtopic")


FAMILIES = {
    "message_pos": [
        (fam_explicit_datetime, 18), (fam_explicit_month, 10), (fam_range, 12),
        (fam_date_only, 8), (fam_multi_day, 5), (fam_relative, 12),
        (fam_location, 8), (fam_timezone, 6), (fam_fuzzy, 6),
        (fam_english, 5), (fam_subject_fallback, 8), (fam_reply_quote, 12),
        (fam_long_signature, 8),
    ],
    "selection_pos": [(fam_selection, 10)],
    "near_miss": [
        (fam_tentative_single, 14), (fam_tentative_multi, 10),
        (fam_tentative_options, 8), (fam_offer_slot, 8), (fam_cancel, 12),
        (fam_reschedule, 10), (fam_deadline, 10), (fam_past_reference, 10),
        (fam_quoted_old, 12),
    ],
    "offtopic": [(fam_offtopic, 1)],
}
