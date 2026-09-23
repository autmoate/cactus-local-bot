---
base_model: Cactus-Compute/needle3
library_name: cactus-needle
license: apache-2.0
language:
- de
- en
tags:
- tool-calling
- function-calling
- calendar
- on-device
- needle
- multi-call
---

# Needle 3 — Calendar Tool-Calling (German), task-tuned (research candidate)

Local LoRA fine-tune of [Needle 3](https://huggingface.co/Cactus-Compute/needle3)
on a **preservation mix** for a local calendar agent (5 production tools, German).
Its distinguishing strength is **multi-action requests** (several calls in one
turn) — it is **not** better than the Needle-2 FT on precise atomic arguments.

> **Honest positioning:** If you need maximum atomic accuracy, use the Needle-2 FT
> (`autmoate/cactus-needle2-calendar`, atomic exact 0.977). This model is the
> candidate for the *multi-call / decomposition* role in a cascade. It is published
> as a research artifact, not as the production default.

## Model details

| | |
|---|---|
| Base | `Cactus-Compute/needle3` (121M, 20 layers, laddered 2–20) |
| Method | local LoRA rank 32 / alpha 32, 5 epochs, batch 16, max-len 1024, seed 42, QAT through the export scheme |
| Dataset | "v4 preservation mix": 25 072 rows — 71.5 % atomic · 17.8 % independent multi-call · 10.7 % negatives/near-negatives |
| Export | full 20-layer `.cact`, 63.4 MB (`needle build`), **engine-version bound** |
| Package | `cactus-needle==3.0.4` (an archive built by another engine version will not load) |
| Hardware | Modal A100-40GB, 6 630 s (~110 min) |
| Languages | German (primary), simple English |
| License | Apache-2.0 (inherited from the base) |

## Tool schema (5 production tools)

`calendar_list` · `calendar_find_slot` · `calendar_create` · `calendar_move` · `calendar_delete`
— the same schemas as the dataset. Load with the identical tool list and system facts:

```python
import needle, json

tools = json.load(open("tools.json"))          # 5 schemas, frozen order
agent = needle.Needle(
    tools=tools,
    system="date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi",
    weights="calendar-needle3-v4-e5.cact",
    auto_date=False,                            # match the training system facts
)
agent.reset()
print(agent.complete("Trag morgen 10 Uhr Zahnarzt ein und Freitag 15 Uhr Sport.")["function_calls"])
```

**Gold convention:** sparse / evidenced-only — arguments contain only literal spans
from the query; optional fields without evidence are omitted (`arguments: {}` is legal).
Resolution to absolute times/IDs is done deterministically downstream (Python).

## Measured results (frozen harness, see the repository)

| Axis | This model | Needle-2 FT (reference) |
|---|---|---|
| A1 atomic tool / args / exact | **1.000 / 0.889 / 0.886** | 0.991 / 0.977 / **0.977** |
| A1 challenge args / exact | 0.720 / 0.600 | **0.840 / 0.680** |
| A2 one-shot multi (10 cases, all_actions) | **0.90** | 0.30 |
| Off-topic refusal accuracy | **100 %** | 97.5 % |
| False refusals on valid requests | **0.00 %** | 0.31 % |
| C production E2E (`final_db_ok`, 25 cases) | **80 %** | 72 % |
| Median latency | **188 ms** | 261 ms |

3–5 epochs matter most (atomic 0.739 → 0.877 → 0.886); the atomic ceiling stayed
~0.89, so the model does **not** reach the Needle-2 FT atomically.

## Known limitations

- **Not the best atomic caller**: 0.886 exact vs. 0.977 for the Needle-2 FT.
- **No calibrated confidence**: local fine-tuning does not train the confidence
  head and `needle build` drops it → `confidence` is `None`. Do not route on it.
- **Result-dependent chains are not solved** (`find_slot → create` etc., 0/10):
  the model does not carry a tool result (e.g. a returned slot) into the next call.
  Use an explicit planner for those goals.
- **Toolset-sensitive**: narrowing it to write-only degrades accuracy
  (0.886 → 0.816); it expects the full 5-tool context.
- Trained for single-turn tool calls; collision logic, multi-step planning and
  multi-turn continuation are intentionally outside the model (Python / a planner).
- Engine-version bound `.cact`: requires a matching Needle-3 engine
  (`cactus-needle==3.0.4` or the platform engine for that generation).

## Training data

Not published here (privacy review pending). The dataset is deterministic and
regenerable: `experiments/ft/build_v4.py` (seed 42) builds train/validation from
the 5 production schemas; the frozen test/challenge sets live in the repository.

## Reproduction

```bash
# in the upstream repo (needle-only/)
uv venv .venv-ft3 --python 3.12
uv pip install --python .venv-ft3/bin/python "cactus-needle[train,gpu]==3.0.4"
PYTHONPATH=src .venv-ft3/bin/python experiments/ft/build_v4.py
PYTHONPATH=src .venv-ft3/bin/python experiments/ft/modal_train.py \
    --plan "v4:r32:e5" --seeds 42 --batch-size 16 --gpu A100-40GB
PYTHONPATH=src .venv-ft3/bin/python experiments/ft/arch_bench.py --pipeline fallback --tag n3-v4-e5
```

Artifact sha256 (this file): see `manifest.json` in this repository.
