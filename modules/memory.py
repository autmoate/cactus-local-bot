from datetime import datetime, timezone
from typing import Any

from modules.postgres_store import PostgresStore

DEFAULT_SOUL = {
    "persona": "direkt, ruhig, konkret; kein Hype, keine Floskeln, kein Geschwätz.",
    "tone": "knapp, standardmäßig 1-2 Sätze; Sprache folgt dem Nutzer (deutsch/englisch).",
    "proximity": "sachlich-freundlich in Standardsituationen, in privaten Kontexten vertrauter; pro Gruppe anpassbar.",
    "boundaries": "erfinde keine Fakten; nenne deine Grenzen ehrlich; Tool-Aktionen brauchen Bestätigung.",
    "expertise": "lokaler persönlicher Assistent: Termine, Inventar, To-dos, wiederverwendbares Wissen.",
    "limits": "kein Internet, keine echte Weltzeit außer lokaler Uhrzeit, kein Allgemeinwissen-Service.",
    "topics": "Alltag und Organisation; keine Rechts- oder Medizinberatung.",
    "anti_patterns": "nie 'kein Problem'; keine Emojis; Nutzer nicht wiederholen.",
}

DEFAULT_TEXT = (
    "Lokaler Agent auf einem Raspberry Pi 5 (8GB), vollständig offline/kein Cloud-Handoff. "
    "Sprache: Deutsch (außer englische Anfragen). Du bist knapp und still, wenn nichts zu tun ist."
)


class Memory:
    def __init__(self, store: PostgresStore):
        self.store = store
        self._seeded = False

    def ensure_defaults(self) -> None:
        if self._seeded:
            return
        self._seeded = True
        if not (self.store.get_profile("default", "soul") or {}):
            self.store.set_profile("default", "soul", DEFAULT_SOUL, "initial seed")
        self.store.get_profile("default", "user")

    def boot(self, text: str, recall_k: int = 5, space: str = "default") -> dict[str, Any]:
        self.ensure_defaults()
        now = datetime.now(timezone.utc).isoformat(timespec="minutes")
        return {
            "now": now,
            "soul": self.store.get_profile("default", "soul"),
            "user": self.store.get_profile("default", "user"),
            "recall": self.store.recall(text, recall_k, space=space),
            "facts": self.store.recall_facts(text, recall_k, space=space),
        }

    def recent_dialogue(self, limit: int = 8, space: str = "default") -> list[dict[str, Any]]:
        rows = self.store.recent_messages(limit)
        rows.reverse()
        return rows

    @staticmethod
    def as_needle_system(boot: dict[str, Any]) -> str:
        parts = [
            f"lokale Zeit (UTC): {boot['now']}",
            "Datenbereiche: todos (aufgaben/erinnerungen), inventory (lager/inventar), "
            "calendar_events (termine/kalender), knowledge (wissen).",
            "Verwaltung läuft lokal; Tool-Aufträge sind kurz und wörtlich gemeint.",
        ]
        for fact in boot["facts"][:3]:
            parts.append(f"Fakt: {fact['subject']} {fact['predicate']} {fact['object']}")
        return "\n".join(parts)

    @staticmethod
    def as_system_text(boot: dict[str, Any]) -> str:
        parts: list[str] = [DEFAULT_TEXT, f"aktuelle lokale Zeit (UTC): {boot['now']}"]
        soul = boot["soul"] or {}
        for key, value in soul.items():
            if value:
                parts.append(f"{key}: {value}")
        user = boot["user"] or {}
        if user:
            parts.append(f"Nutzerkontext: {user}")
        for fact in boot["facts"]:
            parts.append(f"Fakt: {fact['subject']} {fact['predicate']} {fact['object']} (conf {fact['confidence']:.2f})")
        for item in boot["recall"]:
            if item["role"] == "assistant":
                parts.append(f"frühere Aktion: {item['text'][:120]}")
        return "\n".join(parts)

    def record_turn(self, user_text: str, action: str, tool: str | None = None,
                    result: Any = None, reply: str | None = None, space: str = "default") -> None:
        self.ensure_defaults()
        embed_user = action not in ("silent", "decline")
        self.store.log_message("user", user_text, {"action": action, "tool": tool},
                               space=space, embed_vec=embed_user)
        if reply is not None:
            self.store.log_message("assistant", reply, {"action": "reply"}, space=space, embed_vec=False)
        elif tool is not None:
            note = f"{tool}: {str(result)[:200]}" if result is not None else tool
            self.store.log_message("assistant", note, {"action": "tool", "tool": tool}, space=space, embed_vec=False)
