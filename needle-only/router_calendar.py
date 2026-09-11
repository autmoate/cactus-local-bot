#!/usr/bin/env python3
"""Calendar-vNext Router: deterministische Triage vor Needle-FT.

Der Router trifft eine GROBE Entscheidung (Write / Read / Reminder / None),
das task-spezifische Needle-FT-Modell extrahiert danach die Argumente.

Der Router ist bewusst einfach gehalten (String-Matching auf Trigger),
weil er nur eine grobe Triage machen soll — nicht die Argument-Extraktion.

Priorität:
  1. REMINDER  ("erinnere mich", "weck mich")
  2. READ      (Fragen, Anzeigen: "was steht an", "wann", "zeige")
  3. WRITE     (imperative Verben: "trag ein", "verschieb", "sag ab")
  4. FALLBACK  (Datum/Zeit-Muster im Text → deklarativer Create)
  5. NONE      (kein Kalender-Intent)
"""
from __future__ import annotations

import re

# --- Reminder-Trigger (höchste Spezifität) ---
REMINDER_PATTERNS = [
    r"\berinner(?:e|n)?\b",
    r"\berinnerung\b",
    r"\bweck(?:e)?\s+mich\b",
]

# --- Read-Trigger: Fragen und Listen-Anfragen ---
READ_PATTERNS = [
    r"\bsteht\s+an\b",
    r"\bsteht\s+.{1,30}\s+an\b",
    r"\bzeige\b",
    r"\bwelche\s+termine\b",
    r"\bwas\s+(habe\s+ich|kommt|ist\s+geplant|steht)\b",
    r"\bwann\s+(ist|hat|haben|sind|finden|läuft)\b",
    r"\bwann\s+\S+\s+frei\b",
    r"\bkalender\b.*\b(anzeigen|zeigen|abrufen|öffnen|les)\b",
    r"\btermine?\b.*\b(anzeigen|zeigen|abrufen|auflisten|les)\b",
    r"\bfrei\b.*\b(zeit|slots?)\b",
    r"\bliste\b.*\btermine\b",
]

# --- Write-Trigger: imperative Verben ---
WRITE_PATTERNS = [
    r"\btrag(?:e)?\b.*\b(ein|an)\b",
    r"\bers?tell(?:e)?\b",
    r"\bplane?\b",
    r"\bverschieb(?:e)?\b",
    r"\bschieb(?:e)?\b",
    r"\bsag\s+.+?\s+ab\b",         # "sag den Termin Zahnarzt ab"
    r"\bstreich(?:e)?\b",
    r"\blösch(?:e)?\b",
    r"\bänder(?:e)?\b",
    r"\bzieh(?:e)?\b.*\bvor\b",
    r"\bnimm\s+\S+\s+(auch\s+)?mit\b",
    r"\bsetz(?:e|z)?\b.*\b(an|ein)\b",
    r"\bsteck(?:e)?\b.*\b(ein|an)\b",
    r"\bmach(?:e)?\b.*\btermin\b",
]

# --- Fallback: Datum/Zeit-Muster (für deklarative Creates) ---
DATETIME_PATTERNS = [
    r"\bmorgen\b",
    r"\bübermorgen\b",
    r"\buebermorgen\b",
    r"\bheute\b",
    r"\bmontag\b",
    r"\bdienstag\b",
    r"\bmittwoch\b",
    r"\bdonnerstag\b",
    r"\bfreitag\b",
    r"\bsamstag\b",
    r"\bsonntag\b",
    r"\bum\s+\d{1,2}\s*uhr\b",
    r"\bum\s+halb\b",
    r"\bhalb\s+\w+\b",
    r"\bam\s+\d{1,2}\.\d{1,2}\.",
    r"\b\d{1,2}:\d{2}\b",
    r"\bvormittag\b",
    r"\bnachmittag\b",
    r"\babend\b",
]


def _matches_any(text: str, patterns: list) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def route_calendar(text: str) -> str:
    """Route a user query to the correct task model.

    Priority: REMINDER > READ > WRITE > FALLBACK (datetime) > NONE
    """
    # 1. Reminder (most specific intent)
    if _matches_any(text, REMINDER_PATTERNS):
        return "reminder"

    # 2. Read (questions, show/list requests)
    if _matches_any(text, READ_PATTERNS):
        return "calendar_read"

    # 3. Write (imperative commands)
    if _matches_any(text, WRITE_PATTERNS):
        return "calendar_write"

    # 4. Fallback: if the text contains datetime patterns, it's likely
    #    a declarative create ("Morgen um 14 Uhr Zahnarzt.")
    if _matches_any(text, DATETIME_PATTERNS):
        return "calendar_write"

    return "none"
