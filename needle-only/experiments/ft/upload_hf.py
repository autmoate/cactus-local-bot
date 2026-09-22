#!/usr/bin/env python3
"""HF-Release des Needle-2-Calendar-FT (Release-Kandidat) + Verifikation.

Sicherheit: HF_TOKEN wird ausschließlich via python-dotenv aus der Root-.env
geladen und NIE ausgegeben, geloggt oder committet. Hochgeladen werden nur
Modell (.cact), Model Card (als README.md) und Manifest — KEIN Dataset, KEINE
Telegram-Traces.

Usage:
  uv run python experiments/ft/upload_hf.py            # Upload
  uv run python experiments/ft/upload_hf.py --verify   # clean-download + SHA + Smoke
  uv run python experiments/ft/upload_hf.py --dry-run  # nur zeigen, was passiert
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
ROOT = FT_DIR.parents[2]
sys.path.insert(0, str(FT_DIR))
sys.path.insert(0, str(FT_DIR.parents[1] / "src"))

REPO_DEFAULT = "autmoate/cactus-needle2-calendar"
UPLOAD_NAME = "calendar-needle2-seed44.cact"


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


def _release() -> tuple[str, Path, str, int]:
    man = json.loads((FT_DIR / "models" / "manifest.json").read_text())
    rc = [r for r in man["runs"] if r["release_candidate"]][0]
    cact = FT_DIR / "models" / rc["artifacts"]["cact"]["file"]
    return rc["run_name"], cact, rc["artifacts"]["cact"]["sha256"], rc["artifacts"]["cact"]["bytes"]


def upload(token: str, repo: str, dry: bool) -> None:
    from huggingface_hub import HfApi
    name, cact, sha, size = _release()
    card = FT_DIR / "MODEL_CARD.md"
    manifest = FT_DIR / "models" / "manifest.json"
    print(f"Release-Kandidat: {name}")
    print(f"  {cact.name}  {size/1e6:.2f} MB  sha256 {sha[:16]}…")
    if dry:
        print(f"  [dry-run] würde nach {repo} hochladen: "
              f"{UPLOAD_NAME}, README.md, manifest.json")
        return
    if not token:
        raise SystemExit("HF_TOKEN fehlt (.env) — Upload abgebrochen.")

    api = HfApi(token=token)  # Token bleibt im Objekt, nie geloggt
    api.create_repo(repo_id=repo, repo_type="model", exist_ok=True)
    api.upload_file(path_or_fileobj=str(cact), path_in_repo=UPLOAD_NAME,
                    repo_id=repo, commit_message=f"Add {name} (.cact, W4A8)")
    api.upload_file(path_or_fileobj=str(card), path_in_repo="README.md",
                    repo_id=repo, commit_message="Add model card")
    api.upload_file(path_or_fileobj=str(manifest), path_in_repo="manifest.json",
                    repo_id=repo, commit_message="Add model manifest (hashes)")
    print(f"  hochgeladen: https://huggingface.co/{repo}")


SMOKE = [
    ("Trag morgen 10 Uhr Zahnarzt ein.", "calendar_create"),
    ("Verschieb Teammeeting auf Freitag 15 Uhr.", "calendar_move"),
    ("Lösch den Termin Kegelabend.", "calendar_delete"),
    ("Was steht diese Woche an?", "calendar_list"),
    ("Wann haben Lisa und Max nächste Woche 90 Minuten frei?", "calendar_find_slot"),
    ("Termin beim Augenarzt am 18. September um 11 Uhr.", "calendar_create"),
    ("Erinnerung stornieren: Buchhaltung.", "calendar_delete"),
    ("Zeig mir die Termine von Jana.", "calendar_list"),
    ("Buchhaltung ist jetzt erst halb zwölf.", "calendar_move"),
    ("Wie wird das Wetter morgen?", "none"),
]


def verify(repo: str) -> int:
    from huggingface_hub import hf_hub_download
    import needle
    import dataset_spec as spec

    name, cact, sha, _ = _release()
    token = os.environ.get("HF_TOKEN") or None
    tmp = Path(tempfile.mkdtemp())
    print(f"Clean-Download nach {tmp} …")
    got = Path(hf_hub_download(repo_id=repo, filename=UPLOAD_NAME,
                               local_dir=str(tmp), token=token))
    got_sha = _sha256(got)
    ok_sha = got_sha == sha
    print(f"  sha256 {'MATCH' if ok_sha else 'MISMATCH'} ({got_sha[:16]}…)")

    tools = json.loads((FT_DIR / "tools.json").read_text())
    agent = needle.Needle(tools=tools, system=spec.SYSTEM_FACTS, weights=str(got))
    hits = 0
    for query, want in SMOKE:
        agent.reset()
        resp = agent.complete(query)
        calls = resp.get("function_calls") or []
        got_name = calls[0]["name"] if calls else "none"
        good = got_name == want
        hits += good
        print(f"  {'OK ' if good else 'FAIL'} {query[:46]!r:48} -> {got_name}")
    print(f"Smoke: {hits}/{len(SMOKE)} tool-treffer")
    return 0 if (ok_sha and hits >= 8) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=REPO_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="clean-download + SHA + 10 Smoke-Cases (kein Upload)")
    args = ap.parse_args()
    _load_env()
    if args.verify:
        return verify(args.repo)
    token = os.environ.get("HF_TOKEN", "")
    upload(token, args.repo, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
