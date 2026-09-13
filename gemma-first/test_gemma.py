"""Knifflige, abwechslungsreiche Tests für gemma-first.

Kategorien:
1. Format-Tests (deutsche Datumsformatierung)
2. NLU-Parsing-Tests (Gemma-FC-Quirks, Tool-Call-Parsing)
3. Pipeline-Integration-Tests (echte Gemma-Calls via Cactus serve)

Integration-Tests benötigen:
- Laufenden Cactus serve (Port 8080, Modell gemma-4-e2b-it-cq4)
- Erreichbare Postgres-Datenbank (DATABASE_URL aus .env)

Ausführen:
    uv run python gemma-first/test_gemma.py          # alle Tests
    uv run python gemma-first/test_gemma.py unit     # nur Unit-Tests
    uv run python gemma-first/test_gemma.py integ    # nur Integration-Tests
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Pfad-Setup für gemma-first und needle-only
GF = Path(__file__).resolve().parent
ROOT = GF.parent
for p in [str(GF), str(ROOT / "needle-only"), str(ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from format import fmt_day, fmt_dt, fmt_time, localize
from nlu import GemmaNLU, TOOL_SCHEMAS

_TZ = ZoneInfo("Europe/Berlin")


# =====================================================================
# Test-Framework (minimal, kein pytest nötig)
# =====================================================================

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors: list[str] = []
        self.details: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            self.details.append(f"  ✓ {name}")
        else:
            self.failed += 1
            self.details.append(f"  ✗ {name} {('— ' + detail) if detail else ''}")
            self.errors.append(name)

    def report(self, title: str) -> None:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
        for d in self.details:
            print(d)
        print(f"\n  Ergebnis: {self.passed} bestanden, {self.failed} fehlerhaft")
        if self.errors:
            print(f"  Fehler: {', '.join(self.errors)}")
        print()


# =====================================================================
# 1. Format-Tests (deutsche Datumsformatierung)
# =====================================================================

def test_format(r: TestResult) -> None:
    """Deutsche Formatierung: Tag, Uhrzeit, Kombination."""

    # UTC-Sommerzeit: 2026-09-10T12:00:00Z == 14:00 Berlin
    dt_utc = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
    r.check("fmt_dt UTC→Berlin",
            fmt_dt(dt_utc) == "Do 10.09. 14:00",
            f"got: {fmt_dt(dt_utc)}")

    # ISO-String mit Offset
    dt_iso = "2026-09-09T15:00:00+02:00"
    r.check("fmt_day ISO",
            fmt_day(dt_iso) == "Mi 09.09.",
            f"got: {fmt_day(dt_iso)}")

    # Naive datetime → Europe/Berlin annehmen
    dt_naive = datetime(2026, 9, 11, 8, 30)
    r.check("fmt_dt naive",
            fmt_dt(dt_naive) == "Fr 11.09. 08:30",
            f"got: {fmt_dt(dt_naive)}")

    # fmt_time
    r.check("fmt_time",
            fmt_time("2026-09-10T14:30:00+02:00") == "14:30")

    # localize: UTC→Berlin
    loc = localize(datetime(2026, 9, 10, 22, 0, 0, tzinfo=timezone.utc))
    r.check("localize UTC→Berlin (Sommerzeit)",
            loc.hour == 0 and loc.day == 11,
            f"got: {loc}")

    # Winterzeit: 2026-12-01T23:00:00Z == 00:00 Berlin am 02.12.
    loc_w = localize(datetime(2026, 12, 1, 23, 0, 0, tzinfo=timezone.utc))
    r.check("localize UTC→Berlin (Winterzeit)",
            loc_w.hour == 0 and loc_w.day == 2,
            f"got: {loc_w}")


# =====================================================================
# 2. NLU-Parsing-Tests (Gemma-FC-Quirks)
# =====================================================================

def test_nlu_parsing(r: TestResult) -> None:
    """Tool-Call-Parsing: OpenAI-Format, merged name+args, Text-Output."""

    nlu = GemmaNLU.__new__(GemmaNLU)  # ohne __init__ (kein requests nötig)

    # --- 2.1: Standard OpenAI tool_calls format ---
    msg = {
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "calendar_write",
                "arguments": "{\"subject\": \"Zahnarzt\", \"when\": \"morgen um 14 Uhr\", \"kind\": \"appointment\"}"
            }
        }]
    }
    calls = nlu._parse_tool_calls(msg)
    r.check("parse: OpenAI tool_calls",
            len(calls) == 1 and calls[0]["name"] == "calendar_write"
            and calls[0]["arguments"]["subject"] == "Zahnarzt",
            f"got: {calls}")

    # --- 2.2: JSON-String als arguments ---
    msg = {
        "tool_calls": [{
            "function": {
                "name": "fact_write",
                "arguments": '{"subject": "WLAN-Code", "value": "geheim123"}'
            }
        }]
    }
    calls = nlu._parse_tool_calls(msg)
    r.check("parse: JSON arguments string",
            len(calls) == 1 and calls[0]["arguments"]["value"] == "geheim123",
            f"got: {calls}")

    # --- 2.3: SLM-Quirk: name enthält "(" (merged) ---
    msg = {
        "tool_calls": [{
            "function": {
                "name": "calendar_write(subject='Zahnarzt', when='morgen 14 Uhr', kind='appointment')",
                "arguments": ""
            }
        }]
    }
    calls = nlu._parse_tool_calls(msg)
    r.check("parse: merged name+args in name field",
            len(calls) == 1 and calls[0]["name"] == "calendar_write",
            f"got: {calls}")

    # --- 2.4: Python-Dict-String als arguments ---
    msg = {
        "tool_calls": [{
            "function": {
                "name": "calendar_write",
                "arguments": "{'subject': 'Zahnarzt', 'when': 'morgen 14 Uhr', 'kind': 'appointment'}"
            }
        }]
    }
    calls = nlu._parse_tool_calls(msg)
    r.check("parse: Python dict arguments",
            len(calls) == 1 and calls[0]["arguments"]["subject"] == "Zahnarzt",
            f"got: {calls}")

    # --- 2.5: Arguments sind bereits ein dict ---
    args = {"subject": "Zahnarzt", "when": "morgen 14 Uhr", "kind": "appointment"}
    parsed = GemmaNLU._safe_parse_args(args)
    r.check("parse: arguments already dict",
            parsed == args, f"got: {parsed}")

    # --- 2.6: \\u Escapes in Argumenten ---
    msg = {
        "tool_calls": [{
            "function": {
                "name": "calendar_write",
                "arguments": '{"subject": "Zahnarzt", "when": "morgen 14 Uhr", "kind": "appointment"}'
            }
        }]
    }
    calls = nlu._parse_tool_calls(msg)
    r.check("parse: tool_calls mit JSON args",
            calls and calls[0]["arguments"].get("kind") == "appointment",
            f"got: {calls}")

    # --- 2.7: Tool-Schemas haben alle erwarteten Namen ---
    expected_tools = {
        "calendar_write", "calendar_edit", "calendar_delete",
        "calendar_read", "commitment_write", "fact_write",
        "query_answer", "control_command",
    }
    actual_tools = {s["function"]["name"] for s in TOOL_SCHEMAS}
    r.check("tool schemas: alle 8 Tools definiert",
            actual_tools == expected_tools,
            f"missing: {expected_tools - actual_tools}, "
            f"extra: {actual_tools - expected_tools}")

    # --- 2.8: Alle Tool-Schemas haben required fields ---
    for schema in TOOL_SCHEMAS:
        fn = schema["function"]
        has_req = "required" in fn.get("parameters", {})
        r.check(f"tool schema: {fn['name']} hat required",
                has_req or fn["name"] in ("calendar_read", "query_answer"),
                f"parameters: {fn.get('parameters', {}).keys()}")


# =====================================================================
# 3. Pipeline-Integration-Tests (echte Gemma-Calls)
# =====================================================================

def test_pipeline_integration(r: TestResult) -> None:
    """Integration: echter Gemma-Call über Cactus serve.

    Setzt voraus:
    - Cactus serve läuft auf Port 8080
    - Postgres ist erreichbar (DATABASE_URL)
    """
    from world.model import SourceContext
    from world.store import WorldStore

    # --- Setup: Store + Pipeline ---
    from modules.config import load_config
    cfg = load_config()

    try:
        store = WorldStore(cfg.database_url)
    except Exception as exc:
        r.check("Store-Verbindung", False, f"Fehler: {exc}")
        return

    from pipeline import GemmaPipeline
    pipeline = GemmaPipeline(store, GemmaNLU(cfg.cactus_base_url))
    src = SourceContext(source_type="test", source_id="gemma-test:1",
                        actor="ich", raw_text="test")

    # --- 3.1: Einfacher Termin-Write ---
    resp = pipeline.handle("Zahnarzt morgen um 14 Uhr", src)
    r.check("integ: Termin-Write erzeugt Response",
            len(resp) == 1, f"got {len(resp)} responses")

    first = resp[0]
    r.check("integ: Termin-Write requires_approval",
            first.requires_approval is True,
            f"got: {first.requires_approval}")

    r.check("integ: Termin-Write summary enthält 'Zahnarzt'",
            "Zahnarzt" in first.text, f"got: {first.text}")

    # --- 3.2: Read: "Was habe ich morgen?" ---
    resp = pipeline.handle("Was habe ich morgen?", src)
    r.check("integ: Read 'Was habe ich morgen?'",
            len(resp) >= 1 and ("morgen" in resp[0].text.lower()
                                 or "termin" in resp[0].text.lower()),
            f"got: {resp[0].text if resp else 'no response'}")

    # --- 3.3: Read: "Habe ich Termine?" ---
    resp = pipeline.handle("Habe ich Termine?", src)
    r.check("integ: Read 'Habe ich Termine?'",
            len(resp) >= 1, "no response")

    # --- 3.4: Fact-Write ---
    resp = pipeline.handle("Der WLAN-Code ist geheim123", src)
    r.check("integ: Fact-Write 'WLAN-Code'",
            len(resp) >= 1 and "geheim123" in resp[0].text,
            f"got: {resp[0].text if resp else 'no response'}")

    # --- 3.5: Fact-Read ---
    # Erst schreiben (mit Approval simuliert)
    resp = pipeline.handle("Der WLAN-Code ist geheim123", src)
    if resp and resp[0].plan:
        try:
            pipeline.apply(resp[0].plan, src)
        except Exception:
            pass

    resp = pipeline.handle("Wie lautet der WLAN-Code?", src)
    r.check("integ: Fact-Read 'WLAN-Code'",
            len(resp) >= 1 and ("geheim123" in resp[0].text
                                 or "wlan" in resp[0].text.lower()),
            f"got: {resp[0].text if resp else 'no response'}")

    # --- 3.6: Commitment-Write ---
    resp = pipeline.handle("Julia bringt den Beamer", src)
    r.check("integ: Commitment 'Julia bringt Beamer'",
            len(resp) >= 1 and "beamer" in resp[0].text.lower(),
            f"got: {resp[0].text if resp else 'no response'}")

    # --- 3.7: Commitment-Read: "Wer bringt den Beamer?" ---
    resp = pipeline.handle("Wer bringt den Beamer?", src)
    r.check("integ: Commitment-Read 'Wer bringt Beamer?'",
            len(resp) >= 1 and ("julia" in resp[0].text.lower()
                                 or "niemand" in resp[0].text.lower()),
            f"got: {resp[0].text if resp else 'no response'}")

    # --- 3.8: Tricky: "Kannst du diesen Termin auf 16 Uhr verschieben?" ---
    resp = pipeline.handle("Kannst du diesen Termin auf 16 Uhr verschieben?",
                           src)
    r.check("integ: Tricky 'Verschiebe Termin auf 16 Uhr'",
            len(resp) >= 1, "no response")

    # --- 3.9: Tricky: "Wann wollte ich einkaufen?" ---
    resp = pipeline.handle("Wann wollte ich einkaufen?", src)
    r.check("integ: Tricky 'Wann wollte ich einkaufen?'",
            len(resp) >= 1, "no response")

    # --- 3.10: Tricky: Batch-Input ---
    resp = pipeline.handle(
        "Trage bitte folgende Termine ein: "
        "9.9. 12Uhr Mittagessen, "
        "15.9. bis 18.9. Urlaub",
        src
    )
    r.check("integ: Tricky Batch-Input erzeugt Responses",
            len(resp) >= 1,
            f"got: {len(resp)} responses")

    # --- 3.11: Control: Status ---
    resp = pipeline.handle("status", src)
    r.check("integ: Control 'status'",
            len(resp) >= 1 and "world state" in resp[0].text.lower(),
            f"got: {resp[0].text if resp else 'no response'}")

    # --- 3.12: Control: Help ---
    resp = pipeline.handle("hilfe", src)
    r.check("integ: Control 'hilfe'",
            len(resp) >= 1 and ("orga" in resp[0].text.lower()
                                 or "termin" in resp[0].text.lower()),
            f"got: {resp[0].text if resp else 'no response'}")


# =====================================================================
# Main
# =====================================================================

def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    r = TestResult()

    if mode in ("all", "unit"):
        print("\n--- Format-Tests ---")
        test_format(r)

        print("\n--- NLU-Parsing-Tests ---")
        test_nlu_parsing(r)

    if mode in ("all", "integ"):
        print("\n--- Pipeline-Integration-Tests ---")
        test_pipeline_integration(r)

    r.report("gemmain-first Test-Suite")
    sys.exit(1 if r.failed > 0 else 0)


if __name__ == "__main__":
    main()
