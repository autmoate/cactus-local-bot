#!/usr/bin/env python3
"""Upload calendar-FT .cact models to HuggingFace.

Creates three repos under the `autmoate` org with proper model cards:
  1. autmoate/cactus-needle2-calendar-write-dt
  2. autmoate/cactus-needle2-calendar-read-dt
  3. autmoate/cactus-needle2-reminder-dt

Usage:
  uv run python needle-only/calendar_ft/upload_hf.py --dry-run   # Preview only
  uv run python needle-only/calendar_ft/upload_hf.py             # Actually upload
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Load .env WITHOUT reading its contents
from dotenv import load_dotenv
load_dotenv()

FT_DIR = Path(__file__).resolve().parent
MODELS_DIR = FT_DIR / "models"

# ============================================================================
# Model card templates
# ============================================================================

CARD_HEADER = """---
language: de
license: apache-2.0
base_model: Cactus-Compute/needle2
library_name: cactus-needle
tags:
- needle
- tool-calling
- calendar
- german
- on-device
- edge
---

# {title}

{description}

## Base Model

This model is a fine-tune of [Cactus-Compute/needle2](https://huggingface.co/Cactus-Compute/needle2),
a 45M-parameter tool-calling model that runs in 28MB of RAM. See the
[original model card](https://huggingface.co/Cactus-Compute/needle2) for details
on the Simple Attention Network architecture, deployment targets, and benchmarks.

## What Was Done

LoRA fine-tuning (rank 16, alpha 32) on ~{n_train} German calendar examples
(train split), evaluated on ~{n_eval} held-out German examples (eval split).

- **Backend:** JAX 0.10.2 on CUDA (Jetson AGX Orin 64GB, sm_87)
- **Training:** {epochs} epochs, lr 1e-4, batch size 16, cosine decay with warmup
- **Numerics:** Quantization-aware training (CQ mixed STE + A8)
- **Export:** W4A8 (4-bit weights, 8-bit activations)
- **Size:** ~23 MB per .cact archive

## Usage

```python
import needle

# Load the fine-tuned calendar model
agent = needle.Needle(
    tools=[{tool_schema_inline}],
    weights="{hf_repo_id}.cact",  # download from HF, pass local path
)

# Example: {example_query}
response = agent.complete("{example_query}")
print(response["function_calls"])
```

## Evaluation Results (Base vs Fine-Tuned)

{eval_table}

## Training Data

{dataset_info}

## Citation

If you use this model, please cite both the base model and this fine-tune:

```bibtex
{base_bibtex}
```

```bibtex
{ft_bibtex}
```

## License

Apache 2.0 (inherited from the base model).
"""

# ============================================================================
# Per-model configurations
# ============================================================================

MODELS = {
    "calendar_write": {
        "hf_repo": "autmoate/cactus-needle2-calendar-write-dt",
        "title": "Needle 2 Calendar Write (German)",
        "description": (
            "Task-specific fine-tune of Needle 2 for **creating, moving, and "
            "canceling calendar appointments in German**. Extracts literal "
            "spans (title, date, time, location, participants, action_span, "
            "modifier) from natural language queries. Every argument value is "
            "grounded as a substring of the input query."
        ),
        "example_query": "Trag Zahnarzt am Montag um 9 Uhr ein",
        "tool_name": "calendar_write",
    },
    "calendar_read": {
        "hf_repo": "autmoate/cactus-needle2-calendar-read-dt",
        "title": "Needle 2 Calendar Read (German)",
        "description": (
            "Task-specific fine-tune of Needle 2 for **reading and querying "
            "calendars in German**. Handles questions like 'Was steht morgen "
            "an?', 'Wann hat Lisa Termine?', 'Wann sind Lisa und Max frei?'. "
            "Extracts literal spans (query_span, when, person, persons, target) "
            "from natural language queries."
        ),
        "example_query": "Was steht morgen an",
        "tool_name": "calendar_read",
    },
    "reminder": {
        "hf_repo": "autmoate/cactus-needle2-reminder-dt",
        "title": "Needle 2 Reminder (German)",
        "description": (
            "Task-specific fine-tune of Needle 2 for **setting reminders and "
            "alarms in German**. Handles absolute times ('Erinnere mich morgen "
            "um 8 Uhr an X'), relative offsets ('20 Minuten vorher'), and "
            "person-specific reminders. Extracts literal spans (target, when, "
            "relative, person)."
        ),
        "example_query": "Erinnere mich 20 Minuten vor dem Zahnarzt",
        "tool_name": "reminder",
    },
}

BASE_BIBTEX = """@misc{needle2_2026,
  title        = {Needle 2: A 45M-Parameter Foundation Tool-Calling Model for Tiny Devices},
  author       = {Ndubuaku, Henry and Mosoyan, Karen and Mroz, Jakub and Cylich, Noah and
                  Kumar, Satyajit and Sandhu, Parkirat and Shemet, Roman and Lee, Justin H.},
  year         = {2026},
  organization = {Cactus Compute, Inc.},
  howpublished = {\\url{https://github.com/cactus-compute/needle}}
}"""

FT_BIBTEX = """@misc{autmoate_calendar_ft_2026,
  title        = {Task-Specific Needle 2 Fine-Tunes for German Calendar Operations},
  author       = {autmoate},
  year         = {2026},
  howpublished = {\\url{https://huggingface.co/autmoate}},
  note         = {LoRA fine-tune of Cactus-Compute/needle2 on German calendar data}
}"""

# ============================================================================
# Helper functions
# ============================================================================


def load_jsonl_count(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path) as f:
        return sum(1 for line in f if line.strip())


def load_tool_schema(task: str) -> dict:
    schema_path = FT_DIR / "schemas" / f"{task}.json"
    return json.loads(schema_path.read_text())


def format_eval_table(task: str) -> str:
    """Format the Base vs FT comparison as a markdown table."""
    reports = FT_DIR / "reports"
    base_path = reports / f"base_{task}.json"
    ft_path = reports / f"{task}_ft.json"

    if not base_path.exists() or not ft_path.exists():
        return "_Evaluation data not available._"

    base = json.loads(base_path.read_text())
    ft = json.loads(ft_path.read_text())

    metrics = [
        ("Full-frame Exact Match", "full_frame_exact_match"),
        ("Tool Call Accuracy", "tool_call_accuracy"),
        ("Field Precision", "field_precision"),
        ("Field Recall", "field_recall"),
        ("Field F1", "field_f1"),
        ("Hallucinated Field Rate", "hallucinated_field_rate"),
        ("False Positive Tool Rate", "false_positive_tool_rate"),
        ("False Negative Tool Rate", "false_negative_tool_rate"),
    ]

    rows = ["| Metric | Base | Fine-Tuned |", "|---|---|---|"]
    for label, key in metrics:
        b = base.get(key, 0)
        f = ft.get(key, 0)
        if "rate" in key:
            rows.append(f"| {label} | {b:.1%} | {f:.1%} |")
        else:
            rows.append(f"| {label} | {b:.4f} | {f:.4f} |")

    # Latency
    b_lat = base.get("latency_ms", {}).get("mean", 0)
    f_lat = ft.get("latency_ms", {}).get("mean", 0)
    rows.append(f"| Mean Latency (ms) | {b_lat:.0f} | {f_lat:.0f} |")

    return "\n".join(rows)


def build_model_card(task: str) -> str:
    """Build the README.md model card for the given task."""
    cfg = MODELS[task]

    # Load dataset info
    train_path = FT_DIR / "data" / "train" / f"{task}.jsonl"
    eval_path = FT_DIR / "data" / "eval" / f"{task}.jsonl"
    n_train = load_jsonl_count(train_path)
    n_eval = load_jsonl_count(eval_path)

    # Load tool schema for inline display
    schema = load_tool_schema(cfg["tool_name"])
    schema_inline = json.dumps([schema], indent=2, ensure_ascii=False)

    # Dataset description
    dataset_info = (
        f"- **Training examples:** {n_train} (deterministic, seedable)\n"
        f"- **Evaluation examples:** {n_eval} (held-out values and phrasings)\n"
        f"- **Language:** German only\n"
        f"- **Negative examples:** ~12% (cross-task + off-topic)\n"
        f"- **Grounding:** Every argument value is a literal substring of the query\n\n"
        f"Datasets generated with a template-based dataset builder "
        f"(`needle-only/calendar_ft/build_dataset.py`)."
    )

    card = CARD_HEADER.format(
        title=cfg["title"],
        description=cfg["description"],
        n_train=n_train,
        n_eval=n_eval,
        epochs=10,
        tool_schema_inline=schema_inline,
        hf_repo_id=cfg["hf_repo"],
        example_query=cfg["example_query"],
        eval_table=format_eval_table(task),
        dataset_info=dataset_info,
        base_bibtex=BASE_BIBTEX,
        ft_bibtex=FT_BIBTEX,
    )

    return card


# ============================================================================
# Main upload logic
# ============================================================================


def main():
    ap = argparse.ArgumentParser(description="Upload calendar-FT models to HuggingFace")
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview the model cards without uploading")
    ap.add_argument("--task", choices=list(MODELS.keys()),
                    help="Upload only a specific task model")
    args = ap.parse_args()

    tasks = [args.task] if args.task else list(MODELS.keys())

    # Check for HF_TOKEN
    import os
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token and not args.dry_run:
        print("ERROR: HF_TOKEN not found in environment or .env")
        sys.exit(1)

    from huggingface_hub import HfApi

    if not args.dry_run:
        api = HfApi()
        # Verify token works
        try:
            user = api.whoami()
            print(f"Authenticated as: {user.get('name', 'unknown')}")
        except Exception as e:
            print(f"ERROR: Could not authenticate with HF token: {e}")
            sys.exit(1)

    for task in tasks:
        cfg = MODELS[task]
        cact_path = MODELS_DIR / f"{task}.cact"

        if not cact_path.exists():
            print(f"WARNING: {cact_path} does not exist, skipping {task}")
            continue

        print(f"\n{'='*70}")
        print(f"Model: {cfg['title']}")
        print(f"Repo:  {cfg['hf_repo']}")
        print(f"File:  {cact_path} ({cact_path.stat().st_size / 1e6:.1f} MB)")
        print(f"{'='*70}")

        # Build model card
        card = build_model_card(task)

        if args.dry_run:
            print(f"\n--- Model Card Preview ({task}) ---\n")
            print(card[:3000])
            if len(card) > 3000:
                print(f"\n... ({len(card)} chars total)")
            continue

        # Create repo if it doesn't exist
        repo_id = cfg["hf_repo"]
        try:
            api.create_repo(repo_id=repo_id, repo_type="model", exist_ok=True)
            print(f"  ✓ Repo ready: {repo_id}")
        except Exception as e:
            print(f"  ✗ Failed to create repo {repo_id}: {e}")
            continue

        # Upload .cact file
        try:
            api.upload_file(
                path_or_fileobj=str(cact_path),
                path_in_repo=f"{task}.cact",
                repo_id=repo_id,
                repo_type="model",
            )
            print(f"  ✓ Uploaded: {task}.cact")
        except Exception as e:
            print(f"  ✗ Failed to upload {task}.cact: {e}")
            continue

        # Upload model card (README.md)
        try:
            api.upload_file(
                path_or_fileobj=card.encode("utf-8"),
                path_in_repo="README.md",
                repo_id=repo_id,
                repo_type="model",
            )
            print(f"  ✓ Uploaded: README.md (model card)")
        except Exception as e:
            print(f"  ✗ Failed to upload README.md: {e}")
            continue

        # Upload tool schema for reference
        schema_path = FT_DIR / "schemas" / f"{task}.json"
        if schema_path.exists():
            try:
                api.upload_file(
                    path_or_fileobj=str(schema_path),
                    path_in_repo=f"tools_{task}.json",
                    repo_id=repo_id,
                    repo_type="model",
                )
                print(f"  ✓ Uploaded: tools_{task}.json (tool schema)")
            except Exception as e:
                print(f"  ⚠ Failed to upload tool schema: {e}")

        print(f"\n  🎉 Successfully uploaded {repo_id}")
        print(f"     https://huggingface.co/{repo_id}")

    if args.dry_run:
        print("\n\nDry run complete. Run without --dry-run to upload.")


if __name__ == "__main__":
    main()
