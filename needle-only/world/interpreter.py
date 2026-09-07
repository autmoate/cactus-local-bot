"""Orga v6 — Interpreter: Text → Frame (via Needle).

Der Interpreter ist die EINZIGE Stelle, an der Needle aufgerufen
wird. Domain-Logik (Planner/Store/Resolver) kennen Needle nicht.

ARCHITEKTUR (nach ausführlichem Needle-Testing):
Needle 45M erkennt Tools über bekannte Parameter-Namen
(title, start_at, person, action, item). Die funktionierenden
Domänen-Tools werden in SEPARATEN Sessions betrieben:

- calendar_create: title + start_at + kind (v5.6-Schema, bewährt)
- create_commitment: person + action + item
- edit_event: title + start_at (für „Verschieb X auf Y")
- save_fact: title + notes

Alle Tools mappen auf dasselbe v6 WriteFrame.
"""
from __future__ import annotations

import re
import threading

import needle

from modules.timesync import now as tz_now

from .model import ReadFrame, WriteFrame


# =====================================================================
# Needle-Tool-Definitionen (ein Tool pro Domäne)
# =====================================================================


def _make_calendar_create():
    """Event/Termin/Erinnerung: v5.6-Schema (bewährt)."""
    @needle.tool
    def calendar_create(title: str, start_at: str,
                        kind: str = "appointment"):
        '''CREATE a calendar entry (appointment, reminder, or task).
        title: The title of the entry (e.g. 'Zahnarzt', 'Meeting')
        start_at: The date/time (e.g. 'morgen 14 Uhr', '2026-09-08T14:00')
        kind: 'appointment', 'reminder', or 'task' (default: appointment)'''

    return calendar_create


def _make_edit_event():
    """Verschieben: „Verschieb Zahnarzt auf 15 Uhr"."""
    @needle.tool
    def edit_event(title: str, start_at: str):
        '''EDIT or MOVE an existing calendar entry to a new time.
        title: The name of the entry to move (e.g. 'Zahnarzt')
        start_at: The new time (e.g. 'morgen 15 Uhr')'''

    return edit_event


def _make_create_commitment():
    """Commitment: „Julia bringt den Beamer"."""
    @needle.tool
    def create_commitment(person: str, action: str, item: str):
        '''CREATE a commitment — a person agrees to bring or do something.
        person: The person who commits (e.g. 'Julia')
        action: What they will do (e.g. 'bringt', 'bringt mit')
        item: What they will bring or do (e.g. 'Beamer', 'Kuchen')'''

    return create_commitment


def _make_save_fact():
    """Fact/Note: „Der WLAN-Code ist foo123"."""
    @needle.tool
    def save_fact(title: str, notes: str):
        '''SAVE a fact, note, or piece of information for later retrieval.
        title: A short name for the fact (e.g. 'WLAN-Code', 'Adresse')
        notes: The value or content of the fact (e.g. 'foo123', 'Musterstr. 1')'''

    return save_fact


def _make_query_tool():
    @needle.tool
    def query(subject: str = "", relation: str = "", object: str = "",
              context: str = "", when: str = ""):
        '''READ calendar entries, commitments, facts from the world.
        subject: who/what is asked about (e.g. 'Julia', 'Zahnarzt')
        relation: the verb (e.g. 'bringt', 'kommt', 'ist')
        object: what is asked about (e.g. 'Beamer')
        context: event context (e.g. 'Treffen')
        when: time filter (e.g. 'morgen', 'diese Woche', 'ursprünglich')'''

    return query


# =====================================================================
# NeedleInterpreter
# =====================================================================


