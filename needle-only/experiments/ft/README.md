# Needle-FT Dataset v2 (experiments/ft/)

Task-spezifisches Finetuning-Dataset für EIN Needle-Modell auf dem aktuellen
fünf-Tool-Produktionsset (calendar_create/move/delete/list/find_slot).
Trainingsaufgabe: kurze atomare Kalenderanweisung → exakt richtiger Tool Call
→ exakt richtige Argumente. NICHT trainiert: Multi-Step-Planning, Gemma,
DB-Auflösung, Kollisionen, Event-Identifikation — Python bleibt Domainwahrheit.

## Prinzipien (Plan v1, Nutzer-Sep 16)

1. **Schemas nie dupliziert** — `build_dataset.py` lädt die fünf
   Produktionstools via `build_tools()` und liest needle-eigenes
   `fn._needle_tool` (= `build_schema`). Ändert sich das Produktionsschema,
   schlägt die Validierung fehl (schema-hash check).
2. **Deterministisch, seedbar** — Generierung `uv run python build_dataset.py`
   (Default seed 42); Gold-Calls ausschließlich deterministisch aus
   Template + Slots (keine LLM-Labels).
3. **Split nach Template-Familien, nie nach Werten allein** — jede Familie
   gehört genau einem Split (Validator erzwingt das, plan §10).
4. **Held-out Werte (§9)** — validation/test ziehen Titles/Persons/Ausdrücke
   ausschließlich aus eval-only Pools (TÜV, Elternabend, Miriam, Felix, Nora …),
   Train kennt sie nicht; der Validator prüft Wortgrenzen-Leaks.
5. **Sprachmix (§4, Finding ac150d1)** — 60% kurze direkte deutsche
   Formulierungen, 25% kurze Gemma-artige atomare Instructions, 15% simple
   englische Varianten. Keine ausgeschriebenen Kanon-Sätze (die messen
   schlechter als Rohformulierungen).
6. **Temporal-heavy (§5)** — Meta-Tags pro Beispiel
   (`temporal_class`, `range_type`, `participant_count`, `explicitness`,
   `language`, `source_family`); die problematischen Klassen
   (ranges, weekday-relative, offsets, dayparts, explicit ISO) sind bewusst
   hoch gewichtet, Coverage-Report zählt sie.
7. **Gold-Format (§12)** — alle Schema-String-Felder present, nicht
   evidenzbelegte als `""`; Integer/Enum tragen Schema-Defaults
   (`duration_min=60`, `days=7`, `horizon="week"`).
8. **Grounding (§13)** — String-Argumentwerte sind literale Substrings des
   Inputs (Validator prüft). Dokumentierte Ausnahmen:
   - `horizon` ist ein Produktion-Enum (strukturierter Wert, kein Span)
   - `duration_min`/`days` sind Integer
   - `calendar_delete` hat kein `time`-Arg — Uhrzeit im Input fließt nicht ins
     Gold (Identifikation übernimmt `resolve_event` in Python)
   - daypart-Spans (`morgen Nachmittag`) werden für move/find_slot als nackter
     Tag gold-markiert, weil deren Handler nur bare-day spans auflösen
9. **Negatives (§14)** — 8–10% (hier 8.4%), davon ~2/3 Off-topic, 1/3
   Kalender-nahe Nicht-ausführbare; deterministische Kombi-Pools, Split-Slices
   garantieren cross-split-disjunkte Negatives.
10. **Reale Traces (§11)** — 25 frozen Challenge-Items
    (`challenge/challenge_traces.jsonl`): 14 echte Telegram-Inputs
    (inkl. aller dokumentierten Fails: Refusal-Varianz, Range-Create-Fail,
    generische Löschungen) + 11 reale Regression-Fälle aus eval_cases.

## Datensatzstand (Seed 42)

