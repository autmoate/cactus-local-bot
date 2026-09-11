#!/usr/bin/env python3
"""Ein-Kommando-Experiment: Calendar-FT Pipeline auf Jetson AGX Orin.

Führt den kompletten Flow aus:
  1. Environment-Check (JAX GPU verfügbar?)
  2. Dataset bauen (build_dataset.py)
  3. Dataset validieren (validate_dataset.py)
  4. Base-Modell evaluieren (eval_model.py)
  5. Fine-Tuning (train.py / needle finetune)
  6. .cact-Export (needle build)
  7. FT-Modell evaluieren (eval_model.py)
  8. Base vs. FT vergleichen (compare_runs.py)
  9. E2E-Evaluation (e2e_eval.py)

Am Ende wird PASS / FAIL gemeldet gegen die Mindestkriterien
(Nutzer-Spec Abschnitt 21).

Usage:
  uv run python needle-only/calendar_ft/run_experiment.py --task calendar_write
  uv run python needle-only/calendar_ft/run_experiment.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
ROOT = FT_DIR.parent.parent  # repo root

TASKS = ["calendar_write", "calendar_read", "reminder"]

# Mindestkriterien (Nutzer-Spec Abschnitt 21)
THRESHOLDS = {
    "router_accuracy": 0.98,
    "required_field_f1": 0.97,
    "hallucinated_field_rate": 0.01,
    "false_positive_tool_rate": 0.01,
    "full_frame_exact_match_improvement": True,  # FT > Base
}


def sh(cmd: list, env=None, timeout=86400) -> int:
    """Run a shell command and return its exit code."""
    print(f"  $ {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd, env=env or os.environ.copy(),
                            timeout=timeout)
    return result.returncode


def check_environment() -> bool:
    """Verify JAX GPU is available on the Jetson."""
    print("\n[1/9] Environment-Check (JAX GPU)")
    print("-" * 50)

    # Remove LD_LIBRARY_PATH (Jetson system CUDA conflict)
    if "LD_LIBRARY_PATH" in os.environ:
        del os.environ["LD_LIBRARY_PATH"]
    os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"
    os.environ["XLA_FLAGS"] = "--xla_gpu_autotune_level=0"

    code = sh([sys.executable, "-c",
               "import jax; d=jax.devices(); print(f'JAX {jax.__version__} devices={d}');"
               "assert any('gpu' in str(x).lower() or 'cuda' in str(x).lower() for x in d),"
               " 'No GPU device found'"])
    if code != 0:
        print("  ✗ JAX GPU not available!")
        return False
    print("  ✓ JAX GPU available")
    return True


def build_datasets(task: str, seed: int = 42, eval_count: int = 300) -> bool:
    """Build train and eval datasets for the given task."""
    print(f"\n[2/9] Dataset bauen: {task}")
    print("-" * 50)

    train_path = FT_DIR / "data" / "train" / f"{task}.jsonl"
    eval_path = FT_DIR / "data" / "eval" / f"{task}.jsonl"

    # Build train dataset
    train_count = 2000 if task == "calendar_write" else 1200
    code = sh([sys.executable, str(FT_DIR / "build_dataset.py"),
               "--task", task, "--count", str(train_count),
               "--seed", str(seed), "--out", str(train_path)])
    if code != 0:
        return False

    # Build eval dataset (excluding train queries)
    code = sh([sys.executable, str(FT_DIR / "build_dataset.py"),
               "--task", task, "--count", str(eval_count),
               "--seed", str(seed + 1), "--eval",
               "--exclude", str(train_path),
               "--out", str(eval_path)])
    return code == 0


def validate_datasets(task: str) -> bool:
    """Validate train and eval datasets."""
    print(f"\n[3/9] Dataset validieren: {task}")
    print("-" * 50)

    train_path = FT_DIR / "data" / "train" / f"{task}.jsonl"
    eval_path = FT_DIR / "data" / "eval" / f"{task}.jsonl"

    for path in [train_path, eval_path]:
        if not path.exists():
            print(f"  ✗ Missing: {path}")
            return False

    code = sh([sys.executable, str(FT_DIR / "validate_dataset.py"),
               "--dataset", str(train_path), "--task", task])
    if code != 0:
        return False

    code = sh([sys.executable, str(FT_DIR / "validate_dataset.py"),
               "--dataset", str(eval_path), "--task", task])
    return code == 0


def eval_base(task: str) -> dict | None:
    """Evaluate the base model (no weights)."""
    print(f"\n[4/9] Base-Modell evaluieren: {task}")
    print("-" * 50)

    eval_path = FT_DIR / "data" / "eval" / f"{task}.jsonl"
    out_path = FT_DIR / "reports" / f"base_{task}.json"

    code = sh([sys.executable, str(FT_DIR / "eval_model.py"),
               "--task", task, "--dataset", str(eval_path),
               "--out", str(out_path)])
    if code != 0 or not out_path.exists():
        return None

    return json.loads(out_path.read_text())


def train_task(task: str, epochs: int = 10, lora_rank: int = 16,
               lora_alpha: int = 32, lr: float = 1e-4,
               seed: int = 42) -> bool:
    """Fine-tune the given task with LoRA."""
    print(f"\n[5/9] Fine-Tuning: {task}")
    print("-" * 50)

    train_path = FT_DIR / "data" / "train" / f"{task}.jsonl"
    adapter_path = FT_DIR / "models" / f"{task}_lora.pkl"

    code = sh([sys.executable, "-c", f"""