class NeedleInterpreter:
    """Konvertiert Text in Write-/Read-Frames via Needle.

    Verwaltet domänen-spezifische Needle-Sessions.
    Alle Sessions mappen auf dasselbe v6 WriteFrame.
    """

    def __init__(self):
        self._system = (
            f"current date: {tz_now().strftime('%Y-%m-%d %H:%M')}. "
            f"locale: de-DE."
        )
        self._sessions: dict[str, needle.Needle] = {}
        self._lock = threading.Lock()

    def _get_session(self, domain: str) -> needle.Needle:
        """Lazy-init einer domänen-spezifischen Needle-Session."""
        with self._lock:
            if domain not in self._sessions:
                tool_map = {
                    "calendar": _make_calendar_create,
                    "edit": _make_edit_event,
                    "commitment": _make_create_commitment,
                    "fact": _make_save_fact,
                    "query": _make_query_tool,
                }
                tool_fn = tool_map[domain]()
                self._sessions[domain] = needle.Needle(
                    tools=[tool_fn], system=self._system)
            return self._sessions[domain]

    @staticmethod
    def _extract_args(resp) -> dict | None:
        """Extrahiert die Argumente aus einer Needle-Response."""
        calls = resp.get("function_calls") or []
        if not calls:
            return None
        return calls[0].get("arguments", {}) or {}

    # -- WRITE --------------------------------------------------------

    def write_frame(self, text: str) -> WriteFrame:
        """Extrahiert ein WriteFrame aus einem Satz.

        Hybrid-Ansatz:
        1. Deterministische Regex-Patterns für Commitments/Facts
           (Needle 45M ist hier unzuverlässig)
        2. Needle calendar_create für Events/Termine
        3. Needle edit_event für Verschieben
        """
        low = text.lower().strip()

        # --- Deterministische Patterns (vor Needle) ---

        # Pattern: „X bringt Y" / „X übernimmt Y" / „X bringt beim Z Y"
        m = re.search(
            r"(\w+)\s+(bringt|bringen|mitbringt|mitbringen|"
            r"übernimmt|übernehmen|besorgt|besorgen)\s+"
            r"(?:doch|auch|jetzt|dann|nun)?\s*"
            r"(.+)$",
            low)
        if m:
            subject = m.group(1).capitalize()
            relation = m.group(2)
            rest = m.group(3).strip()

            # Context extrahieren: „beim Treffen" / „zum Treffen"
            context = ""
            m_ctx = re.search(
                r"(?:beim|zum|zur|am|im|in der|in den)\s+(\S+)", rest)
            if m_ctx:
                context = m_ctx.group(1).capitalize()
                # Context aus dem Rest entfernen
                rest = (rest[:m_ctx.start()] + rest[m_ctx.end():]).strip()

            # Artikel entfernen: „den Beamer" → „Beamer"
            rest = re.sub(
                r"^(?:den|die|das|dem|einen|eine)\s+", "", rest)
            object_str = rest.capitalize() if rest else ""

            return WriteFrame(
                subject=subject,
                relation=relation,
                object=object_str,
                context=context,
            )

        # Pattern: „X kommt (doch) nicht" → retract_attend
        # WICHTIG: Vor dem allgemeinen „kommen"-Pattern prüfen!
        m = re.search(r"(\w+)\s+kommt\s+(?:doch\s+)?nicht", low)
        if m:
            return WriteFrame(
                subject=m.group(1).capitalize(),
                relation="retract_attend",
            )

        # Pattern: „Erinnere mich ..." → Reminder
        m = re.search(r"erinnere\s+mich\s+(.+)$", low)
        if m:
            return WriteFrame(
                relation="erinnern",
                when=m.group(1).strip(),
            )

        # Pattern: „Erinnerung ..." / „Stell eine Erinnerung ..."
        m = re.search(r"(?:erinnerung|stell\s+eine\s+erinnerung)\s+(.+)$", low)
        if m:
            return WriteFrame(
                relation="erinnern",
                when=m.group(1).strip(),
            )

        # Pattern: „X kommt zum/beim Y" / „X nimmt teil"
        m = re.search(
            r"(\w+)\s+(kommt|kommen|teilnimmt|nimmt teil|dabei)\s*"
            r"(?:zum|zur|beim|am|im|zu)?\s*(.+?)$",
            low)
        if m:
            return WriteFrame(
                subject=m.group(1).capitalize(),
                relation="kommen",
                context=m.group(3).strip().capitalize(),
            )

        # Pattern: „Der/die/das X ist Y" (Fact)
        # WICHTIG: Original-Casing erhalten (z.B. 'WLAN-Code')
        m = re.search(
            r"(?:der|die|das)\s+(\S+)\s+ist\s+(.+)$",
            text, re.IGNORECASE)
        if m:
            return WriteFrame(
                subject=m.group(1),
                note=m.group(2).strip(),
                relation="ist",
            )

        # Pattern: „X ist Y" (allgemeiner Fact)
        m = re.search(
            r"(\S+)\s+ist\s+(?:gleich\s+)?(.+)$",
            text, re.IGNORECASE)
        if m and len(m.group(1)) > 2:
            return WriteFrame(
                subject=m.group(1),
                note=m.group(2).strip(),
                relation="ist",
            )

        # --- Needle-basierte Patterns ---

        # Verschieben → edit_event Tool
        if any(w in low for w in ("verschieb", "änder", "ändere", "bearbeit")):
            frame = self._try_domain("edit", text)
            if frame:
                return frame

        # Erinnerung → calendar_create mit kind='reminder'
        if any(w in low for w in ("erinner", "alarm", "weck")):
            frame = self._try_domain("calendar", text)
            if frame:
                return frame

        # Standard: calendar_create für Events/Termine
        frame = self._try_domain("calendar", text)
        if frame:
            return frame

        # Commitment via Needle (Fallback)
        frame = self._try_domain("commitment", text)
        if frame:
            return frame

        # Fact via Needle (Fallback)
        frame = self._try_domain("fact", text)
        if frame:
            return frame

        return WriteFrame()

    def _try_domain(self, domain: str, text: str) -> WriteFrame | None:
        """Versucht, ein Frame aus einer Domäne zu extrahieren."""
        try:
            session = self._get_session(domain)
            resp = session.complete(text)
            args = self._extract_args(resp)
            if args is None:
                return None

            if domain == "calendar":
                title = str(args.get("title", "") or "")
                start_at = str(args.get("start_at", "") or "")
                kind = str(args.get("kind", "appointment") or "appointment")
                if not title and not start_at:
                    return None
                return WriteFrame(
                    subject=title,
                    when=start_at,
                    relation=kind,  # appointment/reminder/task
                )

            if domain == "edit":
                title = str(args.get("title", "") or "")
                start_at = str(args.get("start_at", "") or "")
                if not title and not start_at:
                    return None
                return WriteFrame(
                    subject=title,
                    when=start_at,
                    relation="verschieben",
                )

            if domain == "commitment":
                person = str(args.get("person", "") or "")
                action = str(args.get("action", "") or "")
                item = str(args.get("item", "") or "")
                if not person and not item:
                    return None
                return WriteFrame(
                    subject=person,
                    relation=action,
                    object=item,
                )

            if domain == "fact":
                title = str(args.get("title", "") or "")
                notes = str(args.get("notes", "") or "")
                if not title and not notes:
                    return None
                return WriteFrame(
                    subject=title,
                    note=notes,
                    relation="ist",
                )

            return None
        except Exception:
            return None

    # -- READ ---------------------------------------------------------

    def read_frame(self, text: str) -> ReadFrame:
        """Extrahiert ein ReadFrame aus einer Frage.

        Hybrid: deterministische Regex-Patterns für häufige
        Fragestellungen, Needle als Fallback.
        """
        low = text.lower().strip()

        # „Wer bringt X?" → relation=bring, object=X
        m = re.search(
            r"wer\s+(bringt|bringen|mitbringt|mitbringen|"
            r"übernimmt|übernehmen)\s+"
            r"(?:doch|auch|jetzt)?\s*"
            r"(?:den|die|das|dem|einen|eine)?\s*"
            r"(.+?)\s*\??$",
            low)
        if m:
            return ReadFrame(
                relation=m.group(1),
                object=m.group(2).strip(),
            )

        # „Wer sollte X (ursprünglich) bringen?" → historische Query
        m = re.search(
            r"wer\s+sollte\s+"
            r"(?:den|die|das|dem|einen|eine)?\s*"
            r"(.+?)\s+"
            r"(ursprünglich|urspruenglich|original|zuerst)?\s*"
            r"(bringen|bringt|mitbringen)\s*\??$",
            low)
        if m:
            return ReadFrame(
                relation="bring",
                object=m.group(1).strip(),
                when="ursprünglich",
            )

        # „Wie lautet X?" / „Was ist X?" / „Was ist der X?"
        m = re.search(
            r"(?:wie lautet|was ist|was war|wer ist)\s+"
            r"(?:der|die|das|den|dem)?\s*"
            r"(.+?)\s*\??$",
            low)
        if m:
            return ReadFrame(subject=m.group(1).strip())

        # „Was steht an?" / „Was steht morgen an?"
        m = re.search(
            r"was\s+steht\s+(.+?)\s+an\s*\??$",
            low)
        if m:
            return ReadFrame(relation="steht an", when=m.group(1).strip())

        # Needle-Fallback
        try:
            session = self._get_session("query")
            resp = session.complete(text)
            args = self._extract_args(resp)
            if args is None:
                return ReadFrame()
            return ReadFrame(
                subject=str(args.get("subject", "") or ""),
                relation=str(args.get("relation", "") or ""),
                object=str(args.get("object", "") or ""),
                context=str(args.get("context", "") or ""),
                when=str(args.get("when", "") or ""),
            )
        except Exception:
            return ReadFrame()