| Split | N | create | move | delete | list | find | negative |
|---|---|---|---|---|---|---|---|
| train | 9940 | 3193 | 1930 | 875 | 952 | 2150 | 840 (8.4%) |
| validation | 1000 | 279 | 185 | 112 | 149 | 185 | 90 (9%) |
| test | 1800 | 539 | 354 | 158 | 213 | 374 | 162 (9%) |

Zielraten leicht angepasst (§6): list-Query-Raum ist klein (kurze Queries,
wenige Slots), darum 13% statt 18% — create/find/move tragen die Last, wie
in §6 vorgesehen. Sprachmix train: de 5435 / gemma 2243 / en 1422.

## Reproduktion

```bash
cd needle-only
uv run python experiments/ft/build_dataset.py      # default seed 42
uv run python experiments/ft/validate_dataset.py   # hart, bricht bei Fehlern
uv run python experiments/ft/base_eval.py          # frozen Base-Baseline
```

Manifest (`manifest.json`) hält: generator commit, seed, counts,
schema-hash, file-sha256, negative_share, system facts, Gold-Konvention.
Die JSONLs (train 9940 ≈ 20 MB mit Schemas pro Zeile) werden NICHT committet —
die RTX-Maschine reproduziert exakt denselben Stand via seed + commit
(manifest hashes verifizieren).

## Frozen Base-Baseline (vom Pi, Seed-42-Dataset, 1 Repeat)

Level 1 (synthetic test, n=1800):
tool_ok 0.863 · args_ok (semantisch) 0.310 · exact_args_ok 0.057 ·
refusals 0.081 · median 1140 ms
per-field: date 0.544 · until 0.357 · time 0.935 · title 0.999 ·
persons 0.732 · person 0.650 · participants 0.887 · horizon 0.006 ·
duration_min 0.012 · days 0.002

Level 3 (challenge, n=25, 14 reale Telegram-Traces + 11 reale Regressionen):
tool_ok 0.920 · args_ok 0.280 · exact_args_ok 0.000 · refusals 0.080 ·
median 913 ms

Das ist die Frozen Baseline: **args_ok ≈ 0.31 ist die Base-Grenze**, die das
FT-Dataset gezielt angreift (Erfolgskriterien §22: args_ok ≥ +0.20 absolut,
final_db_ok ≥ +0.15, Refusals ≤ Base, keine Regression bei Reads/Deletes —
Zielraum args_ok 0.65-0.70). Exact-args (die FT-Konvention) liegt bei Base
bei 0.06 — die Feld-Füllung (horizon/duration/days/empty-strings) ist
der größte konventionelle Shift. Level 2 (final_db_ok) läuft im bestehenden
Bakeoff-/E2E-Harness.

## Bekannte Grenzen / dokumentierte Gaps

- Resolver versteht nicht: "diesen Freitag", "am Wochenende", ausgeschriebene
  Offsets ("in drei Tagen" — nur Ziffern), "am Freitag" (nur plain
  "Freitag") — solche Spans sind bewusst NICHT Gold (Python-Grundwahrheit
  bleibt maßgeblich; FT trainiert keine unaufösbaren Golds).
- Challenge bleibt bewusst "unfair" in sich: sie hält reale Fails samt
  Kapazitätsgrenzen des Resolvers — als Level-3-Erfolgsmessung gedacht.
- Kollisions-/Absence-Interaktion, Multi-Intent, En-Datumsangaben wie
  "August 3" sind NICHT Teil des Trainings (Produktionstools + Python
  übernehmen das).

## Pipeline-Schritte nach diesem Commit (extern, RTX 3090)

1. Training: eigenes `train_rtx.py` (keine Jetson-Hacks; §20).
2. Hyperparameter-Matrix klein: LoRA rank 8/16, LR 5e-5/1e-4, epochs 3/5/8;
   mindestens 3 Seeds (§21).
3. Erfolgskriterien (§22) und 3-Ebenen-Evaluation (§23) vor Training fixiert.
4. Pi-Abnahme (§24): Accuracy, Latency, RAM, Cold start, Refusal-Verhalten.
