#!/usr/bin/env python3
"""Hand-authored realism challenge set for the Thunderbird calendar FT.

NOT generated from dataset templates: every mail below is written by hand and
frozen. This builder only (a) resolves each gold temporal span to concrete
start/end via the production compiler and (b) validates that the gold span,
title and location are verbatim substrings of the mail (or the selection). It
never invents content.

Output: ft/realism_challenge.jsonl — eval-compatible with ../eval.py.

Classification policy (FT_PLAN.md §1):
  event_count = 1  only for a concrete new event that is already agreed,
                   confirmed or announced.
  event_count = 0  tentative scheduling, cancel/reschedule, deadline, past
                   references, unrelated mail.

Run:  cd needle-only && uv run python \
        experiments/business_cases/thunderbird_calendar/ft/build_realism.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPIKE = HERE.parent
sys.path.insert(0, str(SPIKE))

from temporal import compile_when  # noqa: E402

TZ_RE = re.compile(
    r"\b(CET|CEST|ET|EST|EDT|BST|GMT|UTC|PT|PST|PDT|Pacific|"
    r"Europe/Berlin|New York|London)\b", re.IGNORECASE)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u202f", " ")).strip()


def c(cid, cat, subj, frm, to, recv, body, *, title="", span="", loc="",
      count=1, sel=None, stitle=None, sspan=None, sloc=None,
      incomplete=False):
    """One hand-authored case. `stitle/sspan/sloc` default to the whole-mail
    gold for the selection variant when `sel` is given. `incomplete=True` marks
    a temporal phrase the compiler deliberately does not resolve (e.g. a
    timezone or fuzzy time) -> human review routing, not a hard failure."""
    return {
        "id": cid, "category": cat,
        "message": {"subject": subj, "from": [frm], "to": list(to),
                    "cc": [], "received_at": recv, "body": body},
        "selection": sel,
        "incomplete": incomplete,
        "gold": {"event_count": count, "title": title, "span": span,
                 "location": loc},
        "selection_gold": None if sel is None else {
            "event_count": count,
            "title": title if stitle is None else stitle,
            "span": span if sspan is None else sspan,
            "location": loc if sloc is None else sloc},
    }


ME = "Ich <me@example.org>"
LISA = "Lisa <lisa@example.org>"
MAX = "Max <max@example.org>"
ANNA = "Anna <anna@example.org>"

CASES = [
    # ---------------------------------------------------- informal_confirm (25)
    c("r001", "informal_confirm", "Projekt Alpha", LISA, [ME], "2026-09-25T10:12:00",
      "Dienstag um 14 Uhr passt mir gut. Bis dann!", span="Dienstag um 14 Uhr",
      title="Projekt Alpha", sel="Dienstag um 14 Uhr passt mir gut."),
    c("r002", "informal_confirm", "Mittagessen", MAX, [ME, ANNA], "2026-09-25T11:40:00",
      "Donnerstag 12:30 Uhr klingt super. Treffen wir uns vor dem Cafe.",
      span="Donnerstag 12:30 Uhr", title="Mittagessen",
      sel="Donnerstag 12:30 Uhr klingt super."),
    c("r003", "informal_confirm", "Wochenplanung", ANNA, [ME], "2026-09-25T09:05:00",
      "Freitag um 9 Uhr geht klar. Ich schicke vorher die Agenda.",
      span="Freitag um 9 Uhr", title="Wochenplanung", sel="Freitag um 9 Uhr geht klar."),
    c("r004", "informal_confirm", "Kaffee mit Jonas", LISA, [ME], "2026-09-25T15:22:00",
      "Lass uns Mittwoch 15 Uhr im Lesecafe treffen.", span="Mittwoch 15 Uhr",
      title="Kaffee mit Jonas", loc="Lesecafe", sel="Mittwoch 15 Uhr im Lesecafe"),
    c("r005", "informal_confirm", "Team-Sync", MAX, [ME], "2026-09-25T08:30:00",
      "Nächsten Montag 10 Uhr passt. Wir bleiben beim üblichen Kanal.",
      span="Nächsten Montag 10 Uhr", title="Team-Sync",
      sel="Nächsten Montag 10 Uhr passt."),
    c("r006", "informal_confirm", "Laufrunde", ANNA, [ME], "2026-09-25T18:00:00",
      "Samstag 8 Uhr am Stadtpark? Ich bin dabei.", span="Samstag 8 Uhr",
      title="Laufrunde", loc="Stadtpark", sel="Samstag 8 Uhr am Stadtpark"),
    c("r007", "informal_confirm", "Buchclub", LISA, [ME], "2026-09-25T20:10:00",
      "Dienstag 19:30 Uhr bei mir. Freu mich!", span="Dienstag 19:30 Uhr",
      title="Buchclub", sel="Dienstag 19:30 Uhr bei mir."),
    c("r008", "informal_confirm", "Steuerunterlagen", MAX, [ME], "2026-09-25T13:00:00",
      "Dann machen wir es am 12.10. um 11 Uhr fertig.", span="12.10. um 11 Uhr",
      title="Steuerunterlagen", sel="am 12.10. um 11 Uhr"),
    c("r009", "informal_confirm", "Elternabend", ANNA, [ME], "2026-09-25T17:45:00",
      "Der Elternabend ist am 5. November um 18:30 Uhr in der Aula.",
      span="5. November um 18:30 Uhr", title="Elternabend", loc="Aula",
      sel="Der Elternabend ist am 5. November um 18:30 Uhr"),
    c("r010", "informal_confirm", "Zahnarzt", LISA, [ME], "2026-09-25T07:50:00",
      "Ihr Termin ist am 12. Oktober um 9:30 Uhr.", span="12. Oktober um 9:30 Uhr",
      title="Zahnarzt", sel="am 12. Oktober um 9:30 Uhr"),
    c("r011", "informal_confirm", "Sportkurs", MAX, [ME], "2026-09-25T16:00:00",
      "Kommenden Freitag 17 Uhr geht es los, Halle 2.", span="Kommenden Freitag 17 Uhr",
      title="Sportkurs", loc="Halle 2", sel="Kommenden Freitag 17 Uhr"),
    c("r012", "informal_confirm", "Wochenendausflug", ANNA, [ME], "2026-09-25T12:30:00",
      "Wir starten am 3.10. um 8 Uhr vor dem Bahnhof.", span="3.10. um 8 Uhr",
      title="Wochenendausflug", loc="Bahnhof", sel="am 3.10. um 8 Uhr"),
    c("r013", "informal_confirm", "Geburtstag Tim", LISA, [ME], "2026-09-25T19:00:00",
      "Tim feiert am 18.10. ab 19 Uhr. Sei dabei!", span="18.10. ab 19 Uhr",
      title="Geburtstag Tim", sel="am 18.10. ab 19 Uhr"),
    c("r014", "informal_confirm", "Fahrgemeinschaft", MAX, [ME], "2026-09-25T06:40:00",
      "Hol mich Mittwoch 7:15 Uhr ab, wie immer.", span="Mittwoch 7:15 Uhr",
      title="Fahrgemeinschaft", sel="Mittwoch 7:15 Uhr"),
    c("r015", "informal_confirm", "Kino", ANNA, [ME], "2026-09-25T21:00:00",
      "Samstag 20 Uhr im CineStar? Ich reserviere.", span="Samstag 20 Uhr",
      title="Kino", loc="CineStar", sel="Samstag 20 Uhr im CineStar"),
    c("r016", "informal_confirm", "Probe", LISA, [ME], "2026-09-25T14:20:00",
      "Die Chorprobe ist diesen Donnerstag um 18 Uhr.", span="diesen Donnerstag um 18 Uhr",
      title="Chorprobe", sel="diesen Donnerstag um 18 Uhr"),
    # Request, not an agreed event -> no create (semantic boundary, review).
    c("r017", "tentative_slots", "Rückruf", MAX, [ME], "2026-09-25T10:55:00",
      "Passt dir morgen 11 Uhr für ein kurzes Telefonat?", count=0),
    c("r018", "informal_confirm", "Umzug", ANNA, [ME], "2026-09-25T15:35:00",
      "Wir packen am 24.10. ab 10 Uhr. Helfer willkommen.", span="24.10. ab 10 Uhr",
      title="Umzug", sel="am 24.10. ab 10 Uhr"),
    c("r019", "informal_confirm", "Fortbildung", LISA, [ME], "2026-09-25T11:10:00",
      "Die Fortbildung läuft am 7. und 8. Oktober.", span="7. und 8. Oktober",
      title="Fortbildung", sel="am 7. und 8. Oktober"),
    c("r020", "informal_confirm", "Abendessen", MAX, [ME], "2026-09-25T18:25:00",
      "Freitag 19 Uhr beim Italiener passt. Tisch ist reserviert.",
      span="Freitag 19 Uhr", title="Abendessen", loc="Italiener",
      sel="Freitag 19 Uhr beim Italiener"),
    c("r021", "informal_confirm", "Prüfung", ANNA, [ME], "2026-09-25T09:45:00",
      "Die Prüfung ist am 14.12. um 10 Uhr in Raum 204.", span="14.12. um 10 Uhr",
      title="Prüfung", loc="Raum 204", sel="14.12. um 10 Uhr in Raum 204"),
    c("r022", "informal_confirm", "Gartenaktion", LISA, [ME], "2026-09-25T08:15:00",
      "Sonntag 10 Uhr geht es los. Bring Handschuhe mit.", span="Sonntag 10 Uhr",
      title="Gartenaktion", sel="Sonntag 10 Uhr"),
    c("r023", "informal_confirm", "Interview", MAX, [ME], "2026-09-25T13:50:00",
      "Das Interview ist übermorgen um 13 Uhr online.", span="übermorgen um 13 Uhr",
      title="Interview", sel="übermorgen um 13 Uhr"),
    c("r024", "informal_confirm", "Wartungstermin", ANNA, [ME], "2026-09-25T07:30:00",
      "Der Techniker kommt am 9.10. von 8 bis 10 Uhr.", span="am 9.10. von 8 bis 10 Uhr",
      title="Wartungstermin", sel="am 9.10. von 8 bis 10 Uhr"),
    c("r025", "informal_confirm", "Spieleabend", LISA, [ME], "2026-09-25T20:40:00",
      "Nächsten Samstag 18 Uhr Spieleabend bei Max.", span="Nächsten Samstag 18 Uhr",
      title="Spieleabend", sel="Nächsten Samstag 18 Uhr"),

    # ----------------------------------------------------- formal_confirm (20)
    c("r026", "formal_confirm", "Terminbestätigung Praxis Dr. Weber", "praxis@weber.example",
      [ME], "2026-09-25T08:00:00", "Ihr Termin ist am 12.10. um 9 Uhr.",
      span="am 12.10. um 9 Uhr", title="Terminbestätigung Praxis Dr. Weber",
      sel="Ihr Termin ist am 12.10. um 9 Uhr."),
    c("r027", "formal_confirm", "Bestätigung: Projektbesprechung", "office@firma.example",
      [ME], "2026-09-25T09:15:00",
      "Hiermit bestätigen wir die Projektbesprechung am 15.10.2026 um 10 Uhr.",
      span="am 15.10.2026 um 10 Uhr", title="Projektbesprechung"),
    c("r028", "formal_confirm", "Ihr Beratungstermin", "beratung@kanzlei.example",
      [ME], "2026-09-25T10:30:00",
      "Ihr Beratungstermin findet am 4. November um 14:30 Uhr statt.",
      span="am 4. November um 14:30 Uhr", title="Ihr Beratungstermin"),
    c("r029", "formal_confirm", "Terminbuchung bestätigt", "noreply@fitness.example",
      [ME], "2026-09-25T06:45:00",
      "Dein Kurs beginnt am 6.10. um 18 Uhr in Studio 1.", span="am 6.10. um 18 Uhr",
      title="Terminbuchung bestätigt", loc="Studio 1"),
    c("r030", "formal_confirm", "Ihre Reservierung", "reservierung@restaurant.example",
      [ME], "2026-09-25T12:00:00",
      "Wir bestätigen Ihren Tisch am 11.10. um 19:30 Uhr.", span="am 11.10. um 19:30 Uhr",
      title="Ihre Reservierung"),
    c("r031", "formal_confirm", "Augenarzt Bestätigung", "praxis-augen@example.org",
      [ME], "2026-09-25T07:20:00",
      "Ihr Kontrolltermin ist am 21.10. um 8:15 Uhr.", span="am 21.10. um 8:15 Uhr",
      title="Augenarzt Bestätigung"),
    c("r032", "formal_confirm", "Terminbestätigung Kfz-Werkstatt", "werkstatt@auto.example",
      [ME], "2026-09-25T11:05:00",
      "Ihr Fahrzeug ist für den 13.10. um 7:30 Uhr eingeplant.", span="den 13.10. um 7:30 Uhr",
      title="Terminbestätigung Kfz-Werkstatt"),
    c("r033", "formal_confirm", "Onboarding-Termin bestätigt", "hr@firma.example",
      [ME], "2026-09-25T13:10:00",
      "Dein Onboarding findet am 1.10. um 9 Uhr statt.", span="am 1.10. um 9 Uhr",
      title="Onboarding-Termin bestätigt"),
    c("r034", "formal_confirm", "Bestätigung: Webinar", "events@schulung.example",
      [ME], "2026-09-25T14:00:00",
      "Das Webinar ist am 20.10. von 15 bis 16:30 Uhr.", span="am 20.10. von 15 bis 16:30 Uhr",
      title="Webinar"),
    c("r035", "formal_confirm", "Terminbestätigung Notariat", "notar@kanzlei.example",
      [ME], "2026-09-25T15:15:00",
      "Wir bestätigen den Termin am 27.10. um 11 Uhr in Zimmer 12.",
      span="am 27.10. um 11 Uhr", title="Terminbestätigung Notariat", loc="Zimmer 12"),
    c("r036", "formal_confirm", "Ihr Termin im Center", "info@center.example",
      [ME], "2026-09-25T09:50:00",
      "Ihr Termin ist am 8.10. um 16 Uhr.", span="am 8.10. um 16 Uhr",
      title="Ihr Termin im Center"),
    c("r037", "formal_confirm", "Bestätigung Steuerberatung", "steuer@kanzlei.example",
      [ME], "2026-09-25T10:05:00",
      "Unser Gespräch findet am 22.10. um 9:30 Uhr statt.", span="am 22.10. um 9:30 Uhr",
      title="Bestätigung Steuerberatung"),
    c("r038", "formal_confirm", "Terminbestätigung Sozialamt", "post@sozialamt.example",
      [ME], "2026-09-25T08:40:00",
      "Ihr Vorsprachetermin ist am 5.11. um 10:15 Uhr.", span="am 5.11. um 10:15 Uhr",
      title="Terminbestätigung Sozialamt"),
    c("r039", "formal_confirm", "Reha-Termin", "reha@klinik.example",
      [ME], "2026-09-25T07:10:00",
      "Ihr Reha-Termin wurde auf den 30.10. um 13 Uhr gelegt.", span="den 30.10. um 13 Uhr",
      title="Reha-Termin"),
    c("r040", "formal_confirm", "Bestätigung: Elterngespräch", "schule@example.org",
      [ME], "2026-09-25T16:20:00",
      "Das Elterngespräch ist am 3.11. um 17 Uhr.", span="am 3.11. um 17 Uhr",
      title="Elterngespräch"),
    c("r041", "formal_confirm", "Terminbestätigung Fahrschule", "fahrschule@example.org",
      [ME], "2026-09-25T12:45:00",
      "Deine Fahrstunde ist am 9.10. um 14 Uhr.", span="am 9.10. um 14 Uhr",
      title="Fahrstunde"),
    c("r042", "formal_confirm", "Ihr Rückruftermin", "service@telecom.example",
      [ME], "2026-09-25T13:35:00",
      "Wir rufen Sie am 10.10. um 11:30 Uhr an.", span="am 10.10. um 11:30 Uhr",
      title="Ihr Rückruftermin"),
    c("r043", "formal_confirm", "Terminbestätigung Versicherung", "schaden@versicherung.example",
      [ME], "2026-09-25T09:25:00",
      "Der Ortstermin ist am 16.10. um 10 Uhr.", span="am 16.10. um 10 Uhr",
      title="Terminbestätigung Versicherung"),
    c("r044", "formal_confirm", "Impfung bestätigt", "praxis@impf.example",
      [ME], "2026-09-25T08:05:00",
      "Ihr Impftermin ist am 19.10. um 15:45 Uhr.", span="am 19.10. um 15:45 Uhr",
      title="Impfung bestätigt"),
    c("r045", "formal_confirm", "Bestätigung: Vorstellungsgespräch", "jobs@firma.example",
      [ME], "2026-09-25T11:55:00",
      "Ihr Vorstellungsgespräch ist am 23.10. um 13:30 Uhr.", span="am 23.10. um 13:30 Uhr",
      title="Vorstellungsgespräch"),

    # ---------------------------------------------------------- announcement (15)
    c("r046", "announcement", "Workshop Design Thinking", "events@firma.example",
      [ME, MAX], "2026-09-25T09:00:00",
      "Der Workshop findet am 8.11. von 10 bis 16 Uhr im Konferenzraum Berlin statt.",
      span="am 8.11. von 10 bis 16 Uhr", title="Workshop Design Thinking",
      loc="Konferenzraum Berlin"),
    c("r047", "announcement", "Firmenlauf", "sport@firma.example", [ME],
      "2026-09-25T10:00:00",
      "Der Firmenlauf startet am 4.10. um 9 Uhr am Stadtpark.", span="am 4.10. um 9 Uhr",
      title="Firmenlauf", loc="Stadtpark"),
    c("r048", "announcement", "Jahresabschluss-Feier", "hr@firma.example", [ME],
      "2026-09-25T11:00:00",
      "Die Jahresabschluss-Feier ist am 15.12. ab 18 Uhr im Saal.", span="am 15.12. ab 18 Uhr",
      title="Jahresabschluss-Feier", loc="Saal"),
    c("r049", "announcement", "Schulkonzert", "schule@example.org", [ME],
      "2026-09-25T12:00:00",
      "Das Schulkonzert findet am 28.11. um 17 Uhr in der Aula statt.",
      span="am 28.11. um 17 Uhr", title="Schulkonzert", loc="Aula"),
    c("r050", "announcement", "Vortrag KI", "info@uni.example", [ME],
      "2026-09-25T13:00:00",
      "Der Vortrag ist am 7.10. um 18 Uhr online.", span="am 7.10. um 18 Uhr",
      title="Vortrag KI", loc="online"),
    c("r051", "announcement", "Flohmarkt", "verein@example.org", [ME],
      "2026-09-25T14:00:00",
      "Der Flohmarkt öffnet am 3.10. um 8 Uhr auf dem Marktplatz.", span="am 3.10. um 8 Uhr",
      title="Flohmarkt", loc="Marktplatz"),
    c("r052", "announcement", "Konzert", "tickets@example.org", [ME],
      "2026-09-25T15:00:00",
      "Das Konzert beginnt am 12.11. um 20 Uhr in der Arena.", span="am 12.11. um 20 Uhr",
      title="Konzert", loc="Arena"),
    c("r053", "announcement", "Tag der offenen Tür", "schule@example.org", [ME],
      "2026-09-25T16:00:00",
      "Der Tag der offenen Tür ist am 14.11. von 10 bis 14 Uhr.", span="am 14.11. von 10 bis 14 Uhr",
      title="Tag der offenen Tür"),
    c("r054", "announcement", "Mitgliederversammlung", "verein@example.org", [ME],
      "2026-09-25T17:00:00",
      "Die Mitgliederversammlung ist am 25.10. um 19 Uhr im Vereinsheim.",
      span="am 25.10. um 19 Uhr", title="Mitgliederversammlung", loc="Vereinsheim"),
    c("r055", "announcement", "Sommerfest", "hr@firma.example", [ME],
      "2026-09-25T18:00:00",
      "Das Sommerfest findet am 6.9. statt.", span="am 6.9.", title="Sommerfest"),
    c("r056", "announcement", "Hackathon", "community@example.org", [ME],
      "2026-09-25T19:00:00",
      "Der Hackathon läuft am 17. und 18. Oktober im Campus.", span="am 17. und 18. Oktober",
      title="Hackathon", loc="Campus"),
    c("r057", "announcement", "Theateraufführung", "theater@example.org", [ME],
      "2026-09-25T20:00:00",
      "Die Aufführung ist am 9.12. um 19:30 Uhr im Theater am Ring.",
      span="am 9.12. um 19:30 Uhr", title="Theateraufführung", loc="Theater am Ring"),
    c("r058", "announcement", "Erste-Hilfe-Kurs", "drk@example.org", [ME],
      "2026-09-25T07:00:00",
      "Der Erste-Hilfe-Kurs ist am 10.10. von 9 bis 17 Uhr.", span="am 10.10. von 9 bis 17 Uhr",
      title="Erste-Hilfe-Kurs"),
    c("r059", "announcement", "Ausstellung", "museum@example.org", [ME],
      "2026-09-25T08:00:00",
      "Die Ausstellung eröffnet am 11.10. um 18 Uhr im Museum.", span="am 11.10. um 18 Uhr",
      title="Ausstellung", loc="Museum"),
    c("r060", "announcement", "Marathon", "sport@verein.example", [ME],
      "2026-09-25T09:00:00",
      "Der Marathon startet am 5.10. um 9 Uhr am Rathaus.", span="am 5.10. um 9 Uhr",
      title="Marathon", loc="Rathaus"),

    # ----------------------------------------------------------- short_accept (15)
    c("r061", "short_accept", "Re: Terminvorschlag", MAX, [ME, LISA], "2026-09-25T10:00:00",
      "Dienstag 14 Uhr passt mir.", span="Dienstag 14 Uhr", title="Terminvorschlag",
      sel="Dienstag 14 Uhr passt mir."),
    c("r062", "short_accept", "Re: Mittag", LISA, [ME], "2026-09-25T10:05:00",
      "Mittwoch 12 Uhr ist gut.", span="Mittwoch 12 Uhr", title="Mittag",
      sel="Mittwoch 12 Uhr ist gut."),
    c("r063", "short_accept", "Re: Abstimmung", ANNA, [ME], "2026-09-25T10:10:00",
      "Mir passt Donnerstag 9:30 Uhr.", span="Donnerstag 9:30 Uhr", title="Abstimmung",
      sel="Mir passt Donnerstag 9:30 Uhr."),
    c("r064", "short_accept", "Re: Wann?", MAX, [ME], "2026-09-25T10:15:00",
      "Freitag 15 Uhr ist okay.", span="Freitag 15 Uhr", title="Wann?",
      sel="Freitag 15 Uhr ist okay."),
    c("r065", "short_accept", "Re: Doodle", LISA, [ME], "2026-09-25T10:20:00",
      "Nächsten Dienstag 11 Uhr geht bei mir.", span="Nächsten Dienstag 11 Uhr",
      title="Doodle", sel="Nächsten Dienstag 11 Uhr geht bei mir."),
    c("r066", "short_accept", "Re: Planung", ANNA, [ME], "2026-09-25T10:25:00",
      "Montag 8 Uhr nehme ich.", span="Montag 8 Uhr", title="Planung",
      sel="Montag 8 Uhr nehme ich."),
    c("r067", "short_accept", "Re: Interview", MAX, [ME], "2026-09-25T10:30:00",
      "Dann 12 Uhr am Mittwoch.", span="12 Uhr am Mittwoch", title="Interview",
      sel="12 Uhr am Mittwoch."),
    c("r068", "short_accept", "Re: Proben", LISA, [ME], "2026-09-25T10:35:00",
      "Donnerstag 18 Uhr passt.", span="Donnerstag 18 Uhr", title="Proben",
      sel="Donnerstag 18 Uhr passt."),
    c("r069", "short_accept", "Re: Kaffeepause", ANNA, [ME], "2026-09-25T10:40:00",
      "15 Uhr heute geht klar.", span="15 Uhr heute", title="Kaffeepause",
      sel="15 Uhr heute geht klar."),
    c("r070", "short_accept", "Re: Teamrunde", MAX, [ME], "2026-09-25T10:45:00",
      "Bestätige: Dienstag 16 Uhr.", span="Dienstag 16 Uhr", title="Teamrunde",
      sel="Dienstag 16 Uhr."),
    c("r071", "short_accept", "Re: Vorlesung", LISA, [ME], "2026-09-25T10:50:00",
      "Ok, dann morgen um 8 Uhr.", span="morgen um 8 Uhr", title="Vorlesung",
      sel="morgen um 8 Uhr."),
    c("r072", "short_accept", "Re: Abflug", ANNA, [ME], "2026-09-25T10:55:00",
      "Treffen morgen 6:30 Uhr am Flughafen.", span="morgen 6:30 Uhr am Flughafen",
      title="Abflug", loc="Flughafen", sel="morgen 6:30 Uhr am Flughafen."),
    c("r073", "short_accept", "Re: Lernrunde", MAX, [ME], "2026-09-25T11:00:00",
      "Sonntag 14 Uhr passt.", span="Sonntag 14 Uhr", title="Lernrunde",
      sel="Sonntag 14 Uhr passt."),
    c("r074", "short_accept", "Re: Konferenz", LISA, [ME], "2026-09-25T11:05:00",
      "Dann buche ich den 21.10. um 9 Uhr.", span="21.10. um 9 Uhr", title="Konferenz",
      sel="den 21.10. um 9 Uhr"),
    c("r075", "short_accept", "Re: Treffen", ANNA, [ME], "2026-09-25T11:10:00",
      "Einverstanden mit Freitag 13 Uhr.", span="Freitag 13 Uhr", title="Treffen",
      sel="Freitag 13 Uhr."),

    # ----------------------------------------------------------- relative_time (10)
    c("r076", "relative_time", "Statusrunde", LISA, [ME], "2026-09-25T10:00:00",
      "Lass uns morgen um 10 Uhr kurz sprechen.", span="morgen um 10 Uhr",
      title="Statusrunde"),
    c("r077", "relative_time", "Abgabe-Vorbereitung", MAX, [ME], "2026-09-25T10:00:00",
      "Wir treffen uns übermorgen um 15 Uhr.", span="übermorgen um 15 Uhr",
      title="Abgabe-Vorbereitung"),
    c("r078", "relative_time", "Jahresgespräch", ANNA, [ME], "2026-09-25T10:00:00",
      "Das Jahresgespräch ist am nächsten Montag um 14 Uhr.", span="am nächsten Montag um 14 Uhr",
      title="Jahresgespräch"),
    c("r079", "relative_time", "Wartung", LISA, [ME], "2026-09-25T10:00:00",
      "Die Wartung ist kommenden Donnerstag um 7 Uhr.", span="kommenden Donnerstag um 7 Uhr",
      title="Wartung"),
    c("r080", "relative_time", "Kennenlernen", MAX, [ME], "2026-09-25T10:00:00",
      "Kommenden Freitag um 16 Uhr treffen wir uns online.", span="Kommenden Freitag um 16 Uhr",
      title="Kennenlernen", loc="online"),
    c("r081", "relative_time", "Ausflug", ANNA, [ME], "2026-09-25T10:00:00",
      "Der Ausflug ist nächsten Samstag um 9 Uhr.", span="nächsten Samstag um 9 Uhr",
      title="Ausflug"),
    c("r082", "relative_time", "Interview", LISA, [ME], "2026-09-25T10:00:00",
      "Das Interview ist morgen von 11 bis 12 Uhr.", span="morgen von 11 bis 12 Uhr",
      title="Interview"),
    c("r083", "relative_time", "Rückgabe", MAX, [ME], "2026-09-25T10:00:00",
      "Bitte den Schlüssel übermorgen um 18 Uhr abholen.", span="übermorgen um 18 Uhr",
      title="Rückgabe"),
    c("r084", "relative_time", "Teammeeting", ANNA, [ME], "2026-09-25T10:00:00",
      "Das Teammeeting ist am kommenden Dienstag um 10 Uhr.", span="am kommenden Dienstag um 10 Uhr",
      title="Teammeeting"),
    c("r085", "relative_time", "Probe", LISA, [ME], "2026-09-25T10:00:00",
      "Die Probe ist am nächsten Donnerstag um 19 Uhr.", span="am nächsten Donnerstag um 19 Uhr",
      title="Probe"),

    # --------------------------------------------------------------- timezone (10)
    c("r086", "timezone", "Weekly Sync", "john@partner.example", [ME],
      "2026-09-25T10:00:00",
      "Let's meet Thursday at 3pm CET.", span="Thursday at 3pm CET", title="Weekly Sync"),
    c("r087", "timezone", "Partner Call", "sales@partner.example", [ME],
      "2026-09-25T10:00:00",
      "Our call is on Friday at 10 AM Pacific.", span="Friday at 10 AM Pacific",
      title="Partner Call"),
    c("r088", "timezone", "Board Meeting", "office@firma.example", [ME],
      "2026-09-25T10:00:00",
      "The board meeting is Tuesday at 14:00 BST.", span="Tuesday at 14:00 BST",
      title="Board Meeting"),
    c("r089", "timezone", "Interviews", "hr@firma.example", [ME],
      "2026-09-25T10:00:00",
      "Interviews start at 9am ET on Monday.", span="9am ET on Monday",
      title="Interviews"),
    c("r090", "timezone", "Design Review", "lisa@design.example", [ME],
      "2026-09-25T10:00:00",
      "Design review Wednesday 16:00 GMT.", span="Wednesday 16:00 GMT",
      title="Design Review"),
    c("r091", "timezone", "Kick-off", "pm@firma.example", [ME],
      "2026-09-25T10:00:00",
      "Kick-off is Thursday 9am EST / 3pm CET.", span="Thursday 9am EST / 3pm CET",
      title="Kick-off"),
    c("r092", "timezone", "Standup", "team@firma.example", [ME],
      "2026-09-25T10:00:00",
      "Standup tomorrow at 8:30 UTC.", span="tomorrow at 8:30 UTC", title="Standup"),
    c("r093", "timezone", "Retro", "team@firma.example", [ME],
      "2026-09-25T10:00:00",
      "Retro on Friday 15:00 CEST.", span="Friday 15:00 CEST", title="Retro"),
    c("r094", "timezone", "Demo", "pm@firma.example", [ME],
      "2026-09-25T10:00:00",
      "Demo next Tuesday 11am PT.", span="next Tuesday 11am PT", title="Demo"),
    c("r095", "timezone", "1:1", "lead@firma.example", [ME],
      "2026-09-25T10:00:00",
      "1:1 Monday 9:00 Europe/Berlin.", span="Monday 9:00 Europe/Berlin", title="1:1"),

    # ---------------------------------------------------------- long_signature (10)
    c("r096", "long_signature", "Projektbesprechung Q4", "lisa@firma.example", [ME, MAX],
      "2026-09-25T09:30:00",
      "Hallo zusammen,\n\nwie besprochen treffen wir uns am 14.10. um 10 Uhr "
      "im Besprechungsraum A, um den Q4-Plan abzustimmen. Bitte bringt die "
      "aktuellen Zahlen mit.\n\nViele Grüße\nLisa\n\n"
      "---\nLisa Beispiel\nProjektleitung\nTel: +49 123 456\nFirma GmbH, Musterstr. 1",
      span="am 14.10. um 10 Uhr", title="Projektbesprechung Q4", loc="Besprechungsraum A"),
    c("r097", "long_signature", "Einarbeitung", "hr@firma.example", [ME],
      "2026-09-25T09:30:00",
      "Guten Tag,\n\ndeine Einarbeitung beginnt am 2.11. um 9 Uhr bei uns am "
      "Empfang. Melde dich bitte 10 Minuten vorher an.\n\n"
      "Mit freundlichen Grüßen\nPersonalabteilung\nFirma GmbH\n"
      "Musterstr. 1\nTel: +49 123 456\nwww.firma.example",
      span="am 2.11. um 9 Uhr", title="Einarbeitung", loc="Empfang"),
    c("r098", "long_signature", "Klassenfahrt", "schule@example.org", [ME],
      "2026-09-25T09:30:00",
      "Liebe Eltern,\n\ndie Klassenfahrt startet am 5.10. um 7:30 Uhr an der "
      "Schule. Bitte geben Sie das Geld bis Freitag ab.\n\n"
      "Freundliche Grüße\nSekretariat\nGrundschule Musterstadt\nTel: 0123/456",
      span="am 5.10. um 7:30 Uhr", title="Klassenfahrt", loc="Schule"),
    c("r099", "long_signature", "Strategieworkshop", "max@firma.example", [ME],
      "2026-09-25T09:30:00",
      "Hi,\n\nder Strategieworkshop ist am 11.11. von 9 bis 17 Uhr im Hotel "
      "Lakeside. Übernachtung ist organisiert.\n\nBeste Grüße\nMax\n\n"
      "--\nMax Muster | Strategy | +49 987 654 | max@firma.example",
      span="am 11.11. von 9 bis 17 Uhr", title="Strategieworkshop", loc="Hotel Lakeside"),
    c("r100", "long_signature", "Vorsorgeuntersuchung", "praxis@arzt.example", [ME],
      "2026-09-25T09:30:00",
      "Sehr geehrte Patientin, sehr geehrter Patient,\n\nIhre "
      "Vorsorgeuntersuchung ist am 29.10. um 8:45 Uhr. Bitte bringen Sie die "
      "Versichertenkarte mit.\n\nMit freundlichen Grüßen\n"
      "Praxis Dr. Muster\nTel: 0341/123456",
      span="am 29.10. um 8:45 Uhr", title="Vorsorgeuntersuchung"),
    c("r101", "long_signature", "Team-Offsite", "anna@firma.example", [ME],
      "2026-09-25T09:30:00",
      "Hallo,\n\nunser Team-Offsite findet am 12.11. um 10 Uhr in der "
      "Lodge statt. Agenda folgt.\n\nLG Anna\n\n"
      "Anna Beispiel\nEngineering\nMobile: +49 555 123",
      span="am 12.11. um 10 Uhr", title="Team-Offsite", loc="Lodge"),
    c("r102", "long_signature", "Mietvertrag", "verwaltung@immo.example", [ME],
      "2026-09-25T09:30:00",
      "Guten Tag,\n\nbitte erscheinen Sie am 6.11. um 14 Uhr zur "
      "Vertragsunterzeichnung in unserem Büro.\n\n"
      "Freundliche Grüße\nHausverwaltung GmbH\nTel: 030/987654",
      span="am 6.11. um 14 Uhr", title="Mietvertrag", loc="Büro"),
    c("r103", "long_signature", "Präsentation", "tim@firma.example", [ME, LISA],
      "2026-09-25T09:30:00",
      "Servus,\n\nich präsentiere am 13.10. um 15 Uhr unsere Ergebnisse. "
      "Würde mich über Feedback freuen.\n\nGruß Tim\n\n"
      "Tim Test | Data | tim@firma.example | +49 777 888",
      span="am 13.10. um 15 Uhr", title="Präsentation"),
    c("r104", "long_signature", "Nachhilfe", "nachhilfe@example.org", [ME],
      "2026-09-25T09:30:00",
      "Hallo,\n\ndie erste Nachhilfestunde ist am 8.10. um 16:30 Uhr bei uns "
      "zu Hause.\n\nViele Grüße\nFamilie Muster\nTel: 0170/123456",
      span="am 8.10. um 16:30 Uhr", title="Nachhilfe"),
    c("r105", "long_signature", "Quartalsreview", "lead@firma.example", [ME],
      "2026-09-25T09:30:00",
      "Hallo Team,\n\ndas Quartalsreview ist am 20.10. um 13 Uhr. Bitte "
      "aktualisiert eure OKRs vorher.\n\nDanke und Grüße\nLead\n\n"
      "--\nLead | Firma GmbH | office@firma.example",
      span="am 20.10. um 13 Uhr", title="Quartalsreview"),

    # ---------------------------------------------------------- quoted_thread (15)
    c("r106", "quoted_thread", "Re: Projekt Alpha", LISA, [ME, MAX],
      "2026-09-25T10:00:00",
      "Dienstag 14 Uhr passt mir.\n\nAm 24.09. schrieb Max:\n> Wie wäre "
      "Dienstag 14 Uhr oder Mittwoch 10 Uhr?\n\nAm 23.09. schrieb Lisa:\n"
      "> Montag 16 Uhr wäre auch okay.",
      span="Dienstag 14 Uhr", title="Projekt Alpha", sel="Dienstag 14 Uhr passt mir."),
    c("r107", "quoted_thread", "Re: Urlaub", MAX, [ME], "2026-09-25T10:00:00",
      "Ich nehme den 3.11. und 4.11. frei.\n\nAm 20.09. schrieb ich:\n"
      "> Nimmst du dir im November frei?",
      span="den 3.11. und 4.11.", title="Urlaub", sel="den 3.11. und 4.11."),
    c("r108", "quoted_thread", "Re: Termin", ANNA, [ME], "2026-09-25T10:00:00",
      "Dann machen wir den 15.10. um 9 Uhr.\n\nAm 22.09. schrieb Anna:\n"
      "> Vorschlag: 14.10. oder 15.10.?",
      span="den 15.10. um 9 Uhr", title="Termin", sel="den 15.10. um 9 Uhr"),
    # Confirmation-seeking question, not a settled event -> no create.
    c("r109", "tentative_slots", "Re: Planungstreffen", LISA, [ME],
      "2026-09-25T10:00:00",
      "Bleibt es bei Donnerstag 11 Uhr?\n\nAm 21.09. schrieb Lisa:\n"
      "> Können wir Donnerstag 11 Uhr?", count=0),
    c("r110", "quoted_thread", "Re: Workshop", MAX, [ME], "2026-09-25T10:00:00",
      "Der Workshop ist am 8.11. von 10 bis 16 Uhr.\n\nAm 19.09. schrieb Max:\n"
      "> Wann passt der Workshop?",
      span="am 8.11. von 10 bis 16 Uhr", title="Workshop"),
    c("r111", "quoted_thread", "Re: Abendessen", ANNA, [ME], "2026-09-25T10:00:00",
      "Freitag 19 Uhr beim Italiener steht.\n\nAm 23.09. schrieb Anna:\n"
      "> Italiener am Freitag?",
      span="Freitag 19 Uhr", title="Abendessen", loc="Italiener",
      sel="Freitag 19 Uhr beim Italiener"),
    c("r112", "quoted_thread", "Re: Fußball", LISA, [ME], "2026-09-25T10:00:00",
      "Kickoff ist Samstag 15 Uhr.\n\nAm 18.09. schrieb Lisa:\n> Samstag Fußball?",
      span="Samstag 15 Uhr", title="Fußball", sel="Samstag 15 Uhr"),
    c("r113", "quoted_thread", "Re: Anreise", MAX, [ME], "2026-09-25T10:00:00",
      "Wir treffen uns am 2.10. um 6 Uhr am Bahnhof.\n\nAm 17.09. schrieb Max:\n"
      "> Wann fahren wir los?",
      span="am 2.10. um 6 Uhr", title="Anreise", loc="Bahnhof",
      sel="am 2.10. um 6 Uhr"),
    c("r114", "quoted_thread", "Re: Vorbereitung", ANNA, [ME], "2026-09-25T10:00:00",
      "Ich komme am 13.10. um 8 Uhr dazu.\n\nAm 16.09. schrieb Anna:\n"
      "> Bist du am 13.10. dabei?",
      span="am 13.10. um 8 Uhr", title="Vorbereitung", sel="am 13.10. um 8 Uhr"),
    c("r115", "quoted_thread", "Re: Treffen mit Kunde", LISA, [ME],
      "2026-09-25T10:00:00",
      "Der Kundentermin ist am 16.10. um 14 Uhr.\n\nAm 15.09. schrieb Lisa:\n"
      "> Kunde kommt am 16.10. oder 17.10.",
      span="am 16.10. um 14 Uhr", title="Treffen mit Kunde", sel="am 16.10. um 14 Uhr"),
    c("r116", "quoted_thread", "Re: Rückfrage", MAX, [ME], "2026-09-25T10:00:00",
      "Passt mir gut, also Mittwoch 10 Uhr.\n\nAm 24.09. schrieb Max:\n"
      "> Dienstag 14 Uhr oder Mittwoch 10 Uhr?",
      span="Mittwoch 10 Uhr", title="Rückfrage", sel="Mittwoch 10 Uhr"),
    c("r117", "quoted_thread", "Re: Präsentation", ANNA, [ME], "2026-09-25T10:00:00",
      "Dann halte ich sie am 21.10. um 10 Uhr.\n\nAm 22.09. schrieb Anna:\n"
      "> Wann präsentierst du?",
      span="am 21.10. um 10 Uhr", title="Präsentation", sel="am 21.10. um 10 Uhr"),
    c("r118", "quoted_thread", "Re: Team-Sync", LISA, [ME], "2026-09-25T10:00:00",
      "Nächsten Montag 9 Uhr passt.\n\nAm 20.09. schrieb Lisa:\n"
      "> Wie wäre Montag 9 Uhr?",
      span="Nächsten Montag 9 Uhr", title="Team-Sync", sel="Nächsten Montag 9 Uhr"),
    c("r119", "quoted_thread", "Re: Ausflug", MAX, [ME], "2026-09-25T10:00:00",
      "Wir fahren am 4.10. um 8 Uhr los.\n\nAm 19.09. schrieb Max:\n"
      "> Ausflug am 4. oder 5.10.?",
      span="am 4.10. um 8 Uhr", title="Ausflug", sel="am 4.10. um 8 Uhr"),
    # Slot offered ("ist frei"), not agreed -> no create.
    c("r120", "tentative_slots", "Re: Sprechstunde", ANNA, [ME], "2026-09-25T10:00:00",
      "Komm vorbei, Dienstag 13 Uhr ist frei.\n\nAm 18.09. schrieb Anna:\n"
      "> Hast du Dienstag Zeit?", count=0),

    # --------------------------------------------------------- tentative_slots (10)
    c("r121", "tentative_slots", "Terminabstimmung", LISA, [ME, MAX],
      "2026-09-25T10:00:00",
      "Passt dir Dienstag 14 Uhr oder Mittwoch 10 Uhr?", count=0),
    c("r122", "tentative_slots", "Doodle", MAX, [ME], "2026-09-25T10:00:00",
      "Ich könnte Dienstag oder Mittwoch.", count=0),
    c("r123", "tentative_slots", "Wann passt es?", ANNA, [ME], "2026-09-25T10:00:00",
      "Hast du Montag 9 Uhr oder Freitag 15 Uhr Zeit?", count=0),
    c("r124", "tentative_slots", "Vorschlag", LISA, [ME], "2026-09-25T10:00:00",
      "Wie wäre es mit 12.10. oder 13.10.? Ich bin flexibel.", count=0),
    c("r125", "tentative_slots", "Abstimmung", MAX, [ME], "2026-09-25T10:00:00",
      "Option A: Dienstag 11 Uhr, Option B: Donnerstag 16 Uhr. Was passt?", count=0),
    c("r126", "tentative_slots", "Planung offen", ANNA, [ME], "2026-09-25T10:00:00",
      "Lass uns nächste Woche etwas ausmachen, ich melde mich.", count=0),
    c("r127", "tentative_slots", "Rückfrage", LISA, [ME], "2026-09-25T10:00:00",
      "Könntest du am 12.10. um 14 Uhr? Ich muss noch prüfen.", count=0),
    c("r128", "tentative_slots", "Backup-Slot", MAX, [ME], "2026-09-25T10:00:00",
      "Falls Dienstag nicht geht, wäre Donnerstag eine Option.", count=0),
    c("r129", "tentative_slots", "Terminfindung", ANNA, [ME], "2026-09-25T10:00:00",
      "Gib mir gern zwei Termine, dann stimme ich ab.", count=0),
    c("r130", "tentative_slots", "Umfrage", LISA, [ME], "2026-09-25T10:00:00",
      "Bitte stimmt ab: Montag oder Mittwoch, jeweils 10 Uhr?", count=0),

    # ------------------------------------------------------- cancel_reschedule (10)
    c("r131", "cancel_reschedule", "Absage", LISA, [ME], "2026-09-25T10:00:00",
      "Unser Termin am Dienstag 14 Uhr fällt leider aus.", count=0),
    c("r132", "cancel_reschedule", "Verschiebung", MAX, [ME], "2026-09-25T10:00:00",
      "Wir verschieben das Meeting von Dienstag auf Mittwoch 15 Uhr.", count=0),
    c("r133", "cancel_reschedule", "Leider krank", ANNA, [ME], "2026-09-25T10:00:00",
      "Ich muss den Termin am 12.10. leider absagen.", count=0),
    c("r134", "cancel_reschedule", "Neuer Termin nötig", LISA, [ME], "2026-09-25T10:00:00",
      "Der Workshop am 8.11. muss leider entfallen.", count=0),
    c("r135", "cancel_reschedule", "Terminänderung", MAX, [ME], "2026-09-25T10:00:00",
      "Statt Donnerstag 10 Uhr jetzt Freitag 10 Uhr.", count=0),
    c("r136", "cancel_reschedule", "Absage Screening", ANNA, [ME], "2026-09-25T10:00:00",
      "Das Screening am 15.10. wird abgesagt.", count=0),
    c("r137", "cancel_reschedule", "Verschoben", LISA, [ME], "2026-09-25T10:00:00",
      "Der Kundentermin wurde auf unbestimmte Zeit verschoben.", count=0),
    c("r138", "cancel_reschedule", "Stornierung", MAX, [ME], "2026-09-25T10:00:00",
      "Bitte storniere meinen Termin am 20.10. um 9 Uhr.", count=0),
    c("r139", "cancel_reschedule", "Kann nicht", ANNA, [ME], "2026-09-25T10:00:00",
      "Dienstag 14 Uhr klappt bei mir doch nicht, wir müssen neu planen.", count=0),
    c("r140", "cancel_reschedule", "Abgesagt", LISA, [ME], "2026-09-25T10:00:00",
      "Die Probe am Donnerstag fällt aus.", count=0),

    # ------------------------------------------------------------------ deadline (5)
    c("r141", "deadline", "Unterlagen", "office@firma.example", [ME],
      "2026-09-25T10:00:00",
      "Bitte schicke die Unterlagen bis Freitag.", count=0),
    c("r142", "deadline", "Rückmeldung", LISA, [ME], "2026-09-25T10:00:00",
      "Rückmeldung bitte bis 12.10.", count=0),
    c("r143", "deadline", "Abgabe", MAX, [ME], "2026-09-25T10:00:00",
      "Die Abgabe ist spätestens am 30.10.", count=0),
    c("r144", "deadline", "Rechnung", ANNA, [ME], "2026-09-25T10:00:00",
      "Die Rechnung ist bis zum 15.11. fällig.", count=0),
    c("r145", "deadline", "Antrag", LISA, [ME], "2026-09-25T10:00:00",
      "Der Antrag muss bis 5.12. eingereicht werden.", count=0),

    # --------------------------------------------------------------- unrelated (5)
    c("r146", "unrelated", "Newsletter", "news@example.org", [ME],
      "2026-09-25T10:00:00",
      "Im Bericht geht es um das Meeting vom 12.10. und seine Ergebnisse.",
      count=0),
    c("r147", "unrelated", "Witz", "freund@example.org", [ME],
      "2026-09-25T10:00:00",
      "Erzähl mir einen Witz.", count=0),
    c("r148", "unrelated", "Wetter", "freund@example.org", [ME],
      "2026-09-25T10:00:00",
      "Wie wird das Wetter morgen?", count=0),
    c("r149", "unrelated", "Rezept", "freund@example.org", [ME],
      "2026-09-25T10:00:00",
      "Hast du ein gutes Rezept für Pfannkuchen?", count=0),
    c("r150", "unrelated", "Bericht", "kollege@firma.example", [ME],
      "2026-09-25T10:00:00",
      "Das Meeting vom 12.10. war sehr produktiv, danke für die Orga.", count=0),
]

# ---------------------------------------------------------------- hard inbox
# Deliberately messy inbox stress. Mixed supported / needs_review / no_event.
HARD_CASES = [
    c("h001", "hard_reply", "Re: Re: Sommerfest", MAX, [ME, ANNA],
      "2026-09-05T10:00:00",
      "Kurz und knapp: Wir treffen uns am 12.9. um 17 Uhr vor dem Vereinsheim.\n\n"
      "Am 10.9. schrieb Max:\n> Passt euch der 12.9.?\n\nAm 9.9. schrieb Anna:\n"
      "> Oder lieber der 13.9.?\n\nAm 8.9. schrieb Lisa:\n> Am 11.9. kann ich nicht.",
      span="am 12.9. um 17 Uhr", title="Sommerfest", loc="Vereinsheim"),
    c("h002", "hard_reply", "AW: WG: Projekt", LISA, [ME], "2026-09-30T10:00:00",
      "Hallo,\n\ndanke für die vielen Termine. Aktuell gilt: Kickoff am 5.10. "
      "um 9 Uhr im Raum 1.\n\n> Am 2.10. um 14 Uhr wäre auch möglich.\n"
      "> Am 3.10. um 16 Uhr ginge bei mir.\n> Was meinst du, 1.10. oder 2.10.?",
      span="am 5.10. um 9 Uhr", title="Kickoff", loc="Raum 1"),
    c("h003", "hard_footer", "Terminbestätigung", "praxis@muster.example", [ME],
      "2026-10-01T10:00:00",
      "Hallo,\n\nder Termin ist am 14.10. um 10 Uhr.\n\n--\nPraxis Muster\n"
      "Öffnungszeiten: Mo 8-12, Di 14-18, Do 8-12\nTel: 0341 111\nFax: 0341 112",
      span="am 14.10. um 10 Uhr", title="Terminbestätigung"),
    c("h004", "hard_invoice", "Rechnung und Termin", "office@firma.example", [ME],
      "2026-09-28T10:00:00",
      "Guten Tag,\n\nRechnung Nr. 2026-4711 vom 30.09. ist beglichen. Ihr "
      "Beratungstermin ist am 7.10. um 15 Uhr.\n\nViele Grüße",
      span="am 7.10. um 15 Uhr", title="Beratungstermin"),
    c("h005", "hard_numbers", "Rückruf", "service@firma.example", [ME],
      "2026-09-29T10:00:00",
      "Rufen Sie uns unter 030 111 oder 030 222 an. Ihr Termin ist am 9.10. "
      "um 11 Uhr.", span="am 9.10. um 11 Uhr", title="Rückruf"),
    c("h006", "hard_quoted", "Re: Termin", LISA, [ME], "2026-09-25T10:00:00",
      "Dienstag 14 Uhr passt.\n\nAm 24.09. schrieb Lisa:\n> Wie wäre Dienstag "
      "14 Uhr oder Mittwoch 10 Uhr?\nAm 23.09. schrieb Tim:\n> Montag 16 Uhr?",
      span="Dienstag 14 Uhr", title="Termin"),
    c("h007", "hard_subject", "Newsletter 09/2026", "news@firma.example", [ME],
      "2026-10-02T10:00:00",
      "Liebe Leser,\n\nunser Team-Meeting ist am 18.10. um 13 Uhr. Alle Infos "
      "unten im Newsletter.", span="am 18.10. um 13 Uhr", title="Team-Meeting"),
    c("h008", "hard_signature", "Statusupdate", "kollege@firma.example", [ME],
      "2026-10-01T10:00:00",
      "Das Meeting ist am 3.11. um 10 Uhr.\n\n--\nDiese Mail wurde maschinell "
      "erstellt und gilt auch ohne Unterschrift. Terminänderungen bitte bis "
      "morgen melden.", span="am 3.11. um 10 Uhr", title="Statusupdate"),
    c("h009", "hard_link", "Meeting-Link", MAX, [ME], "2026-10-01T10:00:00",
      "Hier der Link: https://meet.example/abc. Wir sprechen uns am 6.10. um "
      "9 Uhr.", span="am 6.10. um 9 Uhr", title="Meeting-Link"),
    c("h010", "hard_mixed", "Weekly", LISA, [ME], "2026-09-25T10:00:00",
      "Hi, quick update: das Review ist am Dienstag um 14 Uhr.",
      span="am Dienstag um 14 Uhr", title="Review"),
    c("h011", "hard_ab", "Kurs", "schule@example.org", [ME], "2026-10-01T10:00:00",
      "Der Kurs startet am 8.10. ab 14 Uhr.", span="am 8.10. ab 14 Uhr",
      title="Kurs"),
    c("h012", "hard_fuzzy", "Treffen", MAX, [ME], "2026-10-01T10:00:00",
      "Wir treffen uns am 11.10. gegen 14 Uhr.", span="am 11.10. gegen 14 Uhr",
      title="Treffen", incomplete=True),
    c("h013", "hard_fuzzy", "Wartungsfenster", "it@firma.example", [ME],
      "2026-10-01T10:00:00",
      "Das Fenster ist am 12.10. zwischen 14 und 15 Uhr.",
      span="am 12.10. zwischen 14 und 15 Uhr", title="Wartungsfenster",
      incomplete=True),
    c("h014", "hard_fuzzy", "Beginn", LISA, [ME], "2026-10-01T10:00:00",
      "Beginn ist am 13.10. ca. 10 Uhr.", span="am 13.10. ca. 10 Uhr",
      title="Beginn", incomplete=True),
    c("h015", "hard_fuzzy", "Mittag", ANNA, [ME], "2026-10-01T10:00:00",
      "Wir sehen uns am 14.10. nach dem Mittagessen.",
      span="am 14.10. nach dem Mittagessen", title="Mittag", incomplete=True),
    c("h016", "hard_fuzzy", "Vormittags", LISA, [ME], "2026-09-25T10:00:00",
      "Der Termin ist morgen Vormittag.", span="morgen Vormittag",
      title="Vormittags", incomplete=True),
    c("h017", "hard_tentative", "Re: Ausflug", ANNA, [ME], "2026-09-20T10:00:00",
      "Am 20.09. schrieb Lisa:\n> Treffen wir uns am 1.10. um 9 Uhr?\n"
      "> Oder am 2.10. um 14 Uhr?\n\nHast du dich schon entschieden?", count=0),
    c("h018", "hard_prefix", "AW: Re: Fwd: WG: Protokoll", LISA, [ME],
      "2026-10-01T10:00:00",
      "Der Jour fixe ist am 15.10. um 8:30 Uhr.", span="am 15.10. um 8:30 Uhr",
      title="Jour fixe"),
    c("h019", "hard_format", "Team", MAX, [ME], "2026-10-01T10:00:00",
      "Hallo&nbsp;Team,<br><br>der Termin ist am 16.10. um 9 Uhr.<br>",
      span="am 16.10. um 9 Uhr", title="Team"),
    c("h020", "hard_confirmed", "Terminbestätigung", "office@firma.example", [ME],
      "2026-10-02T10:00:00",
      "Termin wie besprochen am 17.10. um 11 Uhr.",
      span="am 17.10. um 11 Uhr", title="Terminbestätigung"),
]


def _resolve(text: str, ref_iso: str, span: str, loc: str, title: str,
             *, source: str, cid: str, force_incomplete: bool = False) -> dict:
    from datetime import datetime
    ref = datetime.fromisoformat(ref_iso)
    if not span:
        return {"start": None, "end": None, "all_day": False,
                "incomplete": bool(force_incomplete)}
    timing = compile_when(span, ref)
    incomplete = bool(force_incomplete) or timing.incomplete or bool(
        TZ_RE.search(span))
    src = _norm(source)
    if _norm(span) not in src:
        raise SystemExit(f"{cid}: gold span {span!r} not a substring of source")
    if loc and _norm(loc) not in src:
        raise SystemExit(f"{cid}: gold location {loc!r} not a substring of source")
    if incomplete:
        # Deliberately unresolved (timezone / fuzzy time): no hard start/end,
        # the workflow must route this to human review instead.
        return {"start": None, "end": None, "all_day": timing.all_day,
                "incomplete": True}
    return {"start": timing.start.isoformat() if timing.start else None,
            "end": timing.end.isoformat() if timing.end else None,
            "all_day": timing.all_day, "incomplete": False}


# IDs reserved for contract/order/wording development. These are written to
# contract_dev.jsonl and MUST NOT be used as the final gate (FT_PLAN.md §7/§9):
# choosing a contract on the challenge set would leak it.
DEV_IDS = {
    "r001", "r010", "r019",            # informal_confirm
    "r026", "r034", "r041",            # formal_confirm
    "r046", "r053", "r056",            # announcement
    "r061", "r068", "r072",            # short_accept
    "r076", "r082", "r085",            # relative_time
    "r086", "r091", "r095",            # timezone
    "r096", "r101", "r105",            # long_signature
    "r106", "r110", "r115",            # quoted_thread
    "r017", "r121", "r125", "r130",    # tentative_slots (incl. reclassified)
    "r131", "r135", "r140",            # cancel_reschedule
    "r141", "r145",                    # deadline
    "r146", "r150",                    # unrelated
}


def build(cases: list[dict]) -> list[dict]:
    out = []
    for case in cases:
        ref = case["message"]["received_at"]
        body = case["message"]["body"]
        force = case.get("incomplete", False)
        gold = dict(case["gold"])
        gold.update(_resolve(body, ref, gold["span"], gold["location"],
                             gold["title"], source=body, cid=case["id"],
                             force_incomplete=force))
        entry = {"id": case["id"], "category": case["category"],
                 "message": case["message"], "selection": case["selection"],
                 "expected": gold, "selection_expected": None}
        sg = case["selection_gold"]
        if sg is not None and case["selection"]:
            sg = dict(sg)
            sel_src = _norm(case["selection"])
            if sg["location"] and _norm(sg["location"]) not in sel_src:
                # The marked text does not carry the place -> no location gold.
                sg["location"] = ""
            sg.update(_resolve(case["selection"], ref, sg["span"], sg["location"],
                               sg["title"], source=case["selection"],
                               cid=case["id"] + "/sel",
                               force_incomplete=force))
            entry["selection_expected"] = sg
        out.append(entry)
    return out


def _write(name: str, rows: list[dict]) -> None:
    path = HERE / name
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    cats: dict[str, int] = {}
    for r in rows:
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    positive = sum(1 for r in rows if r["expected"]["event_count"] == 1)
    sel = sum(1 for r in rows if r["selection_expected"])
    rev = sum(1 for r in rows if r["expected"].get("incomplete"))
    print(f"wrote {len(rows):>3} -> {name}  (pos {positive}, sel {sel}, review {rev})")
    for k in sorted(cats):
        print(f"      {k:18} {cats[k]}")


def main() -> int:
    dev = [c for c in CASES if c["id"] in DEV_IDS]
    frozen = [c for c in CASES if c["id"] not in DEV_IDS]
    _write("contract_dev.jsonl", build(dev))
    _write("realism_challenge.jsonl", build(frozen))
    _write("hard_challenge.jsonl", build(HARD_CASES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
