#!/usr/bin/env python3
"""RTX-3090-Trainingswrapper für das FT-Dataset v2 (plan §20, keine Jetson-Hacks).

Zwei Aufgaben:
  1. Format-Injektion — die committeten JSONLs tragen kein `tools`/`system`
     (needle rendert sonst `<tools></tools>` = 111 statt ~850 Tokens, das Modell
     sähe den Katalog nie). Wir schreiben ein Trainings-Artefakt mit dem
     Produktionskatalog (tools.json) + SYSTEM_FACTS. Dataset + manifest.json
     bleiben unangetastet und hashbar.
  2. Training + Export — `needle finetune` (Console-Script, NICHT
     `python -m needle.cli` — das ist ein stiller No-op) und `needle build`
     → `.cact`. Pro Run: Log + Run-Manifest mit Params, Dataset-Hashes, Git-SHA.

Usage:
  uv run python experiments/ft/train_rtx.py \
      --run-name sa-r16-lr1e-4-e8-seed42 \
      --rank 16 --lr 1e-4 --epochs 8 --seed 42 --batch-size 8
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
NEEDLE_ONLY = FT_DIR.parents[1]
ROOT = NEEDLE_ONLY.parent
sys.path.insert(0, str(NEEDLE_ONLY / "src"))
sys.path.insert(0, str(FT_DIR))

import dataset_spec as spec  # noqa: E402

RUNS_DIR = FT_DIR / "reports" / "runs"
MODELS_DIR = FT_DIR / "models"
DATA_FT = FT_DIR / "data" / "_ft"


def _rel(path) -> str:
    """Pfad relativ zum Repo-Root — Logs/Manifeste sollen keine Home-Pfade tragen.
    Der Subprozess läuft mit cwd=ROOT, relative Pfade funktionieren also."""
    try:
        return os.path.relpath(str(path), str(ROOT))
    except ValueError:  # anderes Laufwerk (z.B. Modal) -> unverändert
        return str(path)


def _needle_major() -> int:
    """Installierte cactus-needle-Hauptversion (2 vs 3) — CLI unterscheidet sich."""
    import importlib.metadata as md
    try:
        return int(md.version("cactus-needle").split(".")[0])
    except Exception:  # noqa: BLE001
        return 2


def _needle_bin() -> str:
    """Console-Script neben dem aktiven Interpreter (sys.prefix, nicht
    sys.executable — das ist unter uv ein Symlink in die Base-Install)."""
    return str(Path(sys.prefix) / "bin" / "needle")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inject_training_format(run_name: str, split: str,
                           src: Path | None = None) -> Path:
    """tools + system in jede Row schreiben (Produktionskatalog, frozen)."""
    tools = json.loads((FT_DIR / "tools.json").read_text(encoding="utf-8"))
    src = src or FT_DIR / "data" / f"{split}.jsonl"
    DATA_FT.mkdir(parents=True, exist_ok=True)
    dst = DATA_FT / f"{run_name}_{split}.jsonl"
    n = 0
    with open(src, encoding="utf-8") as fh, open(dst, "w", encoding="utf-8") as out:
        for line in fh:
            if not line.strip():
                continue
            ex = json.loads(line)
            ex["tools"] = tools
            ex["system"] = spec.SYSTEM_FACTS
            out.write(json.dumps(ex, ensure_ascii=False) + "\n")
            n += 1
    if src == FT_DIR / "data" / f"{split}.jsonl":
        want = json.loads((FT_DIR / "manifest.json").read_text())["counts"].get(split)
        if n != want:
            raise SystemExit(f"injection {split}: {n} rows (!= manifest count)")
    print(f"  injected  {split}: {n} rows -> {dst.name}")
    return dst


def run(cmd: list[str], log_path: Path, env: dict,
        stall_timeout_s: int = 900) -> int:
    """Subprozess mit Stall-Watchdog.

    WSL/JAX kann nach einem CUDA-Fehler im Teardown hängen (Prozess lebt,
    GPU idle, Log wächst nicht). Ohne Watchdog blockiert die Retry-Kette
    für immer; hier wird nach `stall_timeout_s` ohne Log-Fortschritt die
    Prozessgruppe gekillt."""
    print(f"  $ {' '.join(cmd)}", flush=True)
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
        last_growth = time.time()
        last_size = log_path.stat().st_size
        while proc.poll() is None:
            time.sleep(15)
            size = log_path.stat().st_size
            if size != last_size:
                last_size, last_growth = size, time.time()
            elif time.time() - last_growth > stall_timeout_s:
                print(f"  ! Stall ({stall_timeout_s}s ohne Log-Fortschritt) — "
                      f"kill process group", flush=True)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                return -9
        return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="FT-Training auf der RTX 3090")
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=float, default=32.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--batch-size", type=int, default=8,
                    help="WSL-VRAM-Cap ~12GiB/GPU: 8@1024 ok, 16@1024 OOM")
    ap.add_argument("--max-len", type=int, default=1024)
    ap.add_argument("--val-split", type=float, default=0.1)
    ap.add_argument("--qat-bits", default="auto")
    ap.add_argument("--data", default=str(FT_DIR / "data" / "train.jsonl"),
                    help="Trainings-JSONL (Default: FT-Dataset v2)")
    ap.add_argument("--layers", type=int, default=0,
                    help="needle3: N-Layer-Rung exportieren (2..20)")
    ap.add_argument("--checkpoint", default=None,
                    help="Base-Checkpoint; Default: needle lädt die passende "
                         "Base automatisch (needle2 .pkl bzw. needle3)")
    args = ap.parse_args()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RUNS_DIR / f"{args.run_name}.log"
    report_path = RUNS_DIR / f"{args.run_name}.json"
    major = _needle_major()
    adapter_ext = ".safetensors" if major >= 3 else ".pkl"
    adapter = MODELS_DIR / f"{args.run_name}_lora{adapter_ext}"
    cact = MODELS_DIR / f"{args.run_name}.cact"

    if args.checkpoint and not Path(args.checkpoint).exists():
        raise SystemExit(f"Basischeckpoint fehlt: {args.checkpoint}")

    print(f"=== FT-Run {args.run_name} ===")
    t0 = time.time()
    train_jsonl = inject_training_format(args.run_name, "train",
                                         src=Path(args.data))

    env = os.environ.copy()
    env.pop("LD_LIBRARY_PATH", None)          # WSL/CUDA-Lib-Konflikt vermeiden
    env["NEEDLE_TELEMETRY"] = "0"
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("XLA_PYTHON_CLIENT_PREALLOCATE", "false")
    # Command-Buffers/CUDA-Graphs aus: auf WSL crasht der Stream-Capture-Pfad
    # sporadisch ("Failed to check stream capturing status") — Mathe unverändert.
    env.setdefault("XLA_FLAGS", "--xla_gpu_autotune_level=0 "
                                "--xla_gpu_enable_command_buffer=")

    finetune = [_needle_bin(), "finetune", _rel(train_jsonl),
                "--epochs", str(args.epochs),
                "--batch-size", str(args.batch_size),
                "--lr", str(args.lr),
                "--lora-rank", str(args.rank),
                "--lora-alpha", str(args.alpha),
                "--max-len", str(args.max_len),
                "--val-split", str(args.val_split),
                "--seed", str(args.seed),
                "--checkpoint-dir", _rel(ROOT / "checkpoints"),
                "--out", _rel(adapter)]
    if major < 3:  # needle3 kennt --qat-bits nicht (QAT ist dort implizit)
        finetune += ["--qat-bits", args.qat_bits]
    if args.checkpoint:
        finetune += ["--checkpoint", _rel(args.checkpoint)]
    log_path.write_bytes(b"")  # frischer Log pro Run
    code = run(finetune, log_path, env)
    train_time = time.time() - t0
    if code != 0 or not adapter.exists() or adapter.stat().st_size == 0:
        raise SystemExit(f"Finetune fehlgeschlagen (code={code}, adapter={adapter})")

    t1 = time.time()
    build = [_needle_bin(), "build"]
    if args.checkpoint:
        build.append(_rel(args.checkpoint))
    build += ["--lora", _rel(adapter), "--out", _rel(cact)]
    if args.layers:
        build += ["--layers", str(args.layers)]
    code = run(build, log_path, env)
    build_time = time.time() - t1
    if code != 0 or not cact.exists() or cact.stat().st_size == 0:
        raise SystemExit(f"Build fehlgeschlagen (code={code}, cact={cact})")

    manifest = json.loads((FT_DIR / "manifest.json").read_text())
    report = {
        "run_name": args.run_name,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "params": {"rank": args.rank, "alpha": args.alpha, "lr": args.lr,
                   "epochs": args.epochs, "seed": args.seed,
                   "batch_size": args.batch_size, "max_len": args.max_len,
                   "val_split": args.val_split, "qat_bits": args.qat_bits,
                   "layers": args.layers or 20,
                   "needle_major": major,
                   "checkpoint": args.checkpoint or "auto (needle base)"},
        "source_data": _rel(args.data),
        "dataset": {"manifest_file_sha256": manifest["file_sha256"],
                    "schema_hash": manifest["schema_hash"],
                    "seed": manifest["provenance"]["seed"],
                    "injected_train_sha256": _sha256(train_jsonl)},
        "adapter": {"path": _rel(adapter), "bytes": adapter.stat().st_size},
        "cact": {"path": _rel(cact), "bytes": cact.stat().st_size},
        "times_s": {"train": round(train_time, 1), "build": round(build_time, 1)},
        "log": _rel(log_path),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps({k: report[k] for k in ("run_name", "times_s", "cact")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
