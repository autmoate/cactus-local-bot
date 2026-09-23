#!/usr/bin/env python3
"""HF-Release der Calendar-FTs (Needle 2 und Needle 3) + Verifikation.

Sicherheit: HF_TOKEN wird ausschließlich via python-dotenv aus der Root-.env
geladen und NIE ausgegeben, geloggt oder committet. Hochgeladen werden nur
Modell (.cact), Model Card (als README.md) und Manifest — KEIN Dataset, KEINE
Telegram-Traces.

Releases:
  --release n2   autmoate/cactus-needle2-calendar   (seed44, atomic-Referenz)
  --release n3   autmoate/cactus-needle3-calendar   (v4-e5, Multi-Call-Kandidat)

Usage (jeweils im passenden venv — n2: needle 2, n3: cactus-needle 3.0.4):
  .venv-ft3/bin/python experiments/ft/upload_hf.py --release n3 --dry-run
  .venv-ft3/bin/python experiments/ft/upload_hf.py --release n3
  .venv-ft3/bin/python experiments/ft/upload_hf.py --release n3 --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
ROOT = FT_DIR.parents[2]
sys.path.insert(0, str(FT_DIR))
sys.path.insert(0, str(FT_DIR.parents[1] / "src"))

RELEASES = {
    "n2": {
        "repo": "autmoate/cactus-needle2-calendar",
        "weights": FT_DIR / "models" / "sa-r16-lr1e-4-e8-seed44.cact",
        "upload_name": "calendar-needle2-seed44.cact",
        "card": FT_DIR / "MODEL_CARD.md",
        "manifest": FT_DIR / "models" / "manifest.json",
        "venv_hint": "needle 2 (Projekt-venv oder .venv-ft)",
    },
    "n3": {
        "repo": "autmoate/cactus-needle3-calendar",
        "weights": FT_DIR / "models" / "modal" / "n3-v4-r32-e5-s42.cact",
        "upload_name": "calendar-needle3-v4-e5.cact",
        "card": FT_DIR / "MODEL_CARD_N3.md",
        "manifest": FT_DIR / "models" / "manifest_n3.json",
        "venv_hint": "cactus-needle 3.0.4 (.venv-ft3)",
    },
}

SMOKE = [
    ("Trag morgen 10 Uhr Zahnarzt ein.", "calendar_create"),
    ("Verschieb Teammeeting auf Freitag 15 Uhr.", "calendar_move"),
    ("Lösch den Termin Kegelabend.", "calendar_delete"),
    ("Was steht diese Woche an?", "calendar_list"),
    ("Wann haben Lisa und Max nächste Woche 90 Minuten frei?", "calendar_find_slot"),
    ("Termin beim Augenarzt am 18. September um 11 Uhr.", "calendar_create"),
    ("Zeig mir die Termine von Jana.", "calendar_list"),
    ("Buchhaltung ist jetzt erst halb zwölf.", "calendar_move"),
    ("Trag morgen 10 Uhr Zahnarzt ein und Freitag 15 Uhr Sport.", "calendar_create"),
    ("Wie wird das Wetter morgen?", "none"),
]


def _load_env() -> None:
    from dotenv import load_dotenv
    for candidate in (ROOT / ".env", ROOT / "needle-only" / ".env"):
        if candidate.exists():
            load_dotenv(candidate)


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


def ensure_manifest(release: str) -> Path:
    """n2 hat ein committetes Manifest; n3 wird hier aus den Run-/Dataset-Daten gebaut."""
    cfg = RELEASES[release]
    if release == "n2":
        return cfg["manifest"]
    run = json.loads((FT_DIR / "reports" / "runs" / "n3-v4-r32-e5-s42.json").read_text())
    ds = json.loads((FT_DIR / "data" / "v4_manifest.json").read_text())
    cact = cfg["weights"]
    man = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release": "n3-v4-e5",
        "base_model": "Cactus-Compute/needle3",
        "needle_pkg": run["needle_pkg"],
        "needle_version_at_manifest": _needle_version(),
        "training": {k: run[k] for k in ("dataset", "rows", "epochs", "rank", "lr",
                                          "seed", "batch", "max_len", "layers",
                                          "train_s", "build_s")},
        "dataset": {"source": "data/train_v4.jsonl (Preservation-Mix)",
                    "counts": ds["counts"], "mix_train": ds["mix_train"],
                    "file_sha256": ds["file_sha256"], "seed": ds["seed"]},
        "artifact": {"file": cfg["upload_name"], "local_path": str(cact.relative_to(ROOT)),
                     "bytes": cact.stat().st_size, "sha256": _sha256(cact)},
        "eval_summary": {"A1_atomic_tool_args_exact": [1.0, 0.889, 0.886],
                         "A1_challenge_args_exact": [0.72, 0.60],
                         "A2_multi_all_actions": 0.90,
                         "neg_refusal": 1.0, "false_refusal": 0.0,
                         "C_final_db": 0.80, "median_ms": 188},
    }
    cfg["manifest"].write_text(json.dumps(man, ensure_ascii=False, indent=1))
    return cfg["manifest"]


def upload(token: str, release: str, dry: bool) -> None:
    from huggingface_hub import HfApi
    cfg = RELEASES[release]
    cact = cfg["weights"]
    if not cact.exists():
        raise SystemExit(f"Modell fehlt: {cact}")
    manifest = ensure_manifest(release)
    sha = _sha256(cact)
    print(f"Release {release}: {cfg['repo']}")
    print(f"  {cfg['upload_name']}  {cact.stat().st_size/1e6:.2f} MB  sha256 {sha[:16]}…")
    print(f"  Card: {cfg['card'].name} · Manifest: {manifest.name}")
    if dry:
        print(f"  [dry-run] würde hochladen: {cfg['upload_name']}, README.md, manifest.json")
        return
    if not token:
        raise SystemExit("HF_TOKEN fehlt (.env) — Upload abgebrochen.")
    api = HfApi(token=token)  # Token bleibt im Objekt, nie geloggt
    api.create_repo(repo_id=cfg["repo"], repo_type="model", exist_ok=True)
    api.upload_file(path_or_fileobj=str(cact), path_in_repo=cfg["upload_name"],
                    repo_id=cfg["repo"], commit_message=f"Add {release} release ({cfg['upload_name']})")
    api.upload_file(path_or_fileobj=str(cfg["card"]), path_in_repo="README.md",
                    repo_id=cfg["repo"], commit_message="Add model card")
    api.upload_file(path_or_fileobj=str(manifest), path_in_repo="manifest.json",
                    repo_id=cfg["repo"], commit_message="Add manifest (hashes/provenance)")
    print(f"  hochgeladen: https://huggingface.co/{cfg['repo']}")


def verify(release: str) -> int:
    from huggingface_hub import hf_hub_download
    import needle
    import dataset_spec as spec

    cfg = RELEASES[release]
    expected = _sha256(cfg["weights"])
    token = os.environ.get("HF_TOKEN") or None
    tmp = Path(tempfile.mkdtemp())
    print(f"[{release}] clean-download → {tmp} (benötigt {cfg['venv_hint']})")
    got = Path(hf_hub_download(repo_id=cfg["repo"], filename=cfg["upload_name"],
                               local_dir=str(tmp), token=token))
    got_sha = _sha256(got)
    ok_sha = got_sha == expected
    print(f"  sha256 {'MATCH' if ok_sha else 'MISMATCH'} ({got_sha[:16]}…)")
    tools = json.loads((FT_DIR / "tools.json").read_text())
    agent = needle.Needle(tools=tools, system=spec.SYSTEM_FACTS, weights=str(got))
    hits = 0
    for query, want in SMOKE:
        agent.reset()
        calls = agent.complete(query).get("function_calls") or []
        got_name = calls[0]["name"] if calls else "none"
        good = got_name == want
        hits += good
        print(f"  {'OK ' if good else 'FAIL'} {query[:48]!r:50} -> {got_name}")
    print(f"Smoke: {hits}/{len(SMOKE)} Tool-Treffer")
    return 0 if (ok_sha and hits >= 8) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", choices=list(RELEASES), default="n3")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="clean-download + SHA + 10 Smoke-Cases (kein Upload)")
    args = ap.parse_args()
    _load_env()
    if args.verify:
        return verify(args.release)
    upload(os.environ.get("HF_TOKEN", ""), args.release, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
