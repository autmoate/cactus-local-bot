# local-calendar

Minimaler, vollständig lokaler Kalender-Agent für den Raspberry Pi 5 (8 GB, ARM64):
**Needle 2** (45M, strukturierte Tool-Calls) + optional **Gemma 4 E2B** über die
Cactus Engine (Normalisierung/Repair). Kein Cloud-Aufruf, kein Fine-Tuning, kein
Agent-Framework — Gradio-UI mit vollständigem Trace statt Chat-Blackbox.

```
User (de/en) ──▶ [hybrid: Gemma → kanonische EN-Instruction]
                       │
                       ▼
              Needle complete()  (5 Tools, Grammar-constrained)
                       │  confidence + ungrounded sichtbar
                       ▼
        Python-Resolver (symbolisch → konkrete Zeiten, deterministisch)
                       ▼
        Verify (Titel gefunden? Kollision?) ──fehl──▶ Repair-Loop (max 3)
                       ▼
                  Execute (SQLite)
```

## Install (Raspberry Pi 5)

```sh
# Voraussetzungen: Python 3.12, uv; cactus serve läuft bereits (oder siehe unten)
cd needle-only
uv sync
```

Die App **lädt keine Modelle herunter**. Erwartet werden:

- **Needle 2**: Python-Paket `cactus-needle` + Engine-Binary, das beim ersten
  `Needle(...)` einmalig nach `~/.cache/cactus-needle/` geladen wird.
- **Gemma 4 E2B**: der bereits laufende `cactus serve`
  (`cactus serve ~/.cache/cactus/weights/gemma-4-e2b-it-cq4 --host 127.0.0.1 --port 8080`).
  Pfad/URL steuerbar per Env (`CACTUS_BASE_URL`, Default `http://127.0.0.1:8080/v1`).
  Ist serve nicht erreichbar, läuft der Rest weiter (Trace vermerkt es).

## Start

```sh
uv run local-calendar                 # hybrid (Gemma + Needle), 0.0.0.0:7860
uv run local-calendar --mode needle   # needle-only, ohne Gemma (schnellster Pfad)
uv run local-calendar --port 7860 --db data/calendar.db   # Optionen
```

Die UI ist im LAN erreichbar (0.0.0.0), aber nicht öffentlich freigegeben.

## Die 4 Tabs

1. **Assistant** — Anfrage eingeben, live kompakter Trace: Gemma-Normalisierung,
   Tool-Wahl (+Args, Confidence, `ungrounded`), Zeit-Auflösung, Verification,
   Execution, Latenzen.
2. **Calendar** — Wochenraster (30 min, ganztägige Events oben), Teilnehmer-Filter
   (kombinierte Kalender), Beschäftigt/Frei-Balken pro Person, Event-Tabelle.
3. **Trace / Debug** — vollständiger Roh-Trace der letzten 10 Requests (JSON + Klartext).
4. **Needle Lab** — Needle isoliert: `complete` / `run` / `extract`, Toolsets
   zuschalten, echte Schemas anzeigen, Presets, Gemma-Vor-Normalisierung testbar.

## Tests & Eval

```sh
uv run pytest                          # 23 Unit + Needle-Integration + E2E-Suite
uv run python tests/test_e2e.py        # CLI-Eval mit Detail-Output
uv run python tests/test_e2e.py --repeat 3   # Varianz messen
uv run python tests/test_e2e.py --hybrid     # Gemma-Pfad messen
```

Unit-Tests laufen ohne Modelle (temporäre SQLite-DBs): DST, Monats-/Jahresgrenze,
Schaltjahr, halboffene Intervalle, All-Day/Mehrtägig, Kollisionen, freie Slots.

## Architektur (Kurzform)

| Datei | Aufgabe |
|---|---|
| `src/local_calendar/calendar.py` | Domäne: Pydantic-Modelle, SQLite (direkt, kein ORM), CRUD, Overlap, freie Slots, Zeit-Auflösung, Rendering |
| `src/local_calendar/agent.py` | Needle-Tool-Definitionen, Gemma-HTTP-Client, Orchestrator-Loop (`gemma_normalize → needle_complete → resolve → verify → repair → execute`), Trace |
| `src/local_calendar/app.py` | nur Gradio-Zusammenbau + Start |
| `src/local_calendar/tabs/*.py` | je ein Tab, keine Fachlogik |
| `data/calendar.db` | SQLite (events, event_participants, participants) |

