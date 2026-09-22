---
base_model: Cactus-Compute/needle2
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
---

# Needle 2 — Calendar Tool-Calling (German), task-tuned

LoRA-fine-tuned [Needle 2](https://huggingface.co/Cactus-Compute/needle2) (45M) for a
**local calendar agent**: short German/English calendar instructions → the exactly
right tool call with **evidence-only (sparse) arguments**. Base stays Needle 2;
this repo ships the merged `.cact` archive for the 5-tool production calendar set.

## Model details

| | |
|---|---|
| Base | `Cactus-Compute/needle2` (45M), engine 2 |
| Method | LoRA rank 16 / alpha 32, lr 1e-4, 8 epochs, QAT (`--qat-bits auto`) |
| Export | W4A8 `.cact`, 13.7 MB, 405 tensors (merged) |
| Hardware | NVIDIA RTX 3090 (WSL2), ~2.9 h training |
| Package | `cactus-needle==2.0.13` |
| Artifact | `calendar-needle2-seed44.cact` (sha256 below) |
| Languages | German (primary), simple English |
| Domain | local calendar tool calling (Raspberry Pi 5, offline) |

## Tool schema (5 production tools)

`calendar_create`, `calendar_move`, `calendar_delete`, `calendar_list`, `calendar_find_slot`.
Load with the same schemas you trained on (expected shape — compact objects):

```python
import needle

tools = [
  {"name": "calendar_create", "description": "...", "parameters": {"type": "object", "properties": {
      "title": {"type": "string"}, "date": {"type": "string"}, "until": {"type": "string"},
      "time": {"type": "string"}, "end_time": {"type": "string"},
      "participants": {"type": "string"}}}},
  # ... calendar_move/delete/list/find_slot
]

agent = needle.Needle(tools=tools,
                      system="date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi",
                      weights="calendar-needle2-seed44.cact")

print(agent.complete("Trag morgen 10 Uhr Zahnarzt ein.")["function_calls"])
# [{'name': 'calendar_create', 'arguments': {'title': 'Zahnarzt', 'date': 'morgen', 'time': '10 Uhr'}}]
```

**System facts** are facts, not instructions (`date:`/`locale:`/`device:`). The model was
trained with `date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi`.

### Gold convention: sparse / evidenced-only
Arguments contain **only literal spans evidenced in the query**. Optional fields without
evidence are **omitted, not defaulted** — an empty `arguments: {}` is legal (e.g. a bare
`calendar_list`). Downstream code resolves spans to times/IDs deterministically. This
convention was validated A/B (`exact_sparse` 0.887 vs `exact_full` 0.186 on the same outputs).

## Training data (not included here)

Synthetic, deterministic (seed 42) German/English templates over the 5 production schemas:
10k train / 1k validation / 1.8k test, ~9 % negatives, family-exclusive splits, held-out
value pools; plus a 25-item frozen challenge set of real (anonymized) usage traces.
Dataset is **not published** in this repo (privacy review pending).

## Evaluation (synthetic test n=1800 / challenge n=25 / final-DB 25 cases)

| Metric | Base | this model |
|---|---|---|
| exact_args (test) | 0.113 | **0.977** |
| args_ok semantic (test) | 0.449 | **0.977** |
| tool_ok (test) | 0.863 | 0.991 |
| false refusals on valid requests | 53/1638 | **5/1638** |
| correct refusals on off-topic | 57 % | **98 %** |
| exact_args (challenge) | 0.120 | 0.680 |
| final-DB end-to-end | 68 % | **72 %** |
| median latency | 1206 ms | **261 ms** |

3-seed variance on `exact_args` (test): 0.974–0.981 (release = seed 44).

## Known limitations

- Trained for **single-request tool calls**; multi-intent decomposition is done upstream.
- **No calibrated confidence**: finetuning does not update the confidence head, so
  `confidence` is `None` — validate calls deterministically instead.
- Non-English tokenization costs ~1.7× tokens; the 256-token window is a real budget.
- Challenge-set gaps remain for `until` ranges and some colloquial phrasings.
- End-to-end calendar correctness is ~70 %: the remaining errors are in the
  **resolver/verification** layer (deterministic Python), not in argument extraction.
- No collision logic, no multi-step planning — intentionally outside the model.

## License

Inherits Apache-2.0 from `Cactus-Compute/needle2`.

## Reproduce

```bash
uv run python experiments/ft/train_rtx.py --run-name sa-r16-lr1e-4-e8-seed44 \
    --rank 16 --lr 1e-4 --epochs 8 --seed 44 --batch-size 8
uv run python experiments/ft/model_manifest.py     # hashes/provenance
```

Artifact sha256 (seed 44): `ba3212abcac8355c026178d98dc6f4d95d2822064fd9cc1d8d4cc57bf471add9`
