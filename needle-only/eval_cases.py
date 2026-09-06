"""Eval-Fälle v5.5 „Router + Absence": Notizen entfernt, Urlaub-Tests neu.
Format: (name, input_text, expected_calls_or_string, seeds).
Seeds: (title, value) — ISO = Termin, 'R iso' = Reminder, text = Notiz."""

CASES = [
    # ==========================================
    # calendar_create — Termine (5 Fälle)
    # ==========================================
    ("cal-create-absolut",
     "erstelle einen termin zahnarzt am 10.9.2026 um 10 uhr",
     [("calendar_create", [("eq", "kind", "appointment"),
                           ("contains", "title", "zahnarzt")])],
     []),

    ("cal-create-relativ",
     "termin arzt nächste woche dienstag 14 uhr",
     [("calendar_create", [("eq", "kind", "appointment")])],
     []),

    ("cal-create-mit-ort",
     "erstelle einen termin meeting im besprechungsraum morgen um 9 uhr",
     [("calendar_create", [("eq", "kind", "appointment"),
                           ("contains", "title", "meeting")])],
     []),

    ("cal-create-iso",
     "neuer termin zahnarzt am 10.9. um 10 uhr",
     [("calendar_create", [("contains", "title", "zahnarzt")])],
     []),

    # USER-REPORT: Titel ohne 'termin', korrektes Datum (Di 8.9.)
    ("cal-create-dienstag",
     "erstelle am dienstag einen termin zahnarzt 9:00",
     [("calendar_create", [("eq", "kind", "appointment"),
                           ("contains", "title", "zahnarzt")])],
     []),

    # ==========================================
    # calendar_create — Kollision (1 Fall)
    # ==========================================
    # Seed: arbeitstreffen am Di 8.9. 09:00 bereits vorhanden
    # User-Report: verschiedene Titel, gleiche Zeit → Kollision!
    ("cal-create-kollision",
     "erstelle am dienstag einen termin zahnarzt 9:00",
     "KOLLISION",
     [("Arbeitstreffen", "2026-09-08T09:00:00+02:00")]),

    # ==========================================
    # calendar_create — Erinnerungen (3 Fälle)
    # ==========================================
    ("cal-create-erinnerung",
     "erinnerung wasser trinken in 2 minuten",
     [("calendar_create", [("eq", "kind", "reminder")])],
     []),

    ("cal-create-erinnerung-morgen",
     "stell eine erinnerung medikamente nehmen morgen früh 8 uhr",
     [("calendar_create", [("eq", "kind", "reminder")])],
     []),

    # USER-REPORT: 'erstelle eine erinnerung' → explizit CREATE
    ("cal-create-erinnerung-explicit",
     "erstelle eine erinnerung wasser trinken in 10 minuten",
     [("calendar_create", [("eq", "kind", "reminder"),
                           ("contains", "title", "wasser")])],
     []),

    # ==========================================
    # calendar_create — Aufgaben (2 Fälle)
    # ==========================================
    ("cal-create-aufgabe",
     "aufgabe bericht schreiben bis freitag",
     [("calendar_create", [("eq", "kind", "task")])],
     []),

    ("cal-create-aufgabe-deadline",
     "erstelle eine aufgabe steuererklärung abgeben bis 31.10.",
     [("calendar_create", [("eq", "kind", "task")])],
     []),

    # ==========================================
    # calendar_create — ABSENCE (Urlaub) — NEU!
    # ==========================================
    # USER-REPORT: 'Trage im Kalender von 07.09. bis 11.09. Urlaub ein!'
    # Muss als mehrtägige Abwesenheit erstellt werden
    # UND darf KEINE Kollision mit bestehenden Terminen triggern
    ("cal-create-urlaub",
     "trage im kalender von 7.9. bis 11.9. urlaub ein",
     [("calendar_create", [("eq", "kind", "absence"),
                           ("contains", "title", "urlaub")])],
     []),

    # Urlaub + bestehende Termine = KEINE Kollision!
    ("cal-create-urlaub-mit-terminen",
     "trage im kalender von 7.9. bis 11.9. urlaub ein",
     [("calendar_create", [("eq", "kind", "absence")])],
     [("Hundefrisör", "2026-09-08T09:00:00+02:00")]),

    # ==========================================
    # calendar_edit (2 Fälle)
    # ==========================================
    ("cal-edit-verschieben",
     "verschiebe zahnarzt auf 11:30",
     [("calendar_edit", [("contains", "title", "zahnarzt")])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    ("cal-edit-uhrzeit",
     "änder den termin arzt auf morgen 14 uhr",
     [("calendar_edit", [("contains", "title", "arzt")])],
     [("Arzt", "2026-09-12T10:00:00+02:00")]),

    # ==========================================
    # calendar_read (4 Fälle)
    # ==========================================
    ("cal-read-all",
     "was steht diese woche an?",
     [("calendar_read", [])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    ("cal-read-reminders",
     "zeige meine erinnerungen",
     [("calendar_read", [("eq", "kind", "reminder")])],
     [("Wasser trinken", "R 2026-09-08T09:00:00+02:00")]),

    ("cal-read-appointments",
     "was habe ich nächste woche für termine",
     [("calendar_read", [("eq", "kind", "appointment")])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    ("cal-read-tasks",
     "zeige meine aufgaben",
     [("calendar_read", [("eq", "kind", "task")])],
     []),

    # ==========================================
    # calendar_delete (3 Fälle)
    # ==========================================
    ("cal-delete",
     "lösche zahnarzt",
     [("calendar_delete", [("contains", "title", "zahnarzt")])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    ("cal-delete-explicit",
     "entferne den termin zahnarzt aus dem kalender",
     [("calendar_delete", [("contains", "title", "zahnarzt")])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    # USER-REPORT: 'lösche termin am 11.9. um 9:00' → delete per Datum/Uhrzeit
    ("cal-delete-datum",
     "lösche termin am 11.9. um 9:00",
     [("calendar_delete", [("contains", "title", "termin")])],
     [("Arbeitstreffen", "2026-09-11T09:00:00+02:00")]),

    # ==========================================
    # calendar_filter (Gruppen-Abfragen) — NEU!
    # ==========================================
    ("cal-filter-person",
     "wann hat lisa diese woche termine",
     [("calendar_filter", [("contains", "person", "lisa")])],
     []),

    # ==========================================
    # NOWRITE (2 Fälle)
    # ==========================================
    ("nowrite-allgemeinwissen",
     "wer hat das internet erfunden?",
     "NOWRITE",
     []),

    ("nowrite-chitchat",
     "wie geht es dir?",
     "NOWRITE",
     []),
]
