#!/usr/bin/env python3
"""Modal FT runner for the Thunderbird calendar task — Needle 2 Base + LoRA.

Mirrors experiments/ft/modal_train.py but for the v2 contract: one tool
(`extract_event`, when-first), negatives = empty call `[]`. Runs remotely so no
local GPU/thermal limits. Data is uploaded raw; tools + system facts are
injected inside the container.

Setup (once):
    cd needle-only && uv sync --extra modal --no-dev
    uv run --extra modal modal setup          # browser login / token

Smoke (function check only, 200 rows, 1 epoch):
    uv run --extra modal modal run \
      experiments/business_cases/thunderbird_calendar/ft/modal_train_tb.py \
      --runs smoke --epochs 1 --batch-size 8

Real runs (max 3):
    ... --plan train:r16:e5,train:r8:e8,train:r16:e8 --seeds 42
    ... --runs train --plan train:r16:e5  # etc.

Artifacts: volume `cactus-ft-artifacts-tb`; fetch with
    uv run --extra modal modal volume get cactus-ft-artifacts-tb ./ft-models-tb
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import modal

FT = Path(__file__).resolve().parent
DATA = FT / "data"
GPU_DEFAULT = "A100-40GB"
NEEDLE = "cactus-needle[train,gpu]==2.0.13"

app = modal.App("cactus-needle2-tb-ft")
vol = modal.Volume.from_name("cactus-ft-artifacts-tb", create_if_missing=True)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(NEEDLE)
    .add_local_file(str(DATA / "train.jsonl"), "/data/train.jsonl")
    .add_local_file(str(DATA / "smoke.jsonl"), "/data/smoke.jsonl")
)

MAX_TRAIN_SECONDS = 3 * 3600


def _inject(src: Path, tools: list, system: str, dst: Path) -> int:
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


@app.function(image=image, volumes={"/artifacts": vol}, timeout=4 * 3600)
def train(run_name: str, dataset: str, tools: list, system: str, epochs: int,
          batch: int, max_len: int, rank: int, lr: float, seed: int,
          qat_bits: str) -> dict:
    import subprocess
    rows = _inject(Path(f"/data/{dataset}.jsonl"), tools, system,
                   Path(f"/tmp/{run_name}.jsonl"))
    print(f"[{run_name}] injected {rows} rows", flush=True)
    adapter = f"/artifacts/{run_name}_lora.pkl"
    cact = f"/artifacts/{run_name}.cact"
    log = f"/artifacts/{run_name}.log"
    t0 = time.time()
    with open(log, "wb") as fh:
        r = subprocess.run(
            ["needle", "finetune", f"/tmp/{run_name}.jsonl",
             "--epochs", str(epochs), "--batch-size", str(batch),
             "--lr", str(lr), "--lora-rank", str(rank), "--lora-alpha", "32",
             "--max-len", str(max_len), "--val-split", "0", "--seed", str(seed),
             "--qat-bits", qat_bits, "--checkpoint-dir", "/artifacts/checkpoints",
             "--out", adapter],
            stdout=fh, stderr=subprocess.STDOUT, timeout=MAX_TRAIN_SECONDS)
    train_s = time.time() - t0
    if r.returncode != 0 or not Path(adapter).exists():
        raise RuntimeError(f"{run_name}: finetune failed (rc={r.returncode})")
    with open(log, "ab") as fh:
        rb = subprocess.run(["needle", "build", "--lora", adapter, "--out", cact],
                            stdout=fh, stderr=subprocess.STDOUT)
    if rb.returncode != 0 or not Path(cact).exists():
        raise RuntimeError(f"{run_name}: build failed (rc={rb.returncode})")
    manifest = {"run_name": run_name, "dataset": dataset, "rows": rows,
                "epochs": epochs, "batch": batch, "max_len": max_len,
                "rank": rank, "lr": lr, "seed": seed, "qat_bits": qat_bits,
                "needle_pkg": NEEDLE, "train_s": round(train_s, 1),
                "cact_bytes": os.path.getsize(cact)}
    Path(f"/artifacts/{run_name}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1))
    vol.commit()
    print(f"[{run_name}] done: train {train_s:.0f}s, cact "
          f"{manifest['cact_bytes']/1e6:.1f}MB", flush=True)
    return manifest


MAX_FULL_RUNS = 3
SMOKE_TIMEOUT_S = 3600
BUDGET_FILE = FT / "reports" / "modal_budget.json"


def _record(entry: dict) -> None:
    """Append one job result to the local budget ledger (no secrets)."""
    BUDGET_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(BUDGET_FILE.read_text()) if BUDGET_FILE.exists() else []
    data.append(entry)
    BUDGET_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                           encoding="utf-8")


@app.local_entrypoint()
def main(runs: str = "smoke", plan: str = "", epochs: int = 1,
         batch_size: int = 8, max_len: int = 1024, rank: int = 16,
         lr: float = 1e-4, seeds: str = "42", gpu: str = GPU_DEFAULT,
         qat_bits: str = "auto", budget_hours: float = 6.0,
         reserve_minutes: float = 15.0, per_run_hours: float = 2.0):
    """STRICTLY SERIAL. Hard AGGREGATE GPU budget across all jobs.

    budget_hours is a total GPU-time cap (not wall-clock with parallelism).
    Jobs run one at a time; a new job starts only while total elapsed GPU time
    is below (budget - reserve). Never starts a 4th full run.
    """
    tools = json.loads((FT / "tools_tb.json").read_text(encoding="utf-8"))
    system = json.loads((FT / "manifest.json").read_text(
        encoding="utf-8"))["system_facts"]
    seed_list = [int(s) for s in seeds.split(",") if s.strip()]
    jobs = []
    if plan:  # e.g. "train:r16:e5,train:r8:e8" — one call, one total budget
        for item in [x.strip() for x in plan.split(",") if x.strip()]:
            ds, r, e = item.split(":")
            for seed in seed_list:
                jobs.append((f"n2-{ds}-{r}-{e}-s{seed}", ds, seed,
                             int(r.lstrip("r")), int(e.lstrip("e"))))
    else:
        for ds in [r.strip() for r in runs.split(",") if r.strip()]:
            for seed in seed_list:
                jobs.append((f"n2-{ds}-r{rank}-e{epochs}-s{seed}", ds, seed,
                             rank, epochs))

    is_smoke = bool(jobs) and all(j[1] == "smoke" for j in jobs)
    if not is_smoke and len(jobs) > MAX_FULL_RUNS:
        raise SystemExit(f"refuse: {len(jobs)} full jobs > {MAX_FULL_RUNS} "
                         "(hard cap; no 4th run)")

    budget_s = budget_hours * 3600
    cutoff_s = budget_s - reserve_minutes * 60
    per_run_s = SMOKE_TIMEOUT_S if is_smoke else per_run_hours * 3600
    print(f"serial run, {len(jobs)} job(s) on {gpu}: {[j[0] for j in jobs]}")
    print(f"aggregate budget {budget_hours:.2f}h, cutoff {cutoff_s/3600:.2f}h, "
          f"per-run cap {per_run_s/3600:.2f}h (smoke={is_smoke})")

    fn = train.with_options(gpu=gpu)
    total = 0.0
    for name, ds, seed, rk, ep in jobs:
        remaining = cutoff_s - total
        if remaining <= 0:
            print(f"STOP: total {total/3600:.2f}h >= cutoff {cutoff_s/3600:.2f}h")
            break
        timeout = min(per_run_s, remaining)
        print(f"START {name} (timeout {timeout/60:.0f}min)", flush=True)
        t0 = time.time()
        handle = fn.spawn(name, ds, tools, system, ep, batch_size, max_len, rk,
                          lr, seed, qat_bits)
        try:
            m = handle.get(timeout=timeout)
        except TimeoutError:
            handle.cancel()
            elapsed = time.time() - t0
            total += elapsed
            _record({"run": name, "dataset": ds, "rank": rk, "epochs": ep,
                     "seed": seed, "status": "timeout",
                     "elapsed_s": round(elapsed, 1),
                     "cumulative_s": round(total, 1)})
            print(f"!! TIMEOUT {name} after {elapsed/60:.1f}min — cancelled")
            break
        elapsed = time.time() - t0
        total += elapsed
        _record({"run": name, "dataset": ds, "rank": rk, "epochs": ep,
                 "seed": seed, "status": "ok",
                 "train_s": m.get("train_s"), "cact_bytes": m.get("cact_bytes"),
                 "elapsed_s": round(elapsed, 1),
                 "cumulative_s": round(total, 1)})
        print(f"OK {name}: {elapsed/60:.1f}min, cumulative {total/3600:.2f}h",
              flush=True)
    print(f"TOTAL aggregate GPU time: {total/3600:.2f}h "
          f"(<= {budget_hours:.2f}h budget)")
    if total > budget_s:
        raise SystemExit("aggregate budget exceeded")
