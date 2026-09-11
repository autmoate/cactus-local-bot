# needle-only — Independent Mini-Stack für Needle 2

> **Zweck:** Eigene, unabhängige Experimentierumgebung für [Needle 2](https://github.com/cactus-compute/needle) — separat vom Gemma-Stack in `modules/` und `tui-app.py`. Alle Komponenten laufen lokal, keine Cloud-Dependency.

## Überblick

```
needle-only/
├── ARCHITECTURE.md          # Architektur-Doku (v5.x)
│
├── run.py                   # Haupt-TUI (v5.6: 29/29 Eval)
├── orga.py                  # Orga v5.5: Kalender-CRUD mit Abwesenheiten
├── tools.py                 # MiniTools v5.6: 6 Tools (CRUD + Gruppen)
├── router.py                # Semantic Router (MiniLM, ~100ms)
├── tg.py                    # Telegram-Bot "needle 📌"
├── eval.py + eval_cases.py  # Eval-Suite: 29 goldene Fälle
├── ics.py                   # ICS-Export (v4.1)
├── scheduler.py             # Reminder-Firing (Poll 30s)
├── serve.py                 # HTTP-Server für ICS-Feed
├── translate.py             # opus-mt Übersetzung (deprecated)
│
├── world/                   # v6: World Model (Datom Store)
│   ├── model.py             # DatomChange, WriteFrame, ReadFrame
│   ├── store.py             # Postgres-backed Datom Store
│   ├── planner.py           # WritePlanner (Frames → DatomChanges)
│   ├── query.py             # QueryEngine (ReadFrames → Antworten)
│   ├── resolver.py          # Entity-Resolution
│   ├── interpreter.py       # NeedleInterpreter (Frames aus Sprache)
│   ├── service.py           # WorldService (Zentrale Orchestrierung)
│   └── schema.sql           # SQL-Schema für world-Datom-Tabellen
│
├── calendar_ft/             # NEU: Calendar-FT (Feintuning-Experimente)
│   ├── README.md            # Detaillierte Calendar-FT-Doku
│   ├── schemas/             # Tool-Schemas (calendar_write, calendar_read, reminder)
│   ├── build_dataset.py     # Deterministischer Dataset-Builder
│   ├── validate_dataset.py  # Dataset-Validator (Grounding-Prinzip)
│   ├── train.py             # Trainings-Wrapper (Jetson AGX Orin)
│   ├── eval_model.py        # Modell-Evaluation (Base + FT)
│   ├── compare_runs.py      # Base vs. FT Vergleich
│   ├── run_experiment.py    # Ein-Kommando-Experiment-Runner
│   ├── e2e_eval.py          # E2E-Evaluation (10 Fälle)
│   ├── upload_hf.py         # HuggingFace-Upload der .cact-Modelle
│   ├── data/train/          # Trainings-Datasets (JSONL)
│   ├── data/eval/           # Eval-Datasets (JSONL, held-out)
│   ├── models/              # .cact-Modelle + LoRA-Adapter
│   └── reports/             # Eval-Reports, Worst-Cases, E2E-Results
│
├── calendar_service.py      # NEU: Calendar-vNext-Service (Router → FT → Planner → DB)
├── router_calendar.py       # NEU: Deterministischer Router für Calendar-vNext
│
└── (docs/, scripts/ etc. im Repo-Root)
```

## Komponenten im Detail

### v5.6: Semantic Router + Gruppen-Support (29/29 Eval)

Der aktuelle Stand der Pipeline:

```
User Query
     ↓
Semantic Router (paraphrase-multilingual-MiniLM, ~100ms)
     ↓ (score >= 0.55)
Per-Tool-Needle (NUR das gewählte Tool)
     ↓
Deterministische Fixes (fix_args: owner, participants, Zeit)
     ↓
Ausführung (Read-Tools direkt, Write-Tools mit Approval)
```

**6 Needle-Tools:**
1. `calendar_create` (appointment/reminder/task/absence, mit owner/participants)
2. `calendar_edit`
3. `calendar_read` (mit person-Parameter)
4. `calendar_delete`
5. `calendar_filter` (Gruppen-Abfragen)
6. `free_slots` (gemeinsame freie Zeitslots)

### v6: World Model (Datom Store)

Das v6-Experiment ersetzt das klassische Kalender-CRUD durch ein **append-only Datom Store**-Modell:

```
Sprache
  ↓ (NeedleInterpreter)
WriteFrame / ReadFrame (reine Sprache, keine IDs/SQL)
  ↓ (WritePlanner / QueryEngine)
DatomChanges (add/retract von atomaren Fakten)
  ↓ (WorldStore)
Postgres (world_datom, world_tx, world_entity)
```

**Kernidee:** Needle erzeugt nur **Frames** (sprachliche Intentionen). Der Planner übersetzt Frames in `DatomChanges`, der Store schreibt append-only nach Postgres. Needle erzeugt niemals UUIDs, SQL oder CRUD-Befehle.

**Zentrale Dateien:**
- `world/model.py` — Dataclasses: `DatomChange`, `WriteFrame`, `ReadFrame`, `TransactionPlan`
- `world/store.py` — `WorldStore`: Postgres-Backed Datom Store (append-only)
- `world/planner.py` — `WritePlanner`: Übersetzt WriteFrames in DatomChanges
- `world/query.py` — `QueryEngine`: Beantwortet ReadFrames aus dem World State
- `world/resolver.py` — `Resolver`: Entity-Resolution (Fuzzy-Matching)
- `world/interpreter.py` — `NeedleInterpreter`: Extrahiert Frames aus Sprache
- `world/service.py` — `WorldService`: Zentrale Orchestrierung

### Calendar-FT: Task-spezifische Feintuning-Experimente

**Siehe [calendar_ft/README.md](calendar_ft/README.md) für die vollständige Doku.**

Drei separate LoRA-Feintuning-Adapter für Needle 2:

| Task | Modell | HF-Repo |
|---|---|---|
| `calendar_write` | Termin erstellen/verschieben/absagen | [autmoate/cactus-needle2-calendar-write-dt](https://huggingface.co/autmoate/cactus-needle2-calendar-write-dt) |
| `calendar_read` | Kalender abfragen/anzeigen | [autmoate/cactus-needle2-calendar-read-dt](https://huggingface.co/autmoate/cactus-needle2-calendar-read-dt) |
| `reminder` | Erinnerungen setzen | [autmoate/cactus-needle2-reminder-dt](https://huggingface.co/autmoate/cactus-needle2-reminder-dt) |

**Pipeline:**
```
User Query → Router (deterministisch) → Task-spezifisches FT-Modell (.cact)
           → Deterministischer Planner → SQLite/Postgres → Response
```

**Ergebnisse (Base vs. FT):**
- `calendar_write`: F1 0.40 → 0.75, Hallucination-Rate 27% → 3%
- `calendar_read`: F1 0.18 → 0.49
- E2E: 9/10 (90% Pass Rate), 100% Route Accuracy

**Bekannte Limitierungen:**
- Nur **deutsche** Anfragen trainiert (kein bilingualer Support)
- FT-Modell verweigert bei fehlendem Date (Base-Fallback fängt das ab)
- Reminder-Task hat schwächere Performance (75% Tool-Accuracy)

---

## How-To: Feintuning von Needle 2 auf dem Jetson AGX Orin

### Voraussetzungen

```bash
# 1. uv-Umgebung (Repo-Root)
uv venv --python 3.11
source .venv/bin/activate
uv pip install "cactus-needle[train,gpu]"

# 2. JAX-GPU-Verifikation (MUSS cuda:0 zeigen)
unset LD_LIBRARY_PATH
XLA_PYTHON_CLIENT_PREALLOCATE=false XLA_FLAGS="--xla_gpu_autotune_level=0" \
python -c "import jax; print(jax.devices())"
```

**Wichtig:** Auf dem Jetson AGX Orin sind **drei Workarounds** notwendig:
1. `LD_LIBRARY_PATH` leeren (Konflikt System-CUDA 12.6 vs. pip-CUDA 12.9)
2. `XLA_FLAGS="--xla_gpu_autotune_level=0"` (Autotuner-Crash auf sm_87)
3. `XLA_PYTHON_CLIENT_PREALLOCATE=false` (46 GiB Preallocation auf Unified Memory)

Details: [calendar_ft/reports/environment.md](calendar_ft/reports/environment.md)

### Schritt 1: Dataset bauen

```bash
cd needle-only/calendar_ft

# Trainings-Dataset (deterministisch, seedbar)
python build_dataset.py \
    --task calendar_write \
    --count 2000 \
    --seed 42 \
    --out data/train/calendar_write.jsonl

# Eval-Dataset (held-out Werte und Phrasings)
python build_dataset.py \
    --task calendar_write \
    --count 300 \
    --seed 43 \
    --eval \
    --exclude data/train/calendar_write.jsonl \
    --out data/eval/calendar_write.jsonl
```

**Dataset-Design-Prinzipien:**
- **Grounding:** Jeder Argument-Wert ist ein **Substring des Queries** (z.B. `"14 Uhr"` für `time`, nicht `"14:00"`)
- **Argumente weglassen statt leere Strings:** Wenn keine Evidenz im Query, wird das Feld **komplett weggelassen**
- **Negativ-Beispiele:** ~12% (Cross-Task-Queries + Off-Topic)
- **Train/Eval-Split:** Keine Duplikate, unterschiedliche Template-Phrasings

### Schritt 2: Dataset validieren

```bash
python validate_dataset.py \
    --dataset data/train/calendar_write.jsonl \
    --task calendar_write
```

### Schritt 3: Fine-Tuning (LoRA)

```bash
# Direkt via needle CLI:
needle finetune data/train/calendar_write.jsonl \
    --epochs 10 \
    --lora-rank 16 \
    --lora-alpha 32 \
    --lr 1e-4 \
    --out models/calendar_write_lora.pkl

# Oder via train.py (mit Jetson-Workarounds):
python train.py \
    --task calendar_write \
    --epochs 10 \
    --lora-rank 16 \
    --lora-alpha 32 \
    --lr 1e-4
```

**Trainings-Details (Run B):**

| Parameter | Wert |
|---|---|
| LoRA-Rank | 16 |
| LoRA-Alpha | 32 |
| Learning Rate | 1e-4 |
| Epochs | 10 |
| Batch Size | 16 |
| QAT-Bits | auto (mixed CQ STE + A8) |
| LoRA-Targets | `q_proj`, `k_proj`, `v_proj`, `gate_proj`, `out_proj` (alle 27 Layer) |

**Trainings-Zeit auf AGX Orin (GPU):**
- `calendar_write`: ~3.9h (1914 Beispiele, 1080 Steps)
- `calendar_read`: ~1.0h (1157 Beispiele, 650 Steps)
- `reminder`: ~0.5h (589 Beispiele, 330 Steps)

### Schritt 4: .cact-Export

```bash
needle build checkpoints/needle2.pkl \
    --lora models/calendar_write_lora.pkl \
    --out models/calendar_write.cact
```

**Export-Format:** W4A8 (4-bit Gewichte, 8-bit Aktivierungen), ~23 MB pro Archiv.

### Schritt 5: Evaluation (Base vs. FT)

```bash
# Base-Modell evaluieren (ohne .cact-Weights)
python eval_model.py \
    --task calendar_write \
    --dataset data/eval/calendar_write.jsonl \
    --out reports/base_calendar_write.json

# FT-Modell evaluieren (mit .cact-Weights)
python eval_model.py \
    --task calendar_write \
    --dataset data/eval/calendar_write.jsonl \
    --weights models/calendar_write.cact \
    --out reports/calendar_write_ft.json

# Base vs. FT vergleichen
python compare_runs.py \
    reports/base_calendar_write.json \
    reports/calendar_write_ft.json
```

**Metriken:**
- `tool_call_accuracy` — wurde das erwartete Tool aufgerufen?
- `full_frame_exact_match` — stimmen Tool + alle Argumente exakt?
- `field_precision` / `field_recall` / `field_f1` — wie präzise sind die extrahierten Felder?
- `hallucinated_field_rate` — Anteil der Felder, deren Werte **nicht** im Query vorkommen
- `false_positive_tool_rate` — Anteil der Negativ-Beispiele, bei denen ein Tool-Call ausgelöst wurde
- `latency_ms` — Inferenz-Latenz

### Schritt 6: E2E-Evaluation

```bash
python e2e_eval.py
```

Führt die 10 E2E-Cases aus der Nutzer-Spezifikation aus (Create, Move, Cancel, Read, Person-Filter, Free-Slots, Reminder, Off-Topic).

### Schritt 7: HuggingFace-Upload (optional)

```bash
python upload_hf.py --dry-run   # Preview
python upload_hf.py             # Upload
```

Lädt die `.cact`-Modelle + Model-Cards zu HuggingFace hoch.

---

## Schnellstart: Kompletter Flow mit einem Befehl

```bash
cd needle-only/calendar_ft

# Ein Task:
python run_experiment.py --task calendar_write

# Alle Tasks:
python run_experiment.py --all

# Mit existierenden Modellen (ohne Re-Training):
python run_experiment.py --task calendar_write --skip-train
```

Der Runner führt aus:
1. **Environment-Check** (JAX GPU verfügbar?)
2. **Dataset bauen** (build_dataset.py)
3. **Dataset validieren** (validate_dataset.py)
4. **Base-Evaluation** (eval_model.py ohne Weights)
5. **Fine-Tuning** (LoRA via needle finetune)
6. **.cact-Export** (needle build)
7. **FT-Evaluation** (eval_model.py mit .cact-Weights)
8. **Base vs. FT Vergleich**
9. **E2E-Evaluation** (e2e_eval.py)

---

## Architektur: Calendar-vNext-Service

Die E2E-Pipeline (`calendar_service.py`) integriert alle Komponenten:

```
┌─────────────────────────────────────────────────────────┐
│                    CalendarService                       │
├─────────────────────────────────────────────────────────┤
│  1. Router (deterministisch, Regex-Trigger)             │
│     → calendar_write / calendar_read / reminder / none │
│                                                          │
│  2. Task-spezifisches Needle-FT-Modell (.cact)          │
│     → Extrahiert Argumente aus Query                    │
│     → Fallback auf Base-Modell wenn FT leer liefert    │
│                                                          │
│  3. Deterministischer Planner                           │
│     → create_appointment / read_calendar / reminder    │
│     → SQLite (Tests) / Postgres (Production)            │
│                                                          │
│  4. Response                                             │
│     → Strukturiertes JSON mit Aktion + DB-Ergebnis     │
└─────────────────────────────────────────────────────────┘
```

**Verwendung:**

```python
from calendar_service import CalendarService

service = CalendarService(db_path=":memory:")
result = service.handle("Morgen um 14 Uhr Zahnarzt")
# → {"route": "calendar_write", "result": {"action": "create", ...}}
```

---

## Verwandte Dokumentation

| Dokument | Inhalt |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architektur-Doku (v5.x, 419 Zeilen) |
| [calendar_ft/README.md](calendar_ft/README.md) | Vollständige Calendar-FT-Doku |
| [calendar_ft/reports/environment.md](calendar_ft/reports/environment.md) | Environment-Report (Jetson AGX Orin) |
| [calendar_ft/reports/](calendar_ft/reports/) | Alle Eval-Reports und E2E-Results |

## Referenzen

- **Needle 2 (Base Model):** [Cactus-Compute/needle2](https://huggingface.co/Cactus-Compute/needle2)
- **Needle GitHub:** [cactus-compute/needle](https://github.com/cactus-compute/needle)
- **Fine-Tuning Guide:** [doc/finetuning.md](https://github.com/cactus-compute/needle/blob/main/doc/finetuning.md)
- **API Guide:** [doc/apis.md](https://github.com/cactus-compute/needle/blob/main/doc/apis.md)
- **Simple Attention Network Paper:** [arXiv:2607.18363](https://arxiv.org/abs/2607.18363)
