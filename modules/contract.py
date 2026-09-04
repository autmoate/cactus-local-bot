CAPS = {
    "add_inventory": "Inventar/Lagerbestand erfassen und verwalten",
    "create_todo": "To-dos und Erinnerungen anlegen und anstehende nennen",
    "add_calendar_event": "Termine in den Kalender eintragen",
    "add_knowledge": "Wissen dauerhaft speichern",
    "search_records": "in Terminen, To-dos, Inventar und Wissen suchen",
    "list_upcoming": "anstehende Termine/To-dos aufführen",
    "web_search": "im lokalen Web (SearXNG) suchen, falls aktiviert",
    "db_stats": "Datenbank-Status anzeigen",
}

LIMITS = "kein Cloud, kein Allgemeinwissen, keine Weltzeit (nur lokal), Web nur über die lokale Suche, wenn sie läuft"


def capability_reply(catalog_names: list[str]) -> str:
    lines = "\n".join(f"- {CAPS.get(n, n)}" for n in catalog_names)
    return (
        "Ich bin ein lokaler Orga-Helfer auf deinem Raspberry Pi (kein Cloud).\n"
        f"Ich kann:\n{lines}\n"
        f"Grenzen: {LIMITS}.\n"
        "Ich bin von selbst still und reagiere, wenn du mich fragst oder @ verwendest."
    )


def decline_reply() -> str:
    return (
        "Das liegt außerhalb meiner lokalen Kompetenz. Ich verwalte Termine, To-dos, "
        "Inventar und Wissen (suchen + anstehendes) – alles andere kann ich hier nicht leisten."
    )


def web_unavailable_reply() -> str:
    return "Websuche ist aktuell nicht verfügbar (keine lokale Such-Instanz)."
