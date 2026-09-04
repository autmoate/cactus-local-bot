from typing import Any


def _unescape(text: str) -> str:
    import re
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), str(text))


def _clean_args(args: dict) -> dict:
    return {k: _unescape(v) if isinstance(v, str) else v for k, v in (args or {}).items()}


def _kv_pairs(text: str) -> dict:
    import re
    args: dict = {}
    for m in re.finditer(r"(\w+)\s*=\s*['\"]([^'\"]*)['\"]", text):
        args[m.group(1)] = m.group(2)
    if not args:
        for m in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', text):
            args[m.group(1)] = m.group(2)
    return args


def parse_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        name = (fn.get("name") or "").strip()
        raw = fn.get("arguments")
        if "(" in name:  # SLM-Quirk: Name+Argumente verschmolzen
            combined = f"{name}, {raw or ''}"
            real = combined.split("(", 1)[0].strip()
            if real:
                out.append({"name": real, "arguments": _clean_args(_kv_pairs(combined))})
            continue
        if not name:
            continue
        out.append({"name": name, "arguments": _clean_args(_parse_args(raw))})
    return out


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "{}")
    import json
    try:
        args = json.loads(text)
        if isinstance(args, dict):
            if len(args) == 1 and any(ch in next(iter(args)) for ch in "=(,"):
                return _kv_pairs(text)  # SLM-Quirk: ganzes KV-Bündel als Schlüssel
            return args
    except Exception:
        pass
    import ast
    try:
        args = ast.literal_eval(text)
        return args if isinstance(args, dict) else {}
    except Exception:
        pass
    import re
    return {m.group(1): m.group(2) for m in re.finditer(r"(\w+)\s*=\s*'([^']*)'", text)}


def _content_calls(content: str) -> list[dict[str, Any]]:
    """Normalisiert Modell-Output, der Calls als Text schreibt: 'list_upcoming(area='todos')'."""
    import re
    import ast
    calls = []
    for m in re.finditer(r"\b([a-z_]{3,})\s*\(([^()]*)\)", (content or "").strip(), re.I):
        args_text = m.group(2).strip()
        args: dict = {}
        if args_text:
            try:
                parsed = ast.literal_eval(f"dict({args_text})")
                args = parsed if isinstance(parsed, dict) else {}
            except Exception:
                args = {k: v for k, v in re.findall(r"(\w+)\s*=\s*['\"]([^'\"]*)['\"]", args_text)}
        calls.append({"name": m.group(1).lower(), "arguments": args})
    return calls


