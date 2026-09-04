from modules.timesync import format_local

_WRITE_LABEL = {
    "create_todo": "Gemerkt",
    "create_calendar_event": "Eingetragen",
    "update_todo": "Aktualisiert",
    "add_calendar_event": "Eingetragen",
    "update_event": "Termin geändert",
    "cancel_event": "Termin abgesagt",
    "add_inventory": "Erfasst",
    "add_knowledge": "Gespeichert",
    "set_timer": "Timer gestellt",
}
_REMIND = {"create_todo", "add_calendar_event", "set_timer"}


def _when(args: dict) -> str:
    from modules.timesync import format_local
    if args.get("in_min") is not None:
        return f"in {args['in_min']} min"
    when = args.get("due_at") or args.get("starts_at") or ""
    return format_local(when) if when else ""


class Reflect:
    """Lesend mitdenkender Teil: lernt aus ausgeführten Schreibaktionen
    (Graph + Fakten) und gibt kompakte Material-Meldungen zurück."""

    def __init__(self, store):
        self.store = store

    def learn(self, tool: str, args: dict, space: str = "default") -> None:
        if tool not in _WRITE_LABEL:
            return
        title = args.get("title") or args.get("name") or ""
        if not title:
            return
        try:
            self.store.add_node("benutzer", space=space)
            self.store.add_edge("benutzer", title, "hat", {"kind": tool}, space=space)
        except Exception:
            pass
        when = _when(args)
        if when:
            try:
                from modules.facts import record_fact
                record_fact(
                    self.store, "benutzer", "hat",
                    f"{_WRITE_LABEL[tool].lower()}: {title} ({when})",
                    0.8, "learned", space,
                )
            except Exception:
                pass

    def confirm(self, tool: str, args: dict) -> str | None:
        if tool not in _WRITE_LABEL:
            return None
        title = args.get("title") or args.get("name") or ""
        if not title:
            return None
        when = _when(args)
        label = _WRITE_LABEL[tool]
        msg = f"{label}: {title}" + (f" ({when})" if when else "")
        if tool in _REMIND:
            msg += ". Ich erinnere daran, wenn es fällig ist."
        return msg
