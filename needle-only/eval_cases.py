"""Eval-Fälle v5.3 „CRUD": 7 Tools, CRUD-Semantik, Hard-Deletes.
Format: (name, input_text, expected_calls_or_string, seeds).
Seeds: [(title, value)] — ISO = Termin, 'R iso' = Reminder, text = Notiz."""

CASES = [
    # ==========================================
    # calendar_create
    # ==========================================
    ("cal-create-absolut",
     "erstelle einen termin zahnarzt am 10.9.2026 um 10 uhr",
     [("calendar_create", [("eq", "kind", "appointment")])],
     []),

    ("cal-create-erinnerung",
     "erinnerung wasser trinken in 2 minuten",
     [("calendar_create", [("eq", "kind", "reminder")])],
     []),

    ("cal-create-aufgabe",
     "aufgabe bericht schreiben bis freitag",
     [("calendar_create", [("eq", "kind", "task")])],
     []),

    # ==========================================
    # calendar_edit
    # ==========================================
    ("cal-edit-verschieben",
     "verschiebe zahnarzt auf 11:30",
     [("calendar_edit", [("contains", "title", "zahnarzt")])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    # ==========================================
    # calendar_read
    # ==========================================
    ("cal-read-all",
     "was steht diese woche an?",
     [("calendar_read", [])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    ("cal-read-reminders",
     "zeige meine erinnerungen",
     [("calendar_read", [("eq", "kind", "reminder")])],
     [("Wasser trinken", "R 2026-09-08T09:00:00+02:00")]),

    # ==========================================
    # calendar_delete
    # ==========================================
    ("cal-delete",
     "lösche zahnarzt",
     [("calendar_delete", [("contains", "title", "zahnarzt")])],
     [("Zahnarzt", "2026-09-10T10:00:00+02:00")]),

    # ==========================================
    # note_write
    # ==========================================
    ("note-write",
     "merk dir feuerholz kostet 8 euro",
     [("note_write", [("contains", "subject", "feuerholz")])],
     []),

    # ==========================================
    # note_read
    # ==========================================
    ("note-read",
     "was weißt du über feuerholz?",
     [("note_read", [("contains", "query", "feuerholz")])],
     []),

    # ==========================================
    # note_delete
    # ==========================================
    ("note-delete",
     "lösche die notiz feuerholz",
     [("note_delete", [("contains", "subject", "feuerholz")])],
     [("Feuerholz-Notiz", "8 euro pro kiste")]),

    # ==========================================
    # NOWRITE / GATEDWRITE
    # ==========================================
    ("nowrite-allgemeinwissen",
     "wer hat das internet erfunden?",
     "NOWRITE",
     []),

    ("gated-komplex",
     "plane eine reise nach japan für nächste woche",
     "GATEDWRITE",
     []),
]