import argparse, time

parser = argparse.ArgumentParser()
parser.add_argument("jsonl_path")
parser.add_argument("--checkpoint", default=None)
parser.add_argument("--epochs", type=int, default=3)
parser.add_argument("--batch-size", type=int, default=16)
parser.add_argument("--lr", type=float, default=1e-4)
parser.add_argument("--lora-rank", type=int, default=16)
parser.add_argument("--lora-alpha", type=float, default=32.0)
parser.add_argument("--max-len", type=int, default=1024)
parser.add_argument("--val-split", type=float, default=0.1)
parser.add_argument("--seed", type=int, default=0)
parser.add_argument("--generate", type=int, default=0)
parser.add_argument("--model", default="deepseek/deepseek-v4-flash")
parser.add_argument("--workers", type=int, default=8)
parser.add_argument("--checkpoint-dir", default="checkpoints")
parser.add_argument("--out", default=None)
parser.add_argument("--qat-bits", choices=["auto", "none", "2", "4"], default="auto")
args = parser.parse_args([
    "{train_path}",
    "--epochs", "{epochs}",
    "--lora-rank", "{lora_rank}",
    "--lora-alpha", "{lora_alpha}",
    "--lr", "{lr}",
    "--seed", "{seed}",
    "--out", "{adapter_path}"])

from needle.model.finetune import finetune_local
t0 = time.time()
finetune_local(args)
print(f"TOTAL TIME: {{time.time()-t0:.1f}}s")
"""])
    return code == 0


def build_cact(task: str) -> bool:
    """Build the .cact archive from the LoRA adapter."""
    print(f"\n[6/9] .cact-Export: {task}")
    print("-" * 50)

    adapter_path = FT_DIR / "models" / f"{task}_lora.pkl"
    cact_path = FT_DIR / "models" / f"{task}.cact"

    code = sh([sys.executable, "-c", f"""
from needle.model.export import write_export
from needle.model.run import load_checkpoint
from needle.model.finetune import merge_lora
from needle.model.tokenizer import get_tokenizer
from needle.model.architecture import effective_kv_window
import pickle, jax.numpy as jnp

params, config = load_checkpoint("checkpoints/needle2.pkl")
with open("{adapter_path}", "rb") as f:
    adapter = pickle.load(f)
lora = {{tuple(k.split("/")): {{"A": jnp.asarray(v["A"]), "B": jnp.asarray(v["B"])}}}}
lora = {{tuple(k.split("/")): {{"A": jnp.asarray(v["A"]), "B": jnp.asarray(v["B"])}} for k, v in adapter["lora"].items()}}
merged = merge_lora(params, lora, adapter["scale"])
info = write_export(merged, config, "{cact_path}",
                    bits=4, bits_map=None,
                    tokenizer=get_tokenizer(config.vocab_size),
                    kv_window=effective_kv_window(config))
