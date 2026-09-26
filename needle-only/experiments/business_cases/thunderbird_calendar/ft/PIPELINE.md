# FT Pipeline (build → validate → train → gate)

All under `business_cases/thunderbird_calendar/ft/`. No production code touched.

| file | role |
|---|---|
| `dataset_spec_tb.py` | deterministic pools + split-exclusive families |
| `build_dataset_tb.py` | writes `data/*.jsonl`, `tools_tb.json`, `manifest.json` |
| `validate_dataset_tb.py` | hard validator + `coverage.json` |
| `modal_train_tb.py` | Modal N2 LoRA runner (`cactus-needle[train,gpu]==2.0.13`) |
| `gate_tb.py` | pre-registered promotion gate (exit != 0 on fail) |
| `tools_tb.json` | frozen `extract_event` (when-first) schema |
| `manifest.json` | provenance hashes, counts, seed, target mix |

## Commands

```sh
cd needle-only

# build (seed 42; exact counts) + hard validation
uv run python experiments/business_cases/thunderbird_calendar/ft/build_dataset_tb.py
uv run python experiments/business_cases/thunderbird_calendar/ft/validate_dataset_tb.py

# Modal smoke (function check only: start, LoRA saved, .cact built), then ≤3 runs
uv run --extra modal modal run experiments/business_cases/thunderbird_calendar/ft/modal_train_tb.py \
    --runs smoke --epochs 1 --batch-size 8
uv run --extra modal modal run experiments/business_cases/thunderbird_calendar/ft/modal_train_tb.py \
    --plan train:r16:e5,train:r16:e8,train:r8:e8 --seeds 42 --gpu A100-40GB

# evaluate a candidate against the frozen sets
uv run python experiments/business_cases/thunderbird_calendar/eval.py --backend n2 \
    --weights experiments/business_cases/thunderbird_calendar/ft/models/<run>.cact \
    --cases experiments/business_cases/thunderbird_calendar/ft/realism_challenge.jsonl \
    --out experiments/business_cases/thunderbird_calendar/ft/reports/<run>_realism.json
# ... and hard_challenge.jsonl -> <run>_hard.json

# gate
uv run python experiments/business_cases/thunderbird_calendar/ft/gate_tb.py \
    --ft-realism .../<run>_realism.json --ft-hard .../<run>_hard.json \
    --base-hard .../n2_hard.json
```

## Dataset stand (seed 42)

| split | n | positives | message | selection | negative | review |
|---|---|---|---|---|---|---|
| train | 7000 | 0.77 | 5460 | 1540 | 1610 | 385 |
| validation | 800 | 0.77 | 624 | 176 | 184 | 48 |
| test | 1400 | 0.77 | 1092 | 308 | 322 | 72 |

Negative = ~18% near-miss (tentative/cancel/reschedule/deadline/past/quoted
old/offer) + ~5% off-topic. Validator enforces verbatim spans, end-time and
timezone coverage, split-exclusive families and no held-out leakage.

## Gate (FT_PLAN.md §9)

`supported_final_event_ok ≥ 0.85` · `selection ≥ 0.90` · `FP ≤ 0.05` (and per
near-miss class) · `supported_approval_ready ≥ 0.80` · `review_routing_ok ≥ 0.90`
· `evidence_grounded_rate ≥ 0.95` · hard set ≥ base + 0.10.
