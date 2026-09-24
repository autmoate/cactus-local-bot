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

## Production: Kalender-Pin 📌 (Telegram V1, eingefroren)

Der produktive Einsatz ist ein lokaler **Telegram**-Kalenderbot. Der Modell-/
Write-Pfad ist abgeschlossen und bleibt eingefroren:

```
Telegram → Needle 2 (N2-FT seed44) → maximal EINE atomare Aktion
        → Python resolve / scope / verify / permissions
        → READ  sofort   ·   WRITE  Preview → Confirm → revalidate → SQLite
```

Kein Training, kein Gemma, kein Needle 3, kein v5-Decomposer im Produktionspfad
(Gemma/N3 bleiben Research unter `experiments/ft/`). Details, Scope/Privacy,
Deployment und Limitationen: **`docs/telegram.md`**.

Betrieb:

```sh
cd needle-only
uv sync --extra telegram
NEEDLE_WEIGHTS=experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact \
  uv run local-calendar-telegram --mode needle
```

Round-2-Prinzipien (kurz): interne Identität ist `person_id` (idempotente
Owner-Reconciliation des Legacy-„Ich"); `move`/`delete` sind strikt auf den
aktuellen Kalender gescoped; **Absence blockiert keine manuellen Termine**,
zählt aber weiterhin als busy für `find_slot`; Overlaps sind Warnungen, kein Hard
Reject; All-Day/Absence ist Header, timed Events sind pro Tag geklippte Segmente;
ein Natural-Language-Read = **eine** Query, Text und PNG aus demselben
`ReadResult`; `/day [Datum]` und `/week [Datum]` sind deterministisch; `/status`
(owner-only) zeigt Build/Modell/Schema ohne Secrets. **SQL/exakte Intervalle =
Wahrheit**, Views/Telegram = deterministische Projektion (X=Tage, Y=Zeit,
Z=Personen).

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

## Agent-Flow (aktuell)

```
User (de/en)
   ↓
Gemma Controller (CONTROLLER_SYSTEM → JSON continue/ask_user/finish)
   ↓ instruction (keine Tool-Namen — Gemma zerlegt mehrfach-Items vor Needle)
Needle complete()  → Tool + constrained Arguments (Grammar)
   ↓
Python: resolve (Zeit aus Text > Wochentag > Modell) → verify (Kollision,
Titel-Kandidaten, Hard-Filter) → execute (SQLite)
   ↓
Observation (Ergebnis + Fehler + Kalenderinhalt) → zurück an Gemma
   ↓
Gemma: continue (nächster Schritt) | ask_user (Pending pro Session) | finish
```
Safety: MAX_AGENT_STEPS=8, Loop-Guards (identische Instruction/Observation,
bereits abgeschlossene Schritte), Needle-Inference serialisiert
(threading.Lock), Pending-State pro Session (Telegram = chat_id, Gradio =
"local"), Gemma-Ausfall → dokumentierter Needle-only-Fallback.
Needle-only-Modus läuft ohne Gemma in einem einzigen Durchlauf.

Bekannte Schwäche: Gemma 4 E2B folgt dem Controller-JSON-Protokoll nur
teilweise zuverlässig — einfache Creates laufen (Iteration 1 = Instruction,
Iteration 2 = finish), aber der Controller fragt gelegentlich unnötig nach
oder beendet zu früh. Das ist ein Gemma-Planning-Fehler (Schicht 1), sichtbar
in jedem Trace (controller-Steps) — Verbesserung über die reale
Nutzungs-Feedback-Schleife (Plan §22-24), nicht über Regex.

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
+1 Tag gespeichert. Zwei Kinds: `appointment` (terminiert) und `absence`
(ganztägig) — Urlaub ist eine Absence. Round-2-Regel: Overlaps sind Warnungen
(kein Hard Reject), und eine Absence blockiert keine manuellen Termine; sie zählt
nur als busy für die Verfügbarkeit (`find_slot`).

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
- **Overlaps sind participant-aware Warnungen** (Round 2): nur gemeinsame
  Teilnehmer werden gemeldet; eine Absence blockiert keine manuellen Termine,
  zählt aber als busy in den freien Slots (zwei getrennte Konzepte).
- **Confidence ist unkalibriert auf dieser Domäne**: abgelehnte (off-topic) Requests
  scoren hoch (~0.99), valide Calls teils sehr niedrig (~0.00–0.5). Das Floor-Default
  ist deshalb 0; `NEEDLE_CONFIDENCE_FLOOR`/`NEEDLE_CONFIDENCE_THRESHOLD` sind
  konfigurierbar, alle Werte sind im Trace sichtbar.
- **Trailing-Satzzeichen** („.") am Query-Ende lösen Refusals aus — wird gestript.
- **Mehrtägige Absences**: Enddatum landet mitunter im falschen Feld; der
  Resolver toleriert das, und zwei Datumsangaben im Text bilden die Zeitspanne.
- **Hybrid (+Gemma)** stabilisiert deutsche Umgangssprache (95 % Tool-Accuracy
  vs ~80 %), kostet auf dem Pi aber ~7 s/Gemma-Call gegenüber ~1 s needle-only;
  der Controller-Loop addiert je Iteration einen Aufruf (Design-Dokumentiert,
  bewusst akzeptiert). Beide Modi sind je startbar (`--mode`).
- `calendar_list` versteht konkrete Tage/Zeiträume (auch in der Vergangenheit,
  ohne Jahres-Roll), `calendar_create` explizite Zeitbereiche (`end_time`, mit
  end>time-Verifikation) und Multi-Create-Requests (Needle multi-call bzw.
  Gemma-Zerlegung).
- **Gemma ist jetzt ein iterativer Controller** (continue / ask_user / finish):
  Mehr-Item-Anfragen werden vor Needle in einzelne Schritte zerlegt, Observationen
  (Ergebnisse, Fehler + Kalenderinhalt) fließen nach jedem Schritt zurück,
  Ambiguitäten führen zu Rückfragen, deren Antwort den Task fortsetzt
  (minimale Pending-State). Safety-Ceiling: 8 Agent-Iterationen + Loop-Guards
  (identische Instruction/Observation). Needle bleibt alleiniger Dispatcher;
  `MAX_TOOL_CALLS_PER_STEP=10`, `MAX_AGENT_STEPS=8` (env-konfigurierbar).
- Repair-Schleife greift auf allen drei Pfaden (leere Calls, niedrige Confidence,
  fehlgeschlagene Ausführung). Gemma bekommt dabei die exakte Fehlermeldung **und
  den aktuellen Kalenderinhalt** (nächste 30 Tage), korrigiert die Instruction
  auf existierende Einträge oder meldet NO_MATCH (max. 3 Loops, dann Rückfrage;
  eine unveränderte Instruction bricht den Loop frühzeitig ab) — Plan §24.
- **Traces sind persistent**: jeder Request landet append-only in
  `data/traces.jsonl` (eine JSON-Zeile pro Request, inkl. Canonical, Steps,
  Confidence, `ungrounded`, Fehler) — Grundlage für gezieltes Debugging.

## Contract-Minimization (experiments/contract_minimize.py — letzte Base-Phase)

Genau eine Frage: Kosten `horizon`/`days` als Schema-Felder Arg-Accuracy?
Minimal-Contract (list/find ohne horizon/days, Handler-Defaults unverändert)
gemessen gegen die Produktion — 21 Cases × raw/canon × 3 Repeats:
**verworfen** — args 0.375 vs 0.400, final 0.60 vs 0.65, Refusals 0.125
vs 0.05 (2.5×). Isoliert hilft Minimal leicht bei `until` (0.5 vs 0.33),
kostet aber bei `date` und verdoppelt Refusals. Canon-Form ist generell
nicht besser als rohe Anfrage (args 0.30 vs 0.40). Base-Needle-Contract
damit abgeschlossen: **Produktionsschema eingefroren**, FT-Datensatz (§10)
ist der nächste Schritt — Fokus: date/until/time/ranges/relative dates/
participant combinations.

## ArgEx-Pass (experiments/argx_pipeline.py — Argument-Robustheit)

Phase 1 per-field: 70 % des Args-Problems sind temporal — date 0.63,
until 0.33 (fehlt meist ganz), time 0.64, horizon 0.00; title 0.80,
end_time/duration 1.00. Phase 2-5 Two-Call-Pipeline (tool-select →
extract(args)) gemessen: **alle Varianten deutlich schlechter als
complete()** — plain 0.05-0.125, None-defaults identisch, Temporal-
Expressions-Variante 0.025. Verifikation am Weg: extract() mit
Aktions-namigen Schemas/Docs flippt die Grammar in Refusals („to create"
→ null; „described in text" → exakt) — Schema-Name/-Docstring sind
Verhaltensfaktoren. Auch mit neutralen Schemas und System-Facts bleibt
die Two-Call-Pipeline unter der Ein-Call-Baseline: **complete() bleibt
Produktionspfad** (Needle's Stärke ist der EINMALIGE constrained Call;
eine Zweit-Extraction verliert Task-Kontext und Grounding-Lizenz).
Phase 7/8: Mutationen laufen bereits by event_id nach resolve_event
(candidate → id → mutate). Phase 9 field-repair: verworfen — der Hebel
existiert nicht, solange extract generisch <50 % liefert.
**args_ok ≈ 0.4-0.48 ist die harte Grenze des Base-Needle** → FT (Plan
§14) ist der nächste Schritt, Gemma bleibt für Planning/Splitting.

## Needle-Bakeoff v2.1 (experiments/, kontrollierte Ablation, korrigierte Messung)

v2 hatte Messfehler (Orders wurden nicht wirklich reihenfolgend, strict nie
übergeben, Args nur Stringvergleich, geteilte Stores). v2.1 behebt alle und
misst mit 21 Cases × raw/canon × 3 Repeats (fresh engine init), echten
Produktionsschemas und drei getrennten Metriken + exact-call-stability:

- **Tool role (Production)**: 0.905 — gut genug
- **Args semantic: 0.476** — der Flaschenhals, unabhängig vom Toolset
- **Final DB: 0.725**
- Naming (create_event): 0.786 — calendar_* bleibt
- Split (isoliert 5-Tool / 6-Tool-Retrieval): 0.714 / 0.69 — verworfen
- Tool-Order: 10 echte Permutationen → 0.69–0.762, kein robuster Effekt
- `run()` mit korrekt übergebenem strict=False: identische ungrounded-Quote
  (10/20) — die Engine erwartet verbatim-evidenced Werte; unser Symbolik-
  Determinismus (Python rechnet) ist das Gegenteil → Design-Entscheidung, kein Bug
- `extract(single)`: 8/17 create-Cases sinnvoll befüllt, Arrays 0,
  iterativ-solo halluziniert — nur nach Gemma-Zerlegung sinnvoll
- **Contract-Ablation** (`contract_ablation.py`): symbolische Arg-Namen
  (date_expression/…) schlagen das aktuelle Contract NICHT
  (tool 0.65 vs 0.90, Refusals 0.275 vs 0.05) — verworfen
- **Loop-Vergleich** (`loop_compare.py`): Controller gewinnt multi-create
  (Dekomposition), needle-raw gewinnt simple Reads (12× schneller);
  find-slot → move scheitert in beiden (offen)

Modell-Evals sind probabilistisch und als solche markiert; deterministische
Korrektheit lebt zu 100 % in `tests/test_calendar.py`.

`needle_bakeoff_v2.py` misst mit 21 Cases × 2 Formen (raw + kanonisch) × 3
Repeats, echten Produktionsschemas (Literal, Field), drei getrennten Metriken
(tool role / argument semantic via Resolver / final DB state) und
exact-call-stability. Ergebnisse (Basis-Needle):

- **Production (calendar_*)**: tool 0.905, args 0.476, final 0.619,
  Refusals 0.095 — stärkste Variante
- **Naming** (create_event/…, isoliert, gleiche Params): 0.786 — calendar_*
  gewinnt; **Tool-Order**: 10 Permutationen identisch (0.738) — kein Effekt
- **Split create_event/create_absence**: 0.714 isoliert (5 Tools), 0.69 mit
  Retrieval — verworfen, ohne Konfundierung gemessen
- **run()**: strict grounding verweigert berechnete ISO-Argumente
  (10/20 ungrounded — die Engine erwartet verbatim-evidenced Werte);
  **extract()**: single-Item exakt, Arrays 0, iterativ-solo halluziniert →
  nutzbarer Weg: Gemma-Zerlegung → extract(single)
- **Loop-Vergleich** (6 komplexe Tasks): Controller gewinnt multi-create
  (Dekomposition), needle-raw gewinnt simple Reads (12× schneller);
  komplexeste Kette (find-slot → move) scheitert in beiden — offener Punkt

args_ok (0.476) ist der Flaschenhals → Needle-FT (Plan §17) auf das
eingefrorene Toolset ist der richtige nächste Schritt.

## Telegram (optional)

```sh
uv sync --extra telegram
uv run local-calendar-telegram           # Long Polling, keine Webhooks
uv run local-calendar-telegram --mode needle   # ohne Gemma (schnellster Pfad)

# mit FT-Modell (experiments/ft): Base-Needle wird durch das .cact ersetzt
NEEDLE_WEIGHTS=experiments/ft/models/<run>.cact uv run local-calendar-telegram --mode needle
```

`NEEDLE_WEIGHTS` wird vom gemeinsamen `Agent` gelesen (auch Gradio); ohne
Angabe läuft Base-Needle. Der Telegram-Adapter braucht keine weitere Anpassung.

Nutzt die bestehenden Variablen aus der Root-`.env` (`TELEGRAM_BOT_TOKEN`,
`TELEGRAM_OWNER_CHAT_ID`, `TELEGRAM_ALLOWED_CHAT_IDS`) — keine neuen Namen,
kein Überschreiben, keine Secrets in Logs. Owner/Allowlist bekommen Agent-
Ausführung, alle anderen Chats werden ignoriert. Natürliche Sprache geht an
denselben Agent wie Gradio (Phase 28); `/week` und `/today` senden Pillow-
Snapshots mit deterministischer Inline-Navigation (◀/Heute/▶, Teilnehmer-
Buttons), `/debug` (nur Owner) zeigt den letzten Eintrag aus
`data/traces.jsonl`. Widget-Auswahl über die Trace-Steps
(`calendar_find_slot` → Availability-PNG, `calendar_list` → Wochen-Snapshot,
create/move/delete → Confirmation-Text) — kein Text-Regex.
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
