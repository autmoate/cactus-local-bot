#!/usr/bin/env python3
"""Cloud-FT auf Modal (Needle 3, LoRA) — umgeht die WSL-Thermik/Limit-Probleme.

Die Box hier bricht Needle-3-Trainings nach ~25–30 min mit CUDA-Faults ab
(Power-/Thermik-Marge, in WSL nicht sauber messbar). Modal läuft in
Rechenzentren: stabile Kühlung/Strom, große GPUs, kein 19,3-GB-WSL-Cap.

Daten werden ROH hochgeladen (11,5 MB) und IM Container injiziert
(tools.json + SYSTEM_FACTS pro Zeile) — spart 65 MB Upload.

Voraussetzung (einmalig):
    cd needle-only && uv sync --extra modal --no-dev
    uv run --extra modal modal setup        # Browser-Login

Start (vollständige Datasets, v2 + v3 parallel):
    cd needle-only
    uv run --extra modal modal run experiments/ft/modal_train.py \
        --runs v2,v3 --epochs 1 --batch-size 16 --gpu A100-40GB

Seeds/Ladder:
    ... --runs v2,v3 --seeds 42,43,44 --epochs 1 ...
    ... --runs v3 --layers 12            # Ladder-Export für den Pi

Artefakte landen im Modal-Volume "cactus-ft-artifacts"; herunterladen mit
    uv run --extra modal modal volume get cactus-ft-artifacts / ./
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import modal

FT = Path(__file__).resolve().parent
DATA = FT / "data"
GPU_DEFAULT = "A100-40GB"          # A100-40GB $2.10/h · L40S $1.95/h · L4 $0.80/h
NEEDLE = "cactus-needle[train,gpu]==3.0.4"

app = modal.App("cactus-needle3-ft")
vol = modal.Volume.from_name("cactus-ft-artifacts", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(NEEDLE)
    .add_local_file(str(DATA / "train.jsonl"), "/data/v2.jsonl")
    .add_local_file(str(DATA / "train_v3.jsonl"), "/data/v3.jsonl")
)


def _inject(src: Path, tools: list, system: str, dst: Path) -> int:
    """tools + SYSTEM_FACTS in jede Zeile (wie train_rtx.py lokal)."""
    n = 0
    with open(src, encoding="utf-8") as fh, open(dst, "w", encoding="utf-8") as out:
        for line in fh:
            if not line.strip():
                continue
            ex = json.loads(line)
            ex["tools"] = tools
            ex["system"] = system
            out.write(json.dumps(ex, ensure_ascii=False) + "\n")
            n += 1
    return n


@app.function(image=image, volumes={"/artifacts": vol}, timeout=3600)
def train(run_name: str, dataset: str, tools: list, system: str,
          epochs: int, batch: int, max_len: int, rank: int, lr: float,
          seed: int, layers: int) -> dict:
    import subprocess
    src = Path(f"/data/{dataset}.jsonl")
    work = Path(f"/tmp/{run_name}_train.jsonl")
    rows = _inject(src, tools, system, work)
    print(f"[{run_name}] injected {rows} rows", flush=True)

    adapter = f"/artifacts/{run_name}_lora.safetensors"
    cact = f"/artifacts/{run_name}.cact"
    log = f"/artifacts/{run_name}.log"
    t0 = time.time()
    with open(log, "wb") as fh:
        r = subprocess.run(
            ["needle", "finetune", str(work), "--epochs", str(epochs),
             "--batch-size", str(batch), "--lr", str(lr),
             "--lora-rank", str(rank), "--lora-alpha", "32",
             "--max-len", str(max_len), "--val-split", "0",
             "--seed", str(seed), "--out", adapter],
            stdout=fh, stderr=subprocess.STDOUT)
    train_s = time.time() - t0
    if r.returncode != 0 or not Path(adapter).exists():
        raise RuntimeError(f"{run_name}: finetune failed (rc={r.returncode})")

    t1 = time.time()
    build = ["needle", "build", "--lora", adapter, "--out", cact]
    if layers:
        build += ["--layers", str(layers)]
    with open(log, "ab") as fh:
        rb = subprocess.run(build, stdout=fh, stderr=subprocess.STDOUT)
    build_s = time.time() - t1
    if rb.returncode != 0 or not Path(cact).exists():
        raise RuntimeError(f"{run_name}: build failed (rc={rb.returncode})")

    manifest = {"run_name": run_name, "dataset": dataset, "rows": rows,
                "epochs": epochs, "batch": batch, "max_len": max_len,
                "rank": rank, "lr": lr, "seed": seed, "layers": layers or 20,
                "needle_pkg": NEEDLE, "train_s": round(train_s, 1),
                "build_s": round(build_s, 1),
                "adapter_bytes": os.path.getsize(adapter),
                "cact_bytes": os.path.getsize(cact)}
    Path(f"/artifacts/{run_name}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1))
    vol.commit()
    print(f"[{run_name}] done: train {train_s:.0f}s build {build_s:.0f}s "
          f"cact {manifest['cact_bytes']/1e6:.1f}MB", flush=True)
    return manifest


@app.local_entrypoint()
def main(runs: str = "v2,v3", epochs: int = 1, batch_size: int = 16,
         max_len: int = 1024, rank: int = 16, lr: float = 1e-4,
         seeds: str = "42", gpu: str = GPU_DEFAULT, layers: int = 0):
    tools = json.loads((FT / "tools.json").read_text(encoding="utf-8"))
    system = json.loads((FT / "manifest.json").read_text(
        encoding="utf-8"))["system_facts"]
    jobs = []
    for ds in [r.strip() for r in runs.split(",") if r.strip()]:
        for seed in [int(s) for s in seeds.split(",") if s.strip()]:
            name = f"n3-{ds}-r{rank}-e{epochs}-s{seed}" + (f"-L{layers}" if layers else "")
            jobs.append((name, ds, seed))
    print(f"starte {len(jobs)} Job(s) auf {gpu}: {[j[0] for j in jobs]}")

    f = train.with_options(gpu=gpu)
    handles = [f.spawn(name, ds, tools, system, epochs, batch_size, max_len,
                       rank, lr, seed, layers) for name, ds, seed in jobs]
    for h in handles:
        m = h.get()
        print("OK", m["run_name"], m["train_s"], "s", m["cact_bytes"] // 1000000, "MB")
