"""Gemma-first NLU: Gemma 4 E2B-it via Cactus als Frame-Interpreter.

Ersetzt die Regex-NLU von v6 durch Gemma Function Calling:
Gemma wählt das Tool und extrahiert die Argumente (Sprache),
der Code validiert deterministisch nach (Semantik).

Der Tool-Call wird auf v6-Frames gemappt:
  calendar_write  → WriteFrame(event)   → Planner
  calendar_edit   → WriteFrame(move)    → Planner
  calendar_delete → WriteFrame(cancel)  → Planner
  commitment_write→ WriteFrame(commit)  → Planner
  fact_write      → WriteFrame(fact)    → Planner
  calendar_read   → ReadFrame(schedule) → QueryEngine
  query_answer    → ReadFrame(query)    → QueryEngine
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import requests

from format import fmt_dt

# =====================================================================
# Tool-Schemas
# =====================================================================

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "calendar_write",
            "description": (
                "Create or add a calendar entry. This is the DEFAULT tool for "
                "any calendar-related statement like 'Zahnarzt morgen um 14 Uhr', "
                "'Erinnere mich in 10 Minuten an Wasser', "
                "'Urlaub von 15.9. bis 18.9.', 'Bericht schreiben bis Freitag'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": (
                            "Entry title WITHOUT time info, "
                            "e.g. 'Zahnarzt', 'Wasser trinken', 'Urlaub', 'Mittagessen'"
                        ),
                    },
                    "when": {
                        "type": "string",
                        "description": (
                            "Raw time expression from user text, VERBATIM. "
                            "E.g. 'morgen um 14 Uhr', '9.9. 12Uhr', "
                            "'in 10 Minuten', 'Freitag 10:00'"
                        ),
                    },
                    "end_when": {
                        "type": "string",
                        "description": "End time for multi-day entries, e.g. '18.9.'",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["appointment", "reminder", "task", "absence"],
                        "description": "Entry type",
                    },
                    "owner": {
                        "type": "string",
                        "description": (
                            "Who the entry is FOR, e.g. 'Lisa' in 'Termin für Lisa'"
                        ),
                    },
                    "participants": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "People participating in the event",
                    },
                    "location": {
                        "type": "string",
                        "description": "Where the event takes place",
                    },
                    "note": {
                        "type": "string",
                        "description": "Additional free-text details",
                    },
                },
                "required": ["subject", "when", "kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_edit",
            "description": (
                "Move an EXISTING calendar entry to a new time. "
                "Only use when user explicitly says 'verschiebe', "
                "'ändere', 'schiebe auf', or similar. "
                "German examples: 'Verschiebe Zahnarzt auf 16 Uhr', "
                "'Zahnarzt auf morgen 10 Uhr', 'Ändere den Termin auf Mittwoch'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Which entry to modify (title from user text)",
                    },
                    "when": {
                        "type": "string",
                        "description": "New time expression, VERBATIM",
                    },
                    "location": {
                        "type": "string",
                        "description": "New location",
                    },
                    "note": {
                        "type": "string",
                        "description": "New note",
                    },
                },
                "required": ["subject", "when"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_delete",
            "description": (
                "Delete a calendar entry. German examples: "
                "'Sage den Zahnarzt ab', 'Lösche den Termin Zahnarzt', "
                "'Streiche Urlaub'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Which entry to delete",
                    },
                },
                "required": ["subject"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_read",
            "description": (
                "Read calendar entries. German examples: "
                "'Was habe ich am Mittwoch?', 'Termine diese Woche?', "
                "'Habe ich Termine?', 'Wann ist Zahnarzt?', "
                "'Zeig meine Termine für Freitag'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "when": {
                        "type": "string",
                        "description": "Time range to query, VERBATIM",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Specific entry title to look up",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["appointment", "reminder", "task", "absence", "all"],
                        "description": "Filter by entry type (default: all)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commitment_write",
            "description": (
                "Store who brings/does what. German examples: "
                "'Julia bringt den Beamer', 'Jana übernimmt den Beamer', "
                "'Max macht den Kuchen'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person": {
                        "type": "string",
                        "description": "Who commits, e.g. 'Julia'",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["bring", "takeover", "retract"],
                        "description": (
                            "bring: person brings item; "
                            "takeover: person takes over from someone else; "
                            "retract: person no longer brings item"
                        ),
                    },
                    "item": {
                        "type": "string",
                        "description": "What they commit to, e.g. 'Beamer'",
                    },
                    "when": {
                        "type": "string",
                        "description": "Optional time context, VERBATIM",
                    },
                },
                "required": ["person", "action", "item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fact_write",
            "description": (
                "Store a fact or piece of information. German examples: "
                "'Der WLAN-Code ist geheim123', "
                "'Merk dir: Das Passwort lautet xyz'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {
                        "type": "string",
                        "description": "Fact name/key, e.g. 'WLAN-Code'",
                    },
                    "value": {
                        "type": "string",
                        "description": "Fact value, e.g. 'geheim123'",
                    },
                },
                "required": ["subject", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_answer",
            "description": (
                "Answer read-queries about facts, commitments, history, or schedule. "
                "German examples: 'Wie lautet der WLAN-Code?' (fact), "
                "'Wer bringt den Beamer?' (commitment), "
                "'Wer hat den Beamer ursprünglich gebracht?' (history), "
                "'Was habe ich am Mittwoch?' / 'Habe ich Termine?' (schedule)"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["fact", "commitment", "history", "schedule"],
                        "description": (
                            "fact: look up a stored fact (Wie lautet X?); "
                            "commitment: who brings/does X (Wer bringt X?); "
                            "history: who ORIGINALLY did X, or when did X happen "
                            "(Wer hat X ursprünglich ...?); "
                            "schedule: what calendar entries exist at time X "
                            "(Was habe ich X? / Habe ich Termine?)"
                        ),
                    },
                    "subject": {
                        "type": "string",
                        "description": (
                            "Subject of the query WITHOUT time info, "
                            "e.g. 'WLAN-Code', 'Beamer'. "
                            "For schedule queries: leave empty, use 'when' instead"
                        ),
                    },
                    "when": {
                        "type": "string",
                        "description": (
                            "Time reference for schedule queries, VERBATIM. "
                            "E.g. 'am Mittwoch', 'morgen', 'diese Woche', 'heute'"
                        ),
                    },
                },
                "required": ["query_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_command",
            "description": (
                "Execute a system command. German examples: "
                "'Undo', 'Rückgängig', 'Status', 'Hilfe'"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["undo", "status", "help", "unclear"],
                        "description": "System command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


# =====================================================================
# System-Prompt
# =====================================================================

def system_prompt() -> str:
    now = datetime.now()
    weekday = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
               "Freitag", "Samstag", "Sonntag"][now.weekday()]
    return (
        f"Du bist 'needle', ein deutscher Kalender- und Orga-Assistent.\n"
        f"Heute ist {weekday}, der {now.strftime('%d.%m.%Y')}, "
        f"{now.strftime('%H:%M')} Uhr (Europe/Berlin).\n\n"
        f"Regeln:\n"
        f"- Zeitangaben WÖRTLICH ins 'when'-Feld übernehmen, "
        f"NIEMALS selbst ein Datum ausrechnen.\n"
        f"- Bei Listen (mehrere Termine in einer Nachricht): "
        f"Tool JE ELEMENT aufrufen.\n"
        f"- Fragen (was/wer/wann/wie) sind READ-Anfragen, nie WRITE.\n"
        f"- Wenn die Anfrage unklar ist: control_command(command='unclear').\n"
        f"- Nutze nur die bereitgestellten Tools.\n\n"
        f"Beispiele:\n"
        f"- 'Zahnarzt morgen um 14 Uhr' → "
        f"calendar_write(subject='Zahnarzt', when='morgen um 14 Uhr', "
        f"kind='appointment')\n"
        f"- 'Was habe ich am Mittwoch?' → "
        f"query_answer(query_type='schedule', when='am Mittwoch')\n"
        f"- 'Habe ich Termine?' → "
        f"query_answer(query_type='schedule', when='heute')\n"
        f"- 'Wie lautet der WLAN-Code?' → "
        f"query_answer(query_type='fact', subject='WLAN-Code')\n"
    )


# =====================================================================
# GemmaNLU
# =====================================================================

class GemmaNLU:
    """NLU-Adapter: Text → Gemma-FC → geparste Tool-Calls."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1"):
        self.base_url = base_url.rstrip("/")
        self._model_id: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def interpret(self, text: str) -> list[dict[str, Any]]:
        """Text → Liste von Tool-Calls.

        Rückgabe: [{"name": ..., "arguments": {...}}, ...]
        Leere Liste = keine Tools erkannt (z. B. Smalltalk).
        """
        messages = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": text},
        ]
        try:
            message = self._function_call(messages, TOOL_SCHEMAS,
                                          temperature=0.1, max_tokens=500)
        except Exception as exc:
            return [{"name": "error", "arguments": {"message": str(exc)}}]

        calls = self._parse_tool_calls(message)

        # Fallback: Modell schreibt Calls als Text
        if not calls:
            content = message.get("content", "")
            calls = self._content_calls(content)

        known = {s["function"]["name"] for s in TOOL_SCHEMAS}
        return [c for c in calls if c.get("name") in known]

    # ------------------------------------------------------------------
    # Cactus FC Endpoint
    # ------------------------------------------------------------------

    def _model_id_or_default(self) -> str:
        if self._model_id is None:
            try:
                r = requests.get(f"{self.base_url}/models", timeout=3)
                models = r.json().get("data", [])
                self._model_id = models[0]["id"] if models else "local"
            except Exception:
                self._model_id = "local"
        return self._model_id

    def _function_call(self, messages: list[dict],
                       tools: list[dict],
                       temperature: float = 0.1,
                       max_tokens: int = 500) -> dict:
        payload = {
            "model": self._model_id_or_default(),
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        r = requests.post(f"{self.base_url}/chat/completions",
                          json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    # ------------------------------------------------------------------
    # Tool-Call-Parsing (Gemma-FC-Quirks)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_tool_calls(message: dict) -> list[dict]:
        """Parst OpenAI-Format tool_calls + Gemma-Quirks."""
        calls = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            name = (fn.get("name") or "").strip()
            raw_args = fn.get("arguments", "{}")

            # SLM-Quirk: Name+Argumente verschmolzen
            # ("calendar_write(subject='X', when='Y')")
            if "(" in name:
                combined = f"{name}, {raw_args or ''}"
                real_name = combined.split("(", 1)[0].strip()
                args_str = "(" + combined.split("(", 1)[1]
                args = GemmaNLU._kv_pairs(args_str)
                if real_name:
                    calls.append({"name": real_name, "arguments": args})
                continue

            args = GemmaNLU._safe_parse_args(raw_args)
            if name:
                calls.append({"name": name, "arguments": args})
        return calls

    @staticmethod
    def _kv_pairs(text: str) -> dict:
        """Parst KV-Paare aus Funktionsaufruf-Strings:
        "calendar_write(subject='X', when='Y', kind='Z')"
        """
        args = {}
        for m in re.finditer(
                r"(?:^|[,(])\s*(\w+)\s*=\s*['\"]([^'\"]*)['\"]",
                text):
            args[m.group(1)] = m.group(2)
        return args

    @staticmethod
    def _safe_parse_args(raw) -> dict:
        """Robustes Argument-Parsing (JSON, Python-Dict, KV-String)."""
        if isinstance(raw, dict):
            return raw
        text = str(raw or "{}").strip()
        # JSON
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        # Python-Dict
        try:
            import ast
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, SyntaxError):
            pass
        # KV-String: "subject='Zahnarzt', when='morgen'"
        args = {}
        for m in re.finditer(r"(\w+)\s*[:=]\s*['\"]([^'\"]*)['\"]", text):
            args[m.group(1)] = m.group(2)
        return args

    @staticmethod
    def _content_calls(content: str) -> list[dict]:
        """Normalisiert Text-Output: 'calendar_write(subject='X', when='Y')'"""
        calls = []
        for m in re.finditer(
                r"\b([a-z_]{3,})\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)",
                (content or "").strip()):
            name = m.group(1).lower()
            args_text = m.group(2).strip()
            args = GemmaNLU._safe_parse_args(f"{{{args_text}}}")
            if not args and args_text:
                # "subject='X', when='Y'" → dict
                parts = re.findall(r"(\w+)\s*[:=]\s*['\"]([^'\"]*)['\"]", args_text)
                args = dict(parts) if parts else {}
            if args:
                calls.append({"name": name, "arguments": args})
        return calls
