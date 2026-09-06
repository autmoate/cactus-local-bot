"""Eval-Fälle v5.4 „Router": 24 Cases, alle 7 Tools + NOWRITE + Multi-Op.
Format: (name, input_text, expected_calls_or_string, seeds).
Seeds: (title, value) — ISO = Termin, 'R iso' = Reminder, text = Notiz."""

CASES = [
    # ==========================================
    # calendar_create — Termine (4 Fälle)
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

    # ==========================================
    # calendar_create — Erinnerungen (2 Fälle)
    # ==========================================
    ("cal-create-erinnerung",
     "erinnerung wasser trinken in 2 minuten",
     [("calendar_create", [("eq", "kind", "reminder")])],
     []),

    ("cal-create-erinnerung-morgen",
     "stell eine erinnerung medikamente nehmen morgen früh 8 uhr",
     [("calendar_create", [("eq", "kind", "reminder")])],
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
    # calendar_delete (2 Fälle)
    # ==========================================
    ("cal-delete",
     "lösche zahnarzt",
     [("calendar_delete", [("contains", "title", "zahnarzt")])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    ("cal-delete-explicit",
     "entferne den termin zahnarzt aus dem kalender",
     [("calendar_delete", [("contains", "title", "zahnarzt")])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    # ==========================================
    # note_write (3 Fälle)
    # ==========================================
    ("note-write",
     "merk dir feuerholz kostet 8 euro",
     [("note_write", [("contains", "subject", "feuerholz")])],
     []),

    ("note-write-adresse",
     "speichere meine adresse ist hauptstraße 1",
     [("note_write", [("contains", "subject", "adresse")])],
     []),

    ("note-write-komplex",
     "notiere dass das meeting um 15 uhr beginnt und im raum 3 stattfindet",
     [("note_write", [("contains", "subject", "meeting")])],
     []),

    # ==========================================
    # note_read (2 Fälle)
    # ==========================================
    ("note-read",
     "was weißt du über feuerholz?",
     [("note_read", [("contains", "query", "feuerholz")])],
     [("Feuerholz", "kostet 8 euro pro kiste")]),

    ("note-read-all",
     "zeige alle notizen",
     [("note_read", [])],
     [("Feuerholz", "8 euro pro kiste")]),

    # ==========================================
    # note_delete (1 Fall)
    # ==========================================
    ("note-delete",
     "lösche die notiz feuerholz",
     [("note_delete", [("contains", "subject", "feuerholz")])],
     [("Feuerholz", "8 euro pro kiste")]),

    # ==========================================
    # NOWRITE / GATEDWRITE (2 Fälle)
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
