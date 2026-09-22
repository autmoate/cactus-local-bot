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
7. **Gold-Format — SPARSE/evidenced-only (A/B-Entscheidung, Sep 16)** —
   nur evidenzbelegte Argumente: non-empty Strings, enum/int-Felder nur mit
   Query-Trigger (`heute/diese Woche/diesen Monat/this week/next week` für
   horizon; `N Minuten/Stunden` für duration). Der erste Stand (full/
   default-filled: alle Felder, Defaults ins Gold) wurde gemessen und
   verworfen: `gold_ab.py` bewertet dieselben Base-Needle-Outputs gegen
   beide Konventionen — exact_full 0.005 vs exact_sparse 0.077 (Test) bzw.
   0.000 vs 0.160 (Challenge); Base lässt Handler-Defaults mit ~99%
   Wahrscheinlichkeit weg (`missing_default` 142/142 horizon, 282/282
   duration_min, 288/288 days). Needle-eigener Finetune-Generator
   (`needle/model/finetune.py _GEN_TEMPLATE`) schreibt exakt diese Konvention
   vor: "only values evidenced in the query". Produktionssicherheit: Handler
   haben identische deterministische Defaults — Omission ist
   ausführungssicher. Wichtig: leeres `arguments:{}` ist bei sparse LEGAL
   (z.B. "Wann ist mein Yoga-Termin?" → calendar_list ohne Args).
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
| train | 10000 | 3267 | 1997 | 746 | 816 | 2274 | 900 (9%) |
| validation | 1000 | 303 | 192 | 92 | 113 | 210 | 90 (9%) |
| test | 1800 | 569 | 362 | 149 | 155 | 403 | 162 (9%) |

Zielraten leicht angepasst (§6): list-Query-Raum ist klein (kurze Queries,
wenige Slots), darum 13% statt 18% — create/find/move tragen die Last, wie
in §6 vorgesehen. Sprachmix train: de 5419 / gemma 2301 / en 1380.
Counts sind exakt garantiert: der Generator bricht hart ab, wenn ein Split
nicht die angeforderte Anzahl erreicht (negatives: sequenzielle Indices,
Pool-Guard).

## Reproduktion

```bash
cd needle-only
uv run python experiments/ft/build_dataset.py      # default seed 42
uv run python experiments/ft/validate_dataset.py   # hart, bricht bei Fehlern
uv run python experiments/ft/base_eval.py          # frozen Base-Baseline
```

Manifest (`manifest.json`) hält die Provenienz: `provenance.build_dataset_sha256`
+ `dataset_spec_sha256` (die eigentlichen Reproduzierbarkeits-IDs — der
Git-Commit allein wäre falsch, weil der Generator erst mit dem Dataset-Commit
entsteht), `schema_sha256`, `seed`, `file_sha256` pro Split, counts,
negative_share, System-Facts, Gold-Konvention. Zusätzlich informativ
`generated_at_commit`. Die JSONLs sind **committet** (train ≈ 5,3 MB,
validation ≈ 0,5 MB, test ≈ 1,0 MB — Reproduktion ohne Generatorlauf möglich,
6,8 MB gesamt). Der Generator bricht hart ab, wenn ein Split nicht exakt die
angeforderte Anzahl erreicht (`count mismatch`) — kein stilles Unterschreiten.

Prüfung auf der RTX-Maschine:
```bash
uv run python experiments/ft/build_dataset.py   # seed 42
uv run python experiments/ft/validate_dataset.py
# manifest.json: provenance-hashes + file_sha256 gegen den Pi-Stand vergleichen
```

## Frozen Base-Baseline (vom Pi, Seed-42-Dataset, 1 Repeat)

Gleiche Modell-Outputs wie beim ersten Stand (rows-Cache `reports/base_*_rows.jsonl`),
gegen das sparse-Gold neu bewertet (`base_eval.py --from-cache`):

Level 1 (synthetic test, n=1800):
tool_ok 0.863 · args_ok 0.449 (produktionstolerant: Extra-Felder erlaubt —
Handler-Defaults verändern das Verhalten nicht) · exact_args_ok 0.113 ·
refusals 0.081 · median 1206 ms
per-field: date 0.551 · until 0.306 · time 0.935 · title 0.999 ·
persons 0.732 · person 0.650 · participants 0.887

