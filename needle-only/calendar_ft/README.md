# Calendar-FT: Task-spezifische Needle-Feintuning-Experimente

> **Status:** Experimentell. Erste vollständige Runde abgeschlossen.
> **Hardware:** NVIDIA Jetson AGX Orin 64 GB (aarch64, sm_87)
> **Stack:** Python 3.11 (uv), cactus-needle 2.0.13, JAX 0.10.2 (CUDA 12), SQLite (E2E-Tests)

## 1. Ziel

Untersuchen, ob task-spezifische Needle-Feintuning-Adapter die Kalenderbedienung
signifikant verbessern. Drei separate LoRA-Adapter werden trainiert:

| Task | Modell | Zweck |
|---|---|---|
| `calendar_write` | `models/calendar_write.cact` | Termin erstellen/verschieben/absagen |
| `calendar_read` | `models/calendar_read.cact` | Kalender abfragen/anzeigen |
| `reminder` | `models/reminder.cact` | Erinnerungen setzen (relativ/absolut) |

## 2. Architektur

```
User Query
     ↓
Router (deterministisch, Regex-Trigger)
     ↓
Task-spezifisches Needle-FT-Modell (.cact)
     ↓
Deterministischer Planner (SQLite/Postgres)
     ↓
Response
```

Der Router trifft nur eine grobe Triage (write/read/reminder/none). Das
task-spezifische FT-Modell extrahiert die Argumente. Der Planner führt die
DB-Operation deterministisch aus.

## 3. Tool-Schemas

Alle Schemas liegen in `schemas/`. Argument-Werte sind **immer** Substrings
des Queries (Grounding-Prinzip).

### calendar_write.json
```json
{
  "name": "calendar_write",
  "parameters": {
    "type": "object",
    "properties": {
      "title":       {"type": "string"},
      "date":        {"type": "string"},
      "time":        {"type": "string"},
      "end_time":    {"type": "string"},
      "person":      {"type": "string"},
      "participants":{"type": "string"},
      "location":    {"type": "string"},
      "action_span": {"type": "string"},
      "modifier":    {"type": "string"}
    }
  }
}
```

## 4. Dataset-Builder

```bash
# Training-Dataset generieren (deterministisch, seedbar)
python build_dataset.py --task calendar_write --count 2000 --seed 42 \
    --out data/train/calendar_write.jsonl

# Eval-Dataset generieren (mit --exclude, um Train-Duplikate zu vermeiden)
python build_dataset.py --task calendar_write --count 300 --seed 43 \
    --eval --exclude data/train/calendar_write.jsonl \
    --out data/eval/calendar_write.jsonl
```

### Train/Eval-Split-Strategie

| Aspekt | Train | Eval |
|---|---|---|
| Template-Phrasings | Kalender-Standard-Phrasings | Andere Phrasings (unbekannt) |
| Wert-Pools | Train-Werte | Train + Eval-Werte (unbekannte Namen/Termine/Daten) |
| Duplikate | — | 0 (per `--exclude` garantiert) |
| Negativ-Beispiele | 12% | 12% |

## 5. Training

```bash
# Kalender-Write (Task A)
python train.py --task calendar_write --epochs 10 --lora-rank 16 --lora-alpha 32 --lr 1e-4 --seed 42

# Kalender-Read (Task B)
python train.py --task calendar_read --epochs 10 --lora-rank 16 --lora-alpha 32 --lr 1e-4 --seed 42

# Reminder (Task C, optional)
python train.py --task reminder --epochs 10 --lora-rank 16 --lora-alpha 32 --lr 1e-4 --seed 42
```

### Trainings-Hyperparameter (Run B)

| Parameter | Wert |
|---|---|
| LoRA-Rank | 16 |
| LoRA-Alpha | 32 |
| Learning Rate | 1e-4 |
| Epochs | 10 |
| Batch Size | 16 |
| Max Sequence Length | 1024 |
| LoRA-Targets | `q_proj, k_proj, v_proj, gate_proj, out_proj` (alle 27 Layer) |

### Trainings-Zeiten (AGX Orin, GPU)

| Task | Examples | Steps | Zeit |
|---|---|---|---|
| calendar_write | 1914 | 1080 | 3.9 h |
| calendar_read | 1157 | 650 | 1.0 h |
| reminder | 589 | 330 | 0.5 h |

## 6. Model-Export (.cact)

```bash
# Nach dem Training:
python -c "
from needle.model.export import write_export
from needle.model.run import load_checkpoint
from needle.model.finetune import merge_lora
from needle.model.tokenizer import get_tokenizer
from needle.model.architecture import effective_kv_window
import pickle, jax.numpy as jnp

params, config = load_checkpoint('checkpoints/needle2.pkl')
with open('models/calendar_write_lora.pkl', 'rb') as f:
    adapter = pickle.load(f)
lora = {tuple(k.split('/')): {'A': jnp.asarray(v['A']), 'B': jnp.asarray(v['B'])}
        for k, v in adapter['lora'].items()}
merged = merge_lora(params, lora, adapter['scale'])
info = write_export(merged, config, 'models/calendar_write.cact',
                    bits=4, bits_map=None,
                    tokenizer=get_tokenizer(config.vocab_size),
                    kv_window=effective_kv_window(config))
print(f'Wrote {info[\"path\"]}  {info[\"bytes\"]/1e6:.2f} MB  W4A8')
"
```

Export-Details: W4A8 (4-bit Gewichte, 8-bit Aktivierungen), 405 Tensoren, ~23 MB.

## 7. Evaluation

