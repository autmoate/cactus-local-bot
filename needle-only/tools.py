"""Needle-only Tools v4.0 „Merge": entries-DB, Tool-Oberfläche exakt v3.1.
Tool-Descriptions sind bewährt (17/24). Keine Änderungen daran!"""
from orga import Orga


class MiniTools:
    def __init__(self, orga: Orga):
        self.orga = orga

    # ---------- Reads ----------
    def execute(self, name: str, args: dict, text: str = "") -> str:
        args = dict(args or {})
        try:
            return self._read(name, args, text)
        except Exception as exc:
            return f"FEHLER bei {name}: {str(exc)[:90]}"

    def _read(self, name: str, a: dict, text: str = "") -> str:
        if name == "list_items":
            what, horizon = str(a.get("what", "termine")).lower(), str(a.get("horizon", "woche")).lower()
            if what in ("heute", "woche", "monat"):  # Needle vertauscht gelegentlich
                what, horizon = horizon, what
            low = (text or "").lower()
            if what not in ("erinnerungen", "notizen"):
                if any(w in low for w in ("erinnerung", "todo", "remind")):
                    what = "erinnerungen"
                elif any(w in low for w in ("notiz", "wissen", "gelernt")):
                    what = "notizen"
            if what in ("erinnerung", "erinnerungen", "todos", "reminders"):
                return self.orga.list_entries("erinnerungen", horizon if horizon in ("heute", "woche", "monat") else "monat")
            if what in ("notizen", "notes"):
                return self.orga.list_notes()
            return self.orga.list_entries("termine", horizon if horizon in ("heute", "woche", "monat") else "woche")
        if name == "free_slots":
            return self.orga.free_slots(str(a.get("horizon", "woche")).lower())
        if name == "find_notes":
            query = str(a.get("query", "")).strip()
            # Fallback: "notizen"/"alle" als Query → alle Notizen listen
            if query.lower() in ("notizen", "notes", "alle", "all", "liste", "list"):
                return self.orga.list_notes()
            return self.orga.find_notes(query)
        if name == "show_status":
            return self.orga.status()
        return f"Unbekanntes Tool: {name}"

    # ---------- Writes (Plan) ----------
    def plan(self, calls: list[dict], text: str = "") -> dict:
        """calls: [{'tool': name, 'arguments': {...}}, ...] → Plan."""
        ops = [{"tool": c["tool"], "arguments": c.get("arguments") or {}} for c in calls]
        return self.orga.plan_writes(ops, text)

    def apply(self, plan: dict) -> str:
        return self.orga.apply_plan(plan)

    def build(self):
        import needle

        @needle.tool
        def list_items(what: str = "termine", horizon: str = "woche"):
            """List saved items (read). what: 'termine', 'erinnerungen' or 'notizen'; horizon: heute, woche, monat. Keywords: was steht an, zeige meine todos, erinnerungen, termine."""

        @needle.tool
        def upsert_event(title: str, start_at: str = "", end_at: str = "",
                         location: str = "", notes: str = "", participants: str = ""):
            """Create, move or edit a calendar event — if a similar event already exists it is MOVED/EDITED, otherwise created (needs date+time). Keywords: termin, kalender, verschiebe, eintragen, meeting."""

        @needle.tool
        def cancel_event(title: str):
            """Cancel an EXISTING event (soft-cancel, status->cancelled). Keywords: termin absagen, sage ab, cancel, storno."""

        @needle.tool
        def upsert_reminder(title: str, due_at: str = "", in_min: int = 0):
            """Create a reminder/timer or change its time if it already exists (needs due_at or in_min). Keywords: erinnere mich, erinnerung, timer."""

        @needle.tool
        def complete_reminder(title: str):
            """Mark an existing reminder as done. Keywords: erledigt, abhaken, done, fertig, habe ich gemacht."""

        @needle.tool
        def free_slots(horizon: str = "woche"):
            """Compute free time slots (deterministic calculation, 8-20h, >=60min). Keywords: wann habe ich zeit, freie zeiten, verfuegbarkeit."""

        @needle.tool
        def remember_note(subject: str, body: str):
            """Save a note or fact. subject = short topic noun, body = the fact. Keywords: merk dir, notiere, notiz, wissen."""

        @needle.tool
        def find_notes(query: str):
            """Search saved notes by keyword (read). Keywords: was weisst du ueber, was habe ich notiert, suche notiz."""

        @needle.tool
        def show_status():
            """Show counts of stored items (read). Keywords: status, was hast du gespeichert."""

        return [list_items, upsert_event, cancel_event, upsert_reminder,
                complete_reminder, free_slots, remember_note, find_notes, show_status]