class GemmaInterpreter:
    def __init__(self, cactus, system_text: str = ""):
        self.cactus = cactus
        self.system_text = system_text

    def system(self, extra: str = "") -> str:
        base = (
            "Du bist 'Cactus', ein lokaler Orga-Helfer. Deutsch. "
            "Rufe das passende Tool auf (max. 2 Calls pro Antwort).\n"
            "Regeln:\n"
            "- ANLAGE/ÄNDERUNG/ERINNERUNG im Satz (erstelle, stelle ein, trage ein, merke dir, "
            "'nur noch N', verbrauche, ändere) -> immer das passende SCHREIB-Tool, nie ein Lese-Tool.\n"
            "- Schreib-Tools gelten nur für deine ORGA-Daten (Termine, To-dos, Inventar, Notizen); "
            "kreative Texte (Gedichte, Briefe, Geschichten) schreibst du direkt ohne Tool.\n"
            "- Datenfragen (Termine, To-dos, Inventar, Fakten, DB-Status) -> passendes Lese-Tool.\n"
            "- Fragen mit 'wann/hast du/hattest du/ob' sind NIE Schreibaktionen — nur suchen/listen.\n"
            "- Steht das Gesuchte schon im Kontext (z. B. 'Nächste Termine'), ist es bereits gespeichert: "
            "dann nur suchen/listen, nichts neu anlegen.\n"
            "- Allgemeinwissen/Plaudern beantwortest du selbst ohne Tool; web_search NUR bei expliziter Websuche.\n"
            "- Zeitangaben wörtlich ins Zeitfeld (due_at/start_at/in_min), NIEMALS in den Titel.\n"
            "- Rückbezug ('das', 'es', 'den Termin'): Titel aus 'Letzter Dialog'/'Zuletzt erstellt' übernehmen.\n"
            "- Nur die bereitgestellten Tools nutzen.\n"
            "Beispiele:\n"
            "Nutzer: erinnere mich in 10 min an wasser -> create_todo(title='wasser', due_at='in 10 min')\n"
            "Nutzer: erstelle eine erinnerung in 5 min hunderunde -> create_todo(title='hunderunde', due_at='in 5 min')\n"
            "Nutzer: stelle für in 10 min essen als erinnerung ein -> create_todo(title='essen', due_at='in 10 min')\n"
            "Nutzer: stelle einen timer für 15 min teewasser -> set_timer(title='teewasser', in_min=15)\n"
            "Nutzer: was steht an? -> list_upcoming(area='all')\n"
            "Nutzer: zeig alle todos -> list_upcoming(area='todos')\n"
            "Nutzer: nur noch 4 säcke grillkohle -> update_inventory(title='grillkohle', quantity=4)\n"
            "Nutzer: verbrauche 2 flaschen öl -> consume_inventory(title='öl', quantity=2)\n"
            "Nutzer: wir haben 3 gläser marmelade verbraucht -> consume_inventory(title='marmelade', quantity=3)\n"
            "Nutzer: kannst du für kommende woche sa. ab 12:30 kindergeburtstag eintragen? -> "
            "create_calendar_event(title='kindergeburtstag', start_at='kommende woche sa. 12:30')\n"
            "Nutzer: lege das meeting am mittwoch in den kalender -> "
            "create_calendar_event(title='meeting', start_at='mittwoch')\n"
            "Nutzer: ändere das bitte auf 23:20 -> update_todo(title=<Titel aus 'Zuletzt erstellt'>, due_at='23:20')\n"
            "Nutzer: wann hast du X eingetragen? -> search_records(table='calendar_events', query='X')\n"
            "Nutzer: hattest du X vermerkt? -> search_records(table='todos', query='X')\n"
            "Nutzer: haben wir noch grillkohle? -> search_records(table='inventory', query='grillkohle')\n"
            "Nutzer: merk dir: feuerholz kostet 8 euro -> add_knowledge(title='feuerholz', body='feuerholz kostet 8 euro')\n"
            "Nutzer: was hast du über mich gelernt? / was weißt du über mich? -> list_facts\n"
            "Nutzer: status deiner datenbank -> db_stats\n"
            "Nutzer: suche im web nach X -> web_search(query='X')"
        )
        if self.system_text:
            base += "\n" + self.system_text
        if extra:
            base += "\n" + extra
        return base

    def english_instruction(self, text: str, context: str = "") -> str:
        prompt = (
            "Formuliere die Nutzeranfrage als eine kurze, englische Tool-Anweisung für einen "
            "Struktur-Classifier und endet mit dem Aufruf des passenden Werkzeugs samt Argumenten. "
            "Nur die Anweisung, keine Erklärung.\n"
            f"Kontext:\n{context or 'kein Kontext'}\nNutzer: {text}"
        )
        return self.cactus.complete(prompt, context=None).strip()

    def draft(self, text: str, context: str = "", history: list[dict] | None = None) -> list[dict[str, Any]]:
        messages = [{"role": "system", "content": self.system(context)}]
        for h in history or []:
            messages.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        messages.append({"role": "user", "content": text})
        message = self.cactus.function_call(messages, self.tool_schemas,
                                            temperature=0.2, max_tokens=220)
        calls = parse_tool_calls(message)
        if not calls:
            calls = _content_calls(message.get("content"))
        known = {s["function"]["name"] for s in self._schemas}
        return [{"name": c["name"], "arguments": _clean_args(c["arguments"])}
                for c in calls if c["name"] in known]

    @property
    def tool_schemas(self) -> list[dict]:
        return self._schemas

    def bind(self, schemas: list[dict]) -> "GemmaInterpreter":
        self._schemas = schemas
        return self