print(f"Wrote {{info['path']}}  {{info['bytes']/1e6:.2f}} MB  W4A8")
"""])
    return code == 0 and cact_path.exists()


def eval_ft(task: str) -> dict | None:
    """Evaluate the fine-tuned .cact model."""
    print(f"\n[7/9] FT-Modell evaluieren: {task}")
    print("-" * 50)

    eval_path = FT_DIR / "data" / "eval" / f"{task}.jsonl"
    cact_path = FT_DIR / "models" / f"{task}.cact"
    out_path = FT_DIR / "reports" / f"{task}_ft.json"

    code = sh([sys.executable, str(FT_DIR / "eval_model.py"),
               "--task", task, "--dataset", str(eval_path),
               "--weights", str(cact_path),
               "--out", str(out_path)])
    if code != 0 or not out_path.exists():
        return None

    return json.loads(out_path.read_text())


def run_e2e() -> dict | None:
    """Run E2E evaluation."""
    print(f"\n[9/9] E2E-Evaluation")
    print("-" * 50)

    e2e_path = FT_DIR / "reports" / "e2e_results.json"
    code = sh([sys.executable, str(FT_DIR / "e2e_eval.py")])
    if code != 0 or not e2e_path.exists():
        return None

    return json.loads(e2e_path.read_text())


def check_thresholds(base: dict, ft: dict, e2e: dict | None) -> dict:
    """Check the minimum quality criteria (user spec section 21)."""
    results = {}

    # Router accuracy (from E2E results)
    if e2e:
        results["router_accuracy"] = {
            "value": e2e.get("route_accuracy", 0),
            "threshold": THRESHOLDS["router_accuracy"],
            "pass": e2e.get("route_accuracy", 0) >= THRESHOLDS["router_accuracy"],
        }
    else:
        results["router_accuracy"] = {"value": None, "pass": False,
                                       "reason": "E2E results not available"}

    # Required-field F1 (from FT eval)
    f1 = ft.get("field_f1", 0)
    results["required_field_f1"] = {
        "value": f1,
        "threshold": THRESHOLDS["required_field_f1"],
        "pass": f1 >= THRESHOLDS["required_field_f1"],
    }

    # Hallucinated field rate (from FT eval)
    halluc = ft.get("hallucinated_field_rate", 1)
    results["hallucinated_field_rate"] = {
        "value": halluc,
        "threshold": THRESHOLDS["hallucinated_field_rate"],
        "pass": halluc <= THRESHOLDS["hallucinated_field_rate"],
    }

    # False positive tool rate (from FT eval)
    fp = ft.get("false_positive_tool_rate", 1)
    results["false_positive_tool_rate"] = {
        "value": fp,
        "threshold": THRESHOLDS["false_positive_tool_rate"],
        "pass": fp <= THRESHOLDS["false_positive_tool_rate"],
    }

    # Full-frame exact match improvement (FT > Base)
    base_match = base.get("full_frame_exact_match", 0)
    ft_match = ft.get("full_frame_exact_match", 0)
    results["full_frame_exact_match_improvement"] = {
        "base": base_match,
        "ft": ft_match,
        "pass": ft_match > base_match,
    }

    return results


def run_experiment(task: str, epochs: int = 10, skip_train: bool = False) -> bool:
    """Run the complete experiment for a given task."""
    print("=" * 70)
    print(f"CALENDAR-FT EXPERIMENT: {task}")
    print("=" * 70)

    # Step 1: Environment check
    if not check_environment():
        print("\n❌ Environment check failed. Aborting.")
        return False

    # Step 2: Build datasets
    if not build_datasets(task):
        print("\n❌ Dataset building failed. Aborting.")
        return False

    # Step 3: Validate datasets
    if not validate_datasets(task):
        print("\n❌ Dataset validation failed. Aborting.")
        return False

    # Step 4: Evaluate base model
    base_metrics = eval_base(task)
    if base_metrics is None:
        print("\n❌ Base model evaluation failed. Aborting.")
        return False

    # Step 5: Fine-tune
    if not skip_train:
        if not train_task(task, epochs=epochs):
            print("\n❌ Fine-tuning failed. Aborting.")
            return False

        # Step 6: Build .cact
        if not build_cact(task):
            print("\n❌ .cact export failed. Aborting.")
            return False
    else:
        print("\n[5-6/9] Skipping training (--skip-train)")

    # Step 7: Evaluate FT model
    ft_metrics = eval_ft(task)
    if ft_metrics is None:
        print("\n❌ FT model evaluation failed. Aborting.")
        return False

    # Step 8: Compare base vs FT (inline summary)
    print(f"\n[8/9] Base vs. FT Vergleich")
    print("-" * 50)
    base_match = base_metrics.get("full_frame_exact_match", 0)
    ft_match = ft_metrics.get("full_frame_exact_match", 0)
    base_f1 = base_metrics.get("field_f1", 0)
    ft_f1 = ft_metrics.get("field_f1", 0)
    print(f"  Full-frame exact match:  {base_match:.4f} → {ft_match:.4f} ({(ft_match-base_match)*100:+.1f}%)")
    print(f"  Field F1:                {base_f1:.4f} → {ft_f1:.4f} ({(ft_f1-base_f1)*100:+.1f}%)")
    print(f"  Hallucinated field rate: {ft_metrics.get('hallucinated_field_rate', 0):.4f}")
    print(f"  False positive tool rate:{ft_metrics.get('false_positive_tool_rate', 0):.4f}")

    # Step 9: E2E evaluation (only for calendar_write as the main task)
    e2e_metrics = None
    if task == "calendar_write":
        e2e_metrics = run_e2e()
        if e2e_metrics:
            print(f"\n  E2E Pass Rate: {e2e_metrics['pass_rate']:.1%}")
            print(f"  Route Accuracy: {e2e_metrics['route_accuracy']:.1%}")

    # Check thresholds
    threshold_results = check_thresholds(base_metrics, ft_metrics, e2e_metrics)

    print(f"\n{'='*70}")
    print(f"MINDESKRITERIEN (Nutzer-Spec Abschnitt 21)")
    print(f"{'='*70}")
    all_pass = True
    for name, result in threshold_results.items():
        if isinstance(result, dict) and "pass" in result:
            status = "✓" if result["pass"] else "✗"
            if not result["pass"]:
                all_pass = False
            val = result.get("value", result.get("ft", "?"))
            thr = result.get("threshold", "?")
            print(f"  {status} {name}: {val} (threshold: {thr})")

    print(f"\n{'='*70}")
    overall = "PASS" if all_pass else "FAIL (thresholds not met)"
    print(f"ERGEBNIS: {overall}")
    print(f"{'='*70}")

    return all_pass


def main():
    ap = argparse.ArgumentParser(description="Calendar-FT experiment runner")
    ap.add_argument("--task", choices=TASKS,
                    help="Run experiment for a specific task")
    ap.add_argument("--all", action="store_true",
                    help="Run experiments for all tasks")
    ap.add_argument("--epochs", type=int, default=10,
                    help="Training epochs (default: 10)")
    ap.add_argument("--skip-train", action="store_true",
                    help="Skip training (use existing models)")
    args = ap.parse_args()

    if args.all:
        tasks = TASKS
    elif args.task:
        tasks = [args.task]
    else:
        ap.print_help()
        sys.exit(1)

    results = {}
    for task in tasks:
        t0 = time.time()
        success = run_experiment(task, epochs=args.epochs,
                                 skip_train=args.skip_train)
        results[task] = {
            "success": success,
            "time_s": round(time.time() - t0, 1),
        }

    print("\n" + "=" * 70)
    print("ZUSAMMENFASSUNG")
    print("=" * 70)
    for task, result in results.items():
        status = "PASS" if result["success"] else "FAIL"
        print(f"  {status} {task:<18} ({result['time_s']}s)")

    all_success = all(r["success"] for r in results.values())
    print(f"\nOverall: {'PASS' if all_success else 'FAIL'}")
    sys.exit(0 if all_success else 1)


if __name__ == "__main__":
    main()
