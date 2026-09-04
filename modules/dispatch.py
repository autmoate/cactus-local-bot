import re
from dataclasses import dataclass

from modules.contract import decline_reply, web_unavailable_reply
from modules.intents import READ_KINDS, WRITE_KINDS, Intent, IntentClassifier
from modules.needle_router import Proposal
from modules.timesync import resolve_dt, to_utc_iso

COMPLETE_CONF = 0.3
WRITE_TOOLS = {"create_todo", "add_calendar_event", "add_inventory", "add_knowledge"}

_DT_PHRASE = re.compile(
    r"(um\s*)?\d{1,2}([:.]\d{2})?\s*(uhr|h)?|(über)?morgen|heute|gestern|"
    r"montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|wochenende", re.I)
_ARTICLE = re.compile(r"^(einen|eine|ein|den|der|die|das)\s+", re.I)

REQUIRED = {
    "search_records": ("table", "query"),
    "create_todo": ("title",),
    "add_calendar_event": ("title", "starts_at"),
    "add_inventory": ("name", "description"),
    "add_knowledge": ("title", "body"),
    "list_upcoming": (),
    "web_search": ("query",),
    "db_stats": (),
}

_TODO_TRIGGERS = ("create todo", "add todo", "erinnere mich an", "erinnere mich",
                  "erinner mich an", "erinner mich", "erinnere an", "remind me to")
_CAL_TRIGGERS = ("add calendar event", "add calendar", "termin anlegen", "termin eintragen",
                 "vereinbare einen termin", "vereinbare", "plane einen termin", "plane termin",
                 "neuen termin", "einen termin", "lege einen termin")
_KNOW_TRIGGERS = ("add knowledge", "merke dir", "merken dass", "speichere in mein wissen",
                  "speichere das wissen", "wissen speichern", "merke dir gut")
_INV_TRIGGERS = ("add inventory", "inventar aufnehmen", "lager aufnehmen", "neues inventar",
                 "zum inventar hinzu", "ins inventar")
_SEARCH_TRIGGERS = ("suche nach", "suche", "search for", "search", "durchsuche",
                    "finde", "such mir", "finde mir")

_TAG_RE = re.compile(r"^@[^ ]*\s*", re.I)


@dataclass
class Decision:
    action: str  # act | clarify | silent | escalate | decline | meta
    proposal: Proposal | None = None
    message: str | None = None


def is_tagged(text: str) -> bool:
    low = text.lower().lstrip().rstrip("?!")
    return low.startswith(("@", "@cactus", "cactus ", "cactus,"))


def strip_tag(text: str) -> str:
    return _TAG_RE.sub("", text.lstrip()).strip()


def _strip_triggers(text: str, triggers) -> str:
    low = text.lower()
    for t in sorted(triggers, key=len, reverse=True):
        if t in low:
            return " ".join(text[low.index(t) + len(t):].split()).strip(" ?!.:")
    return text.strip(" ?!.:")


def _table_of(low: str) -> str:
    if any(m in low for m in ("todo", "aufgabe", "erinner")):
        return "todos"
    if any(m in low for m in ("termin", "kalender", "meeting", "veranstaltung", "versammlung")):
        return "calendar_events"
    if any(m in low for m in ("inventar", "lager", "bestand")):
        return "inventory"
    return "knowledge"


def _complete(proposal: Proposal) -> bool:
    return all(proposal.arguments.get(k) for k in REQUIRED.get(proposal.tool, ()))


def _clean_title(s: str, due_resolved: bool) -> str:
    out = _DT_PHRASE.sub(" ", s) if due_resolved else s
    out = " ".join(out.split()).strip(" ,;")
    return _ARTICLE.sub("", out).strip() or out


def _sy_write_title(intent: Intent, rest: str):
    due = resolve_dt(rest) if rest else None
    if due:
        return _clean_title(rest, True)[:80], to_utc_iso(due)
    return rest[:80], ""