```bash
# Base-Modell evaluieren (ohne --weights)
python eval_model.py --task calendar_write \
    --dataset data/eval/calendar_write.jsonl \
    --out reports/base_calendar_write.json

# FT-Modell evaluieren (mit .cact-Weights)
python eval_model.py --task calendar_write \
    --dataset data/eval/calendar_write.jsonl \
    --weights models/calendar_write.cact \
    --out reports/calendar_write_ft.json
```

### E2E-Evaluation (10 Cases)

```bash
python e2e_eval.py
```

## 8. Ergebnisse

### Base vs. FT (Eval-Datasets, 12% Negatives)

| Task | Base Exact | FT Exact | Base F1 | FT F1 | FT Halluc. | FT FP | FT Latency |
|---|---|---|---|---|---|---|---|
| calendar_write | 0.127 | **0.287** | 0.402 | **0.752** | 0.031 | 0.056 | 240ms |
| calendar_read | 0.117 | **0.175** | 0.184 | **0.494** | 0.059 | 0.214 | 145ms |
| reminder | 0.092 | **0.233** | 0.493 | **0.565** | 0.201 | 0.286 | 173ms |

### E2E-Results (Calendar-vNext Pipeline)

| Metrik | Wert |
|---|---|
| E2E Pass Rate | **90%** (9/10) |
| Route Accuracy | **100%** |
| Calendar-Write Tool Call Accuracy | **91%** |
| Calendar-Read Tool Call Accuracy | **96%** |
| Reminder Tool Call Accuracy | **75%** |

### Model-Größen

| Modell | Größe |
|---|---|
| Base needle2.pkl | 90.4 MB |
| calendar_write.cact (FT) | 23.2 MB |
| calendar_read.cact (FT) | 23.2 MB |
| reminder.cact (FT) | 23.2 MB |
| calendar_write_lora.pkl (Adapter) | 8.0 MB |

## 9. Integration

Der `CalendarService` in `needle-only/calendar_service.py` integriert alle
FT-Modelle in eine Pipeline:

```python
from calendar_service import CalendarService

service = CalendarService(db_path=":memory:")
result = service.handle("Morgen um 14 Uhr Zahnarzt")
# -> route=calendar_write, result={action: create, title: Zahnarzt, start_at: 2026-09-12T14:00:00}
```

### Fallback-Strategie

Wenn das FT-Modell keine `function_calls` liefert (z.B. bei "Missing required
parameter"-Verweigerung), fällt der Service auf das Base-Modell zurück.

## 10. Bekannte Probleme

1. **FT-Modell verweigert Calls ohne Date**: Bei Queries wie
   "Verschieb Zahnarzt auf 15 Uhr" (kein Datum angegeben) verweigert das
   FT-Modell den Call mit "Missing required parameter: date". Der Base-Model-
   Fallback fängt diese Fälle ab.

2. **Generalisierung bei unbekannten Phrasings**: "Sag den Termin Zahnarzt ab"
   (mit "den Termin"-Prefix) wird vom FT-Modell nicht korrekt verarbeitet,
   da diese Phrasings nicht im Training waren. Lösung: Trainingsdaten um
   mehr diverse Phrasings erweitern und nachtrainieren.

3. **Reminder-Modell schwächer**: Der Reminder-Task hat eine geringere
   Tool-Call-Accuracy (75%) und eine höhere Halluzinationsrate (20%).
   Dies liegt an der Komplexität der relativen Zeit-Auflösung.

4. **JAX GPU Autotuning crasht auf Orin**: Der XLA-GPU-Autotuner crasht
   reproduzierbar auf dem Orin (sm_87). Abhilfe:
   `XLA_FLAGS="--xla_gpu_autotune_level=0"` (in train.py gesetzt).

5. **LD_LIBRARY_PATH muss geleert werden**: Der Orin hat System-CUDA 12.6
   in `/usr/local/cuda`. `LD_LIBRARY_PATH` zeigt dorthin, was den Loader
   die System-CUDA-12.6-Libs vor den pip-installierten CUDA-12.9-Wheels
   finden lässt. Abhilfe: `LD_LIBRARY_PATH` vor dem Training leeren
   (in train.py gesetzt).

## 11. Reproduktion

```bash
# 1. uv-Umgebung (Repo-Root)
uv venv --python 3.11
source .venv/bin/activate
uv pip install "cactus-needle[train,gpu]"

# 2. Datasets generieren
cd needle-only/calendar_ft
for task in calendar_write calendar_read reminder; do
    python build_dataset.py --task $task --count 2000 --seed 42 --out data/train/$task.jsonl
done

# 3. Trainieren (GPU, ~5.5h total)
python train.py --task calendar_write --epochs 10 --lora-rank 16 --lora-alpha 32 --lr 1e-4 --seed 42
python train.py --task calendar_read --epochs 10 --lora-rank 16 --lora-alpha 32 --lr 1e-4 --seed 42
python train.py --task reminder --epochs 10 --lora-rank 16 --lora-alpha 32 --lr 1e-4 --seed 42

# 4. Base-Modell evaluieren
python eval_model.py --task calendar_write --dataset data/eval/calendar_write.jsonl --out reports/base_calendar_write.json
python eval_model.py --task calendar_read --dataset data/eval/calendar_read.jsonl --out reports/base_calendar_read.json
python eval_model.py --task reminder --dataset data/eval/reminder.jsonl --out reports/base_reminder.json

# 5. E2E-Tests ausführen
python e2e_eval.py
```

## 12. Offizielle Quellen

- [Cactus Needle](https://github.com/cactus-compute/needle)
- [Needle Fine-Tuning](https://github.com/cactus-compute/needle/blob/main/doc/finetuning.md)
- [Needle API](https://github.com/cactus-compute/needle/blob/main/doc/apis.md)
- [JAX Installation](https://docs.jax.dev/en/latest/installation.html)
