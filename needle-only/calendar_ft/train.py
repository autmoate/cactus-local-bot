#!/usr/bin/env python3
"""Train a task-specific LoRA adapter and build a .cact archive.

This is a thin wrapper around the `needle` CLI that:
  1. Sets up the Jetson AGX Orin environment correctly
     (unset LD_LIBRARY_PATH to avoid system-CUDA conflicts,
     disable XLA GPU autotuning to avoid the sm_87 crash).
  2. Pre-fetches the base checkpoint (auto-download from Hugging Face).
  3. Runs `needle finetune` with the given hyperparameters.
  4. Runs `needle build` to merge the LoRA and export a .cact.

Usage:
  python train.py --task calendar_write --epochs 10 --lora-rank 16 --lora-alpha 32
  python train.py --task calendar_read --epochs 10 --lora-rank 16 --lora-alpha 32
  python train.py --task reminder --epochs 10 --lora-rank 16 --lora-alpha 32
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

DATA_DIR = FT_DIR / "data"
MODELS_DIR = FT_DIR / "models"
REPORTS_DIR = FT_DIR / "reports"
CHECKPOINTS_DIR = ROOT / "checkpoints"

DEFAULT_BASE = "checkpoints/needle2.pkl"


def setup_jetson_env():
    """Set environment variables for Jetson AGX Orin GPU training.

    Three workarounds are required on the AGX Orin (see reports/environment.md):

    1. Remove LD_LIBRARY_PATH. The Jetson has system CUDA 12.6 in
       /usr/local/cuda; LD_LIBRARY_PATH points there, so the loader prefers
       the system CUDA 12.6 libraries over the pip-installed CUDA 12.9 wheels,
       breaking cuSPARSE symbol resolution ("Unable to load cuSPARSE").
       Without LD_LIBRARY_PATH, the pip wheels' RUNPATHs resolve intra-pip
       dependencies correctly.

    2. Disable XLA GPU autotuning (--xla_gpu_autotune_level=0). The autotuner
       crashes on the Orin (sm_87): "Could not load RepeatBufferKernel: ...
       cudaErrorNoKernelImageForDevice".

    3. Disable XLA memory preallocation (XLA_PYTHON_CLIENT_PREALLOCATE=false).
       Default preallocation grabs ~75% of the 64 GB unified memory (~46 GiB),
       which OOMs on the Orin (~42 GiB free) and cascades into cublas handle
       failures.
    """
    # (1) Remove LD_LIBRARY_PATH to avoid Jetson system CUDA conflicts
    if "LD_LIBRARY_PATH" in os.environ:
        del os.environ["LD_LIBRARY_PATH"]

    # (2) Disable XLA GPU autotuning to avoid the sm_87 RedzoneBuffers crash
    xla_flags = os.environ.get("XLA_FLAGS", "")
    if "--xla_gpu_autotune_level" not in xla_flags:
        os.environ["XLA_FLAGS"] = (
            xla_flags + " --xla_gpu_autotune_level=0").strip()

    # (3) Disable XLA memory preallocation for unified-memory Orin
    os.environ.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")


def ensure_base_checkpoint() -> Path:
    """Ensure the base Needle checkpoint is available locally.

    The needle.model.run.load_checkpoint function auto-downloads from
    Hugging Face (Cactus-Compute/needle2) when the file doesn't exist.
    We call it once to ensure the checkpoint is cached.
    """
    ckpt_path = ROOT / DEFAULT_BASE
    if ckpt_path.exists():
        return ckpt_path

    print(f"[train] Fetching base checkpoint: {DEFAULT_BASE}")
    sys.path.insert(0, str(ROOT))

    # Use the needle library's checkpoint loading to trigger the download
    from needle.model.run import load_checkpoint
    params, config = load_checkpoint(str(ckpt_path))
    print(f"[train] Base checkpoint ready: {ckpt_path}")
    print(f"[train] Config: {config}")
    return ckpt_path


def run_finetune(task: str, train_jsonl: Path, epochs: int, lora_rank: int,
                 lora_alpha: float, lr: float, batch_size: int, seed: int,
                 out_adapter: Path) -> bool:
    """Run needle finetune for the given task."""
    cmd = [
        sys.executable, "-m", "needle.cli", "finetune",
        str(train_jsonl),
        "--epochs", str(epochs),
        "--lora-rank", str(lora_rank),
        "--lora-alpha", str(lora_alpha),
        "--lr", str(lr),
        "--batch-size", str(batch_size),
        "--seed", str(seed),
        "--out", str(out_adapter),
    ]
    print(f"[train] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=os.environ.copy(), cwd=str(ROOT))
    return result.returncode == 0


def run_build(adapter_path: Path, out_cact: Path, base_checkpoint: Path) -> bool:
    """Run needle build to merge LoRA and export .cact."""
    cmd = [
        sys.executable, "-m", "needle.cli", "build",
        str(base_checkpoint),
        "--lora", str(adapter_path),
        "--out", str(out_cact),
    ]
    print(f"[build] Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, env=os.environ.copy(), cwd=str(ROOT))
    return result.returncode == 0


def main():
    ap = argparse.ArgumentParser(description="Calendar-FT training wrapper")
    ap.add_argument("--task", required=True,
                    choices=["calendar_write", "calendar_read", "reminder"])
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--lora-rank", type=int, default=16)
    ap.add_argument("--lora-alpha", type=float, default=32)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--train-data", default=None,
                    help="Override training data path")
    args = ap.parse_args()

    setup_jetson_env()

    # Determine data path
    if args.train_data:
        train_jsonl = Path(args.train_data)
    else:
        train_jsonl = DATA_DIR / "train" / f"{args.task}.jsonl"

    if not train_jsonl.exists():
        raise SystemExit(f"Training data not found: {train_jsonl}. "
                         f"Run build_dataset.py first.")

    # Setup output paths
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    adapter_path = MODELS_DIR / f"{args.task}_lora.pkl"
    cact_path = MODELS_DIR / f"{args.task}.cact"

    # Ensure base checkpoint is available
    base_checkpoint = ensure_base_checkpoint()

    # Run finetune
    t0 = time.time()
    success = run_finetune(
        args.task, train_jsonl, args.epochs, args.lora_rank,
        args.lora_alpha, args.lr, args.batch_size, args.seed,
        adapter_path)
    train_time = time.time() - t0

    if not success:
        raise SystemExit(f"Finetune failed for {args.task}")

    print(f"\n[train] Training completed in {train_time:.1f}s")

    # Build .cact
    t0 = time.time()
    success = run_build(adapter_path, cact_path, base_checkpoint)
    build_time = time.time() - t0

    if not success:
        raise SystemExit(f"Build failed for {args.task}")

    print(f"\n[build] .cact build completed in {build_time:.1f}s")

    # Summary
    adapter_size = adapter_path.stat().st_size if adapter_path.exists() else 0
    cact_size = cact_path.stat().st_size if cact_path.exists() else 0
    summary = {
        "task": args.task,
        "epochs": args.epochs,
        "lora_rank": args.lora_rank,
        "lora_alpha": args.lora_alpha,
        "lr": args.lr,
        "batch_size": args.batch_size,
        "seed": args.seed,
        "train_time_s": round(train_time, 1),
        "build_time_s": round(build_time, 1),
        "adapter_path": str(adapter_path),
        "adapter_size_bytes": adapter_size,
        "cact_path": str(cact_path),
        "cact_size_bytes": cact_size,
    }

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