Speichermodell: halboffene Intervalle `[start, end)`; All-Day = Datum 00:00 bis
exklusiv Folgetag 00:00 (mehrtägig entsprechend); `until` ist inklusiv und wird
+1 Tag gespeichert. Zwei Kinds: `appointment` (terminiert, kollidiert ±30 min mit
Terminen) und `absence` (ganztägig, kollidiert nie) — Urlaub ist eine Absence.

## Gemessene Erkenntnisse (Basis Needle 2, ohne Fine-Tuning)

- **Tool-Accuracy** ist gut (E2E-Suite 80–95 % je nach Modus); Argumente werden
  überwiegend korrekt extrahiert, Mehrfach-Calls pro Person werden gemergt.
- **Confidence ist unkalibriert auf dieser Domäne**: abgelehnte (off-topic) Requests
  scoren hoch (~0.99), valide Calls teils sehr niedrig (~0.00–0.5). Das Floor-Default
  ist deshalb 0; `NEEDLE_CONFIDENCE_FLOOR`/`NEEDLE_CONFIDENCE_THRESHOLD` sind
  konfigurierbar, alle Werte sind im Trace sichtbar.
- **Das Modell rechnet Datumsangaben selbst und dabei regelmäßig falsch**
  (z. B. „am 9.9." → heutiges Datum als ISO). Fix: ein explizites Datum im
  Anfragetext gewinnt deterministisch über das Modell-Datum (Python rechnet,
  nicht das Modell); vergangene Daten rollen aufs Folgejahr und werden mit
  Jahreszahl ausgewiesen.
- **Trailing-Satzzeichen** („.") am Query-Ende lösen Refusals aus — wird gestript.
- **Mehrtägige Absences** extrahiert das Basismodell nur teils zuverlässig
  (Enddatum landet mitunter im falschen Feld; der Resolver toleriert das).
- **Hybrid (+Gemma)** stabilisiert deutsche Umgangssprache (95 % vs ~80 %), kostet
  auf dem Pi aber ~7 s/Request gegenüber ~1 s needle-only. Beide Modi sind bewusst
  je startbar (`--mode`).
- Repair-Schleife greift jetzt auf allen drei Pfaden (leere Calls, niedrige
  Confidence, **fehlgeschlagene Ausführung**) mit der exakten Fehlermeldung an
  Gemma (max. 3, danach Rückfrage) — Plan §24.
- Die kanonische Gemma-Instruction muss die Intent-Wörter (löschen/entfernen)
  bewahren — im Prompt explizit verankert, sonst erzeugt „Termin X löschen"
  ein CREATE statt eines DELETE (war ein echter Bug, über Repair nicht fangbar).

## Bekannte Limitationen

- Reminder/Tasks sind bewusst nicht im V1-Toolset (nur appointment/absence).
- Englische „Show my appointments“-Formulierungen routen gelegentlich auf
  `calendar_find_slot` — Varianz des Basismodells, sicher abgefangen (Rückfrage).
- `cactus serve` muss für den Hybrid-Modus separat laufen; die App startet es nicht
  selbst (kein unbeaufsichtigter Modell-Start/Download).
- **Gradio-Share-Link**: das von Gradio gebündelte `frpc` (arm64 v0.3) verbindet
  sich zwar, sein Datenkanal liefert aber in manchen Netzen keine Requests
  (kontrolliert auch mit einer Hello-World-App reproduzierbar). Die App verifiziert
  den Share-Link deshalb beim Start selbst und zeigt im Fehlerfall die LAN-URL —
  **`http://<pi-ip>:7860`** ist der zuverlässige Weg zum Testen vom zweiten Gerät
  (App lauscht auf 0.0.0.0, keine öffentliche Freigabe).