def _synth_write(intent: Intent, text: str):
    if intent.kind == "todo_write":
        title = _strip_triggers(text, _TODO_TRIGGERS)
        if not title:
            return None
        clean, due = _sy_write_title(intent, title)
        return "create_todo", {"title": clean, "notes": "", "due_at": due}
    if intent.kind == "calendar_write":
        rest = _strip_triggers(text, _CAL_TRIGGERS)
        if not rest:
            return None
        clean, start = _sy_write_title(intent, rest)
        return "add_calendar_event", {"title": clean,
                                      "starts_at": start if start else rest[:80], "notes": ""}
    if intent.kind == "knowledge_write":
        body = _strip_triggers(text, _KNOW_TRIGGERS)
        if not body:
            return None
        return "add_knowledge", {"title": body[:60], "body": body, "source": "conversation"}
    if intent.kind == "inventory_write":
        rest = _strip_triggers(text, _INV_TRIGGERS)
        if not rest:
            return None
        return "add_inventory", {"name": rest[:80], "description": rest, "location": ""}
    return None


def route(text: str, classifier: IntentClassifier, web_enabled: bool = False) -> Decision | None:
    intent = classifier.classify(text)
    tagged = is_tagged(text)
    low = text.lower()
    kind = intent.kind

    if kind == "meta":
        return Decision("meta")
    if kind == "unclear":
        return Decision("escalate" if tagged else "silent", None)
    if kind == "off_topic":
        return Decision("decline" if tagged else "silent", None, decline_reply())
    if kind == "smalltalk":
        return Decision("escalate" if tagged else "silent", None)
    if kind == "web":
        if web_enabled:
            return Decision("act", Proposal("web_search", {"query": text.strip(" ?!.:")},
                                            1.0, "expliziter Web-Auftrag", {}))
        return Decision("decline" if tagged else "silent", None, web_unavailable_reply())
    if kind == "upcoming":
        area = "todos" if any(w in low for w in ("aufgab", "todo", "erinner")) else "calendar_events"
        horizon = "day" if any(w in low for w in ("heute", "morgen")) else \
                  ("month" if "monat" in low else "week")
        return Decision("act", Proposal("list_upcoming", {"area": area, "horizon": horizon, "limit": 10},
                                        1.0, "anstehende Einträge", {}))
    if kind == "inventory_read":
        return Decision("act", Proposal("search_records", {"table": "inventory", "query": text, "limit": 10},
                                        1.0, "Inventar", {}))
    if kind == "search_read":
        return Decision("act", Proposal("search_records", {"table": _table_of(low), "query": text, "limit": 8},
                                        1.0, "Suche", {}))
    if kind == "db_status":
        return Decision("act", Proposal("db_stats", {}, 1.0, "Status", {}))
    if kind in WRITE_KINDS:
        built = _synth_write(intent, text)
        if built:
            tool, args = built
            return Decision("act", Proposal(tool, args, 1.0, "Auftrag", {}))
        return None
    return None


def decide_with_proposal(text: str, proposal: Proposal | None,
                         threshold: float, classifier: IntentClassifier) -> Decision:
    intent = classifier.classify(text)
    if proposal is not None and proposal.has_call:
        conf = proposal.confidence
        if conf is None or conf >= threshold or (conf >= COMPLETE_CONF and _complete(proposal)):
            return Decision("act", proposal)
        return Decision("clarify", proposal, _intent_clarify(intent))
    return Decision("clarify", None, _intent_clarify(intent))


def _intent_clarify(intent: Intent) -> str:
    if intent.kind == "calendar_write":
        return "Was für ein Termin und wann? (z. B. 'lege einen Termin Meeting morgen 15:00' an)"
    if intent.kind == "todo_write":
        return "Welches To-do soll ich anlegen? (z. B. 'create todo Zahnarzt morgen 9')"
    if intent.kind == "inventory_write":
        return "Welches Inventar soll ich erfassen? (Name + Beschreibung)"
    return "Bitte präzisiere kurz, was ich tun soll."