Level 3 (challenge, n=25, 14 reale Telegram-Traces + 11 reale Regressionen):
tool_ok 0.920 · args_ok 0.520 · exact_args_ok 0.120 · refusals 0.080

**Base-Grenze:** args_ok ≈ 0.45/0.52 produktionsnah, aber exact_args nur
0.11/0.12 — der Hauptfehler ist der Span-Kopier-Shift (Base liefert
aufgelöstes ISO `2026-12-03` statt den Input-Span `3.12.`; Auflösen macht
dann Python). Genau diese Spans trainiert das FT-Dataset. Erfolgskriterien
§22 (auf Level-1-exact gemessen): exact_args ≥ +0.20 absolut über Base
(≥ 0.31), args_ok produktionsnah ≥ +0.15, Refusals ≤ Base, keine Regression
bei Reads/Deletes. Level 2 (final_db_ok) läuft im bestehenden
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
- `gold_ab.py` bleibt Teil des Moduls: sie dokumentiert die Konventions-
  Entscheidung (sparse) und ist nach dem FT der A/B-Vergleichsmaßstab
  (exact_full vs exact_sparse gegen FT-Outputs).

## Pipeline auf der RTX 3090 (`train_rtx.py`)

Die committeten JSONLs tragen **kein `tools`/`system`** — `needle finetune`
rendert sonst `<tools></tools>` (111 statt ~850 Tokens, das Modell sähe den
Katalog nie). `train_rtx.py` injiziert den Produktionskatalog (`tools.json`) +
`SYSTEM_FACTS` wrapper-seitig in ein Trainings-Artefakt unter `data/_ft/`;
Dataset + `manifest.json` bleiben unangetastet und hashbar. Danach
`needle finetune` (Console-Script — `python -m needle.cli` ist ein No-op) und
`needle build` → `.cact`. Jeder Run schreibt Log + Run-Manifest
(`reports/runs/<run>.log|json`: Params, Dataset-Hashes, Zeiten).

```bash
cd needle-only
# Root-venv (Python 3.11) statt Projekt-venv: Projekt verlangt >=3.12
uv pip install --python ../.venv-ft/bin/python -e .   # nur mit py>=3.12; sonst PYTHONPATH=src
PYTHONPATH=src ../.venv-ft/bin/python experiments/ft/build_dataset.py     # seed 42
PYTHONPATH=src ../.venv-ft/bin/python experiments/ft/validate_dataset.py  # muss "OK" sagen
CUDA_VISIBLE_DEVICES=0 PYTHONPATH=src ../.venv-ft/bin/python experiments/ft/train_rtx.py \
    --run-name sa-r16-lr1e-4-e8-seed42 --rank 16 --lr 1e-4 --epochs 8 --seed 42 --batch-size 8
```

Batch 8 ist Default wegen des WSL-VRAM-Caps (~12 GiB/GPU; 16@1024 OOMt).
Ein Run (10 k, 8 Epochen, batch 8, 9000 Steps) ≈ **1,9 h** auf der 3090.

### 3-Ebenen-Evaluation

```bash
# Level 1+3 (synthetic test n=1800 + challenge n=25), §22-Kriterien automatisch:
PYTHONPATH=src ../.venv-ft/bin/python experiments/ft/base_eval.py \
    --weights experiments/ft/models/<run>.cact --tag <run>
# schreibt reports/<run>_test_report.json + <run>_challenge_report.json
# und prüft exact_args >= base+0.20, args_ok >= base+0.15, refusals <= base

# Level 2 (final_db_ok, 29 Cases aus eval_cases.json) über den Produktions-Agent:
NEEDLE_WEIGHTS=experiments/ft/models/<run>.cact PYTHONPATH=src \
    ../.venv-ft/bin/python tests/test_e2e.py            # + --repeat N

# A/B der Gold-Konvention (sparse vs full) gegen FT-Outputs:
PYTHONPATH=src ../.venv-ft/bin/python experiments/ft/gold_ab.py
```

### Pi-Abnahme (§24)

```bash
# auf dem Pi: FT-Modell laden, App + E2E, dann messen
NEEDLE_WEIGHTS=models/<run>.cact uv run local-calendar --mode needle
NEEDLE_WEIGHTS=models/<run>.cact uv run python tests/test_e2e.py
# Kriterien: Accuracy nicht schlechter als Base, Latenz/RAM im Rahmen
# (Base: ~1 s/Query needle-only, Session ~28 MB), Cold start, Refusals.
```
