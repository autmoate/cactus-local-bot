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

- **Eval mischt zwei Metriken**: Tool-Accuracy liegt bei ~80–90 %, aber die
  **semantische Genauigkeit** (finaler DB-Zustand: Titel, all_day, Zeitspanne,
  Teilnehmer) bei ~70 % — die E2E-Suite prüft beides (`expected`-Felder in
  `eval_cases.json`). Die alte reine Tool-Namen-Messung („90–95 %") war zu optimistisch.
- **Das Modell rechnet Datumsangaben selbst und dabei regelmäßig falsch**
  (z. B. „am 9.9." → heutiges Datum, „Freitag" → Mittwoch als ISO). Fix:
  deterministische Autoritätshierarchie — explizites Datum im Text > benannter
  Wochentag > Modell-Output (`extract_dates_from_text`, `extract_weekday_from_text`);
  zwei Datumsangaben im Text bilden eine Zeitspanne; vergangene Daten rollen
  (Create) bzw. werden unverändert gematcht (Move/Delete).
- **Keine Zeitangabe im Text → ganztägig**: das Modell erfindet Zeiten; ohne
  Zeit-Signal (HH:MM/Uhr/at/Perioden) wird die erfundene Zeit verworfen.
  Grenze: „Ich habe morgen Urlaub" ohne DD.MM bleibt timed (dokumentierte Schwäche).
- **Collision ist participant-aware**: nur gemeinsame Teilnehmer kollidieren;
  eine Absence blockiert Termine derselben Person (konsistent mit den freien
  Slots), das Anlegen einer Absence selbst kollidiert nie.
- **Confidence ist unkalibriert auf dieser Domäne**: abgelehnte (off-topic) Requests
  scoren hoch (~0.99), valide Calls teils sehr niedrig (~0.00–0.5). Das Floor-Default
  ist deshalb 0; `NEEDLE_CONFIDENCE_FLOOR`/`NEEDLE_CONFIDENCE_THRESHOLD` sind
  konfigurierbar, alle Werte sind im Trace sichtbar.
- **Trailing-Satzzeichen** („.") am Query-Ende lösen Refusals aus — wird gestript.
- **Mehrtägige Absences**: Enddatum landet mitunter im falschen Feld; der
  Resolver toleriert das, und zwei Datumsangaben im Text bilden die Zeitspanne.
- **Hybrid (+Gemma)** stabilisiert deutsche Umgangssprache (95 % Tool-Accuracy
  vs ~80 %), kostet auf dem Pi aber ~7 s/Request gegenüber ~1 s needle-only.
  Beide Modi sind bewusst je startbar (`--mode`).
- Repair-Schleife greift auf allen drei Pfaden (leere Calls, niedrige Confidence,
  fehlgeschlagene Ausführung) mit der exakten Fehlermeldung an Gemma (max. 3,
  danach Rückfrage) — Plan §24.
- Die kanonische Gemma-Instruction muss die Intent-Wörter (löschen/entfernen)
  bewahren — im Prompt explizit verankert, sonst erzeugt „Termin X löschen"
  ein CREATE statt eines DELETE (war ein echter Bug, über Repair nicht fangbar).

## Bekannte Limitationen

- Reminder/Tasks sind bewusst nicht im V1-Toolset (nur appointment/absence).
- „Ich habe morgen Urlaub" (relative Tagesangabe ohne Zeit) erzeugt noch einen
  terminierten Eintrag statt einer ganztägigen Absence — ehrlich im Eval gemessen.
- Kolloquiale Formulierungen („Nimm den Zahnarzttermin wieder raus") werden vom
  Basismodell teils verweigert — varianzbehaftet, sicher abgefangen (Rückfrage).
- `cactus serve` muss für den Hybrid-Modus separat laufen; die App startet es nicht
  selbst (kein unbeaufsichtigter Modell-Start/Download).
- Gradio-Share (`--share`) verlässt sich auf das gebündelte frpc; in manchen Netzen
  liefert sein Datenkanal keine Requests. Die App lauscht standardmäßig auf
  0.0.0.0 — **`http://<pi-ip>:7860`** ist der zuverlässige Weg vom zweiten Gerät.
