"""Needle-only Tools v5.3: 7 CRUD-Tools für Kalender und Notizen.
Beschreibungen mit klaren NOT-Abgrenzungen und expliziten Keywords.
Kalender-Einträge = Termine (appointment), Erinnerungen (reminder), Aufgaben (task)."""
from orga import Orga


class MiniTools:
    def __init__(self, orga: Orga):
        self.orga = orga

    def execute(self, name: str, args: dict, text: str = "") -> str:
        """Read-Tool ausführen (calendar_read, note_read)."""
        args = dict(args or {})
        try:
            if name == "calendar_read":
                kind = str(args.get("kind", "all")).lower()
                horizon = str(args.get("horizon", "woche")).lower()
                return self.orga.calendar_read(kind=kind, horizon=horizon)
            if name == "note_read":
                query = str(args.get("query", "")).strip()
                return self.orga.note_read(query=query)
            return f"Unbekanntes Tool: {name}"
        except Exception as exc:
            return f"FEHLER bei {name}: {str(exc)[:90]}"

    def plan(self, calls: list, text: str = "") -> dict:
        """Write-Tool-Calls direkt ausführen."""
        ops = [{"tool": c.get("tool"), "arguments": c.get("arguments", {})}
               for c in calls]
        results = [self._execute_write(op["tool"], op["arguments"]) for op in ops]
        return {"results": results, "ops": ops}

    def apply(self, plan: dict) -> str:
        return "\n".join(plan.get("results", []))

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
                return self.orga.calendar_delete(args.get("title", ""))
            if tool == "note_write":
                return self.orga.note_write(
                    subject=args.get("subject", ""),
                    body=args.get("body", ""))
            if tool == "note_delete":
                return self.orga.note_delete(args.get("subject", ""))
            return f"Unbekanntes Tool: {tool}"
        except Exception as exc:
            return f"FEHLER bei {tool}: {str(exc)[:90]}"

    def build(self):
        import needle

        @needle.tool
        def calendar_create(title: str, start_at: str, end_at: str = "",
                            location: str = "", kind: str = "appointment",
                            alarm_min: int = 0, notes: str = ""):
            """CREATE a new calendar entry. ALWAYS use this for time-based things:
            appointments (kind='appointment'), reminders (kind='reminder'),
            tasks (kind='task'). NOT for notes!
            start_at: ISO datetime string (e.g. '2026-09-10T10:00:00').
            Keywords: erstelle, neuer termin, erinnerung, aufgabe, lege an, meeting anlegen."""

        @needle.tool
        def calendar_edit(title: str, start_at: str = "", end_at: str = "",
                          location: str = "", alarm_min: int = 0, notes: str = ""):
            """EDIT or MOVE an EXISTING calendar entry (appointment, reminder, or task).
            Only use this if the entry already exists! NOT for creating new entries!
            title: The existing entry to edit (e.g. 'Zahnarzt').
            start_at: New start time (ISO datetime). Empty = keep current.
            Keywords: verschiebe, ändere, bearbeite, move, edit, termin verschieben, uhrzeit ändern."""

        @needle.tool
        def calendar_read(kind: str = "all", horizon: str = "woche"):
            """READ or LIST calendar entries (appointments, reminders, tasks).
            Use this for 'what's coming up' questions. NOT for notes!
            kind: 'appointment', 'reminder', 'task', or 'all'.
            horizon: 'heute', 'woche', 'monat', or 'alle'.
            Keywords: was steht an, was kommt, zeige termine, zeige kalender, termine anzeigen, erinnerungen anzeigen."""

        @needle.tool
        def calendar_delete(title: str):
            """DELETE a calendar entry (appointment, reminder, or task) permanently.
            Use this for removing events from the calendar. NOT for notes!
            title: The entry to delete (e.g. 'Zahnarzt', 'Wasser trinken').
            Keywords: lösche, streiche, entferne, termin löschen, erinnerung löschen, sage ab, absagen."""

        @needle.tool
        def note_write(subject: str, body: str):
            """WRITE or SAVE a new note or fact. NOT a calendar entry, NOT time-based!
            Use this for storing information (e.g. 'Feuerholz kostet 8 euro').
            subject: Short topic noun (e.g. 'Feuerholz').
            body: The fact or content (e.g. 'kostet 8 euro pro Kiste').
            Keywords: merk dir, notiere, speichere, notiz schreiben, notiz speichern."""

        @needle.tool
        def note_read(query: str = ""):
            """READ or SEARCH saved notes. NOT for calendar entries!
            Use this for 'what do you know about X' questions.
            query: Search term (e.g. 'feuerholz'). Empty = list all notes.
            Keywords: was weißt du, was weisst du, zeige notizen, suche notiz, notizen anzeigen, notizen durchsuchen."""

        @needle.tool
        def note_delete(subject: str):
            """DELETE a saved note permanently. NOT for calendar entries!
            Use this only for removing notes (e.g. 'lösche die notiz über feuerholz').
            subject: The note to delete (e.g. 'Feuerholz').
            Keywords: lösche notiz, entferne notiz, vergiss notiz, notiz löschen."""

        return [calendar_create, calendar_edit, calendar_read, calendar_delete,
                note_write, note_read, note_delete]
