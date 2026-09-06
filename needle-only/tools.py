"""Needle-only Tools v5.5: 5 CRUD-Tools (Notizen entfernt).

Tools:
1. calendar_create (appointment/reminder/task/absence)
2. calendar_edit
3. calendar_read (mit person-Parameter für Gruppen-Abfragen)
4. calendar_delete
5. calendar_filter (ICS-Filter: person + zeitraum → Markdown)

Kollisions-Logik: NUR appointment+appointment (±30 min).
Abwesenheiten (absence) kollidieren mit NICHTS.
"""
from orga import Orga


class MiniTools:
    def __init__(self, orga: Orga):
        self.orga = orga

    def execute(self, name: str, args: dict, text: str = "") -> str:
        """Read-Tool ausführen."""
        args = dict(args or {})
        try:
            if name == "calendar_read":
                kind = str(args.get("kind", "all")).lower()
                horizon = str(args.get("horizon", "woche")).lower()
                person = args.get("person")
                return self.orga.calendar_read(kind=kind, horizon=horizon, person=person)
            if name == "calendar_filter":
                person = str(args.get("person", "")).strip()
                horizon = str(args.get("horizon", "woche")).lower()
                return self.orga.calendar_read(kind="all", horizon=horizon, person=person)
            return f"Unbekanntes Tool: {name}"
        except Exception as exc:
            return f"FEHLER bei {name}: {str(exc)[:90]}"

    def _execute_write(self, tool: str, args: dict) -> str:
        try:
            if tool == "calendar_create":
                return self.orga.calendar_create(
                    title=args.get("title", ""),
                    start_at=args.get("start_at"),
                    end_at=args.get("end_at"),
                    location=args.get("location", ""),
                    kind=args.get("kind", "appointment"),
                    alarm_min=args.get("alarm_min", 0),
                    notes=args.get("notes", ""))
            if tool == "calendar_edit":
                return self.orga.calendar_edit(
                    title=args.get("title", ""),
                    start_at=args.get("start_at"),
                    end_at=args.get("end_at"),
                    location=args.get("location", ""),
                    alarm_min=args.get("alarm_min"),
                    notes=args.get("notes", ""))
            if tool == "calendar_delete":
                return self.orga.calendar_delete(
                    title=args.get("title", ""),
                    start_at=args.get("start_at"))
            return f"Unbekanntes Tool: {tool}"
        except Exception as exc:
            return f"FEHLER bei {tool}: {str(exc)[:90]}"

    def build(self):
        import needle

        @needle.tool
        def calendar_create(title: str, start_at: str, end_at: str = "",
                            location: str = "", kind: str = "appointment",
                            alarm_min: int = 0, notes: str = ""):
            """CREATE a new calendar entry.
            kind='appointment': Termin (checkt Kollision mit anderen Terminen ±30 min)
            kind='reminder': Erinnerung (feuert und wird gelöscht)
            kind='task': Aufgabe
            kind='absence': Abwesenheit (Urlaub, Reise, krank) — mehrtägig, kollidiert NICHT
            start_at: ISO datetime (e.g. '2026-09-10T10:00:00')
            end_at: ISO datetime für mehrtägige Einträge (e.g. Urlaub von-bis)
            Keywords: erstelle, termin, erinnerung, aufgabe, urlaub, abwesend, verreist"""

        @needle.tool
        def calendar_edit(title: str, start_at: str = "", end_at: str = "",
                          location: str = "", alarm_min: int = 0, notes: str = ""):
            """EDIT or MOVE an EXISTING calendar entry.
            Only use this if the entry already exists!
            title: The existing entry to edit (e.g. 'Zahnarzt').
            start_at: New start time (ISO datetime). Empty = keep current.
            Keywords: verschiebe, ändere, bearbeite, move, edit"""

        @needle.tool
        def calendar_read(kind: str = "all", horizon: str = "woche", person: str = ""):
            """READ or LIST calendar entries — Markdown-Format mit Tages-Headern.
            kind: 'appointment', 'reminder', 'task', or 'all'
            horizon: 'heute', 'woche', 'monat', or 'alle'
            person: Optional person name for filtering (e.g. 'Lisa')
            Keywords: was steht an, zeige termine, kalender anzeigen, was kommt"""

        @needle.tool
        def calendar_delete(title: str, start_at: str = ""):
            """DELETE a calendar entry (Hard Delete).
            title: The entry to delete (e.g. 'Zahnarzt').
            start_at: Optional date/time (ISO) for disambiguation.
            Keywords: lösche, entferne, streiche, termin löschen"""

        @needle.tool
        def calendar_filter(person: str, horizon: str = "woche"):
            """FILTER calendar entries by person and time range (ICS-Filter).
            Returns Markdown with day headers and bullet points.
            person: Person name to filter by (e.g. 'Lisa', 'Max')
            horizon: 'heute', 'woche', 'monat', or 'alle'
            Keywords: wann hat, termine von, kalender von, person, gruppe"""

        return [calendar_create, calendar_edit, calendar_read, calendar_delete,
                calendar_filter]
