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
    .add_local_file(str(DATA / "train_v4.jsonl"), "/data/v4.jsonl")
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


MAX_TRAIN_SECONDS = 7 * 3600 + 1800      # 7h30 Watchdog pro Job (Build/Upload folgen)


@app.function(image=image, volumes={"/artifacts": vol}, timeout=8 * 3600)
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
            stdout=fh, stderr=subprocess.STDOUT,
            timeout=MAX_TRAIN_SECONDS)
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
         seeds: str = "42", gpu: str = GPU_DEFAULT, layers: int = 0,
         budget_hours: float = 8.0, plan: str = ""):
    tools = json.loads((FT / "tools.json").read_text(encoding="utf-8"))
    system = json.loads((FT / "manifest.json").read_text(
        encoding="utf-8"))["system_facts"]
    seed_list = [int(s) for s in seeds.split(",") if s.strip()]
    jobs = []   # (name, dataset, seed, rank, epochs)
    if plan:    # z.B. "v2:r16:e3,v4:r32:e3" — ein Aufruf, ein Gesamtbudget
        for item in [x.strip() for x in plan.split(",") if x.strip()]:
            ds, r, e = item.split(":")
            for seed in seed_list:
                jobs.append((f"n3-{ds}-{r}-{e}-s{seed}", ds, seed,
                             int(r.lstrip("r")), int(e.lstrip("e"))))
    else:
        for ds in [r.strip() for r in runs.split(",") if r.strip()]:
            for seed in seed_list:
                name = f"n3-{ds}-r{rank}-e{epochs}-s{seed}" + (f"-L{layers}" if layers else "")
                jobs.append((name, ds, seed, rank, epochs))
    print(f"starte {len(jobs)} Job(s) auf {gpu}: "
          f"{[(j[0], f'rank{j[3]}', f'e{j[4]}b{batch_size}') for j in jobs]}")

    # Hartes Gesamtbudget: Abbruch, wenn die GPU-Walltime das Limit überschreitet
    deadline = time.time() + budget_hours * 3600
    print(f"Budget: {budget_hours} h (Deadline {time.strftime('%H:%M:%S', time.localtime(deadline))})")
    f = train.with_options(gpu=gpu)
    handles = [f.spawn(name, ds, tools, system, ep, batch_size, max_len,
                       rk, lr, seed, layers) for name, ds, seed, rk, ep in jobs]
    ok = True
    for h in handles:
        remaining = max(1, int(deadline - time.time()))
        try:
            m = h.get(timeout=remaining)
        except TimeoutError:
            print(f"!! Budget erschöpft — breche {h.object_id} ab")
            h.cancel()
            ok = False
            continue
        print("OK", m["run_name"], m["train_s"], "s", m["cact_bytes"] // 1000000, "MB")
    print(f"Walltime: {(time.time() - (deadline - budget_hours*3600))/60:.1f} min")
    if not ok:
        raise SystemExit("Budget-Limit erreicht — restliche Jobs abgebrochen")
