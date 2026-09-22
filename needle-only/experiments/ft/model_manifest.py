#!/usr/bin/env python3
"""Erzeugt models/manifest.json: Hashes/Config/Provenienz der FT-Artefakte.

Quellen: reports/runs/<run>.json (Trainingsparameter, Dataset-Hashes) +
die Modell-Dateien selbst. Die Binärmodelle sind gitignored (*.cact/*.pkl);
dieses Manifest ist der commitbare Nachweis. Kein Secret-Zugriff.

Usage:  uv run python experiments/ft/model_manifest.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
RUNS = FT_DIR / "reports" / "runs"
MODELS = FT_DIR / "models"
RELEASE_CANDIDATE = "sa-r16-lr1e-4-e8-seed44"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _needle_version() -> str:
    import importlib.metadata as md
    try:
        return md.version("cactus-needle")
    except md.PackageNotFoundError:
        return "unknown"


def _commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                          text=True, cwd=FT_DIR.parents[2]).stdout.strip()


def main() -> int:
    dataset = json.loads((FT_DIR / "manifest.json").read_text())
    entries = []
    for run_json in sorted(RUNS.glob("sa-*.json")):
        run = json.loads(run_json.read_text())
        name = run["run_name"]
        entry = {
            "run_name": name,
            "release_candidate": name == RELEASE_CANDIDATE,
            "training_config": run["params"],
            "dataset": {
                "split_file_sha256": dataset["file_sha256"],
                "schema_hash": dataset["schema_hash"],
                "seed": dataset["provenance"]["seed"],
                "injected_train_sha256": run["dataset"]["injected_train_sha256"],
            },
            "artifacts": {},
        }
        for kind, key in (("cact", ".cact"), ("adapter", "_lora.pkl")):
            p = MODELS / f"{name}{key}"
            entry["artifacts"][kind] = (
                {"file": p.name, "sha256": _sha256(p), "bytes": p.stat().st_size}
                if p.exists() else None)
        entries.append(entry)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": _commit(),
        "cactus_needle_version": _needle_version(),
        "base_checkpoint": "checkpoints/needle2.pkl (HF Cactus-Compute/needle2)",
        "training_hardware": "NVIDIA RTX 3090 (WSL), LoRA rank16/alpha32/lr1e-4/8ep/batch8",
        "gold_convention": dataset["gold_convention"],
        "system_facts": dataset["system_facts"],
        "release_candidate": RELEASE_CANDIDATE,
        "runs": entries,
    }
    out = MODELS / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    for e in entries:
        c = e["artifacts"]["cact"]
        print(f"  {e['run_name']:<34} cact {c['bytes']//1000000}MB "
              f"{c['sha256'][:16]}  RC={e['release_candidate']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
