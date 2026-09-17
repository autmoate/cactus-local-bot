#!/usr/bin/env python3
"""Deterministic FT-dataset builder (plan: Needle-FT-Dataset v1).

- Imports the five production tool schemas via build_tools() and needle's
  own build_schema (attached as fn._needle_tool) — no duplicated schemas.
- Generates train/validation/test JSONL by template families; splits are
  family-exclusive, validation/test draw from held-out value pools.
- Writes manifest.json (commit sha, seed, schema hash, file hashes, config).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
SRC = FT_DIR.parents[1] / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(FT_DIR.parent))

import dataset_spec as spec  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import build_tools  # noqa: E402


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def production_schemas() -> tuple[list[dict], str]:
    """Schemas straight from production build_tools (never re-declared)."""
    store = cal.CalendarStore(Path(tempfile.mkdtemp()) / "schema_probe.db")
    tools = build_tools(store)
    schemas = [fn._needle_tool for fn in tools.values()]
    digest = hashlib.sha256(
        json.dumps(schemas, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return schemas, digest


def _schema_props(schemas: list[dict], tool: str) -> dict:
    for s in schemas:
        if s["name"] == tool:
            return s["parameters"]["properties"]
    raise KeyError(tool)


def _meta(family: dict, filled: dict, lang: str, inp: str) -> dict:
    persons = str(filled.get("participants") or filled.get("persons") or "")
    p_count = len(cal.parse_persons(persons)) if persons else (
        0 if family["tool"] == "calendar_find_slot" else 1)
    temporal = "none"
    for k in ("date", "until"):
        v = str(filled.get(k, "") or "")
        if v:
            temporal = spec.temporal_class_of(v) or temporal
            break
    has_until = bool(filled.get("until"))
    range_type = "day_range" if has_until else "none"
    explicitness = "polite" if "polite" in family["id"] else (
        "question" if family["id"].endswith("question") or "?" in inp
        else "direct")
    return {"tool": family["tool"], "temporal_class": temporal,
            "range_type": range_type, "participant_count": p_count,
            "explicitness": explicitness, "language": lang,
            "source_family": family["id"], "split": family["split"]}


def build_split(schemas: list[dict], split: str, n: int, rng, seen: set):
    fams = [f for f in spec.FAMILIES if f["split"] == split]
    pos_fams = [f for f in fams if f["tool"] != "none"]
    n_neg = round(n * spec.NEGATIVE_SHARE)
    n_pos = n - n_neg
    by_tool = {}
    fams_by_tool = {}
    for f in pos_fams:
        fams_by_tool.setdefault(f["tool"], []).append(f)
    quota = {}
    for tool, share in spec.TARGET_RATES.items():
        pool = fams_by_tool.get(tool, [])
        tw = sum(f["weight"] for f in pool)
        for f in pool:
            quota[f["id"]] = int(round(n_pos * share * f["weight"] / tw))
    rows: list[dict] = []
    idc = 0
    props = {s["name"]: s["parameters"]["properties"] for s in schemas}
    def emit(family, lang):
        nonlocal idc
        for _ in range(30):
            inp, filled = family["fn"](rng, split, lang)
            key = inp.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            idc += 1
            rows.append({
                "id": f"{split[:3]}-{idc:05d}",
                "query": inp,
                "answers": ([] if family["tool"] == "none"
                            else [{"name": family["tool"],
                                   "arguments": spec._args_sparse(
                                       filled, inp)}]),
                "reasoning": spec.gold_reasoning(inp, filled) if filled
                else "off-topic: no calendar action applies",
                "meta": _meta(family, filled, lang, inp),
            })
            return True
        return False
    for family in pos_fams:
        for _ in range(quota[family["id"]]):
            emit(family, spec.draw_lang(rng))
    attempts = 0
    pool = list(pos_fams)
    while (len(rows) - sum(1 for r in rows if r["meta"]["tool"] == "none")
           < n_pos and attempts < 100 * n_pos and pool):
        family = pool[(attempts + rng.randrange(len(pool))) % len(pool)]
        if not emit(family, spec.draw_lang(rng)):
            pool = [f for f in pool if f["id"] != family["id"]]
        attempts += 1
    emitted_neg = sum(1 for r in rows if r["meta"]["tool"] == "none")
    for q, kind in spec.negative_slice(split, n_neg - emitted_neg):
        if q.strip().lower() in seen or emitted_neg >= n_neg:
            continue
        seen.add(q.strip().lower())
        emitted_neg += 1
        idc += 1
        rows.append({
            "id": f"{split[:3]}-{idc:05d}", "query": q, "answers": [],
            "reasoning": "off-topic: no calendar action applies",
            "meta": {"tool": "none", "temporal_class": "none",
                     "range_type": "none", "participant_count": 0,
                     "explicitness": "direct", "language": "de",
                     "source_family": {"offtopic": {"train": "neg_offtopic",
                                                   "validation": "neg_offtopic_val",
                                                   "test": "neg_offtopic_test"},
                                       "adjacent": {"train": "neg_adjacent",
                                                    "validation": "neg_adjacent_val",
                                                    "test": "neg_adjacent_test"}}[kind][split],
                     "split": split}})
    rng.shuffle(rows)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--train", type=int, default=10000)
    ap.add_argument("--validation", type=int, default=1000)
    ap.add_argument("--test", type=int, default=1800)
    args = ap.parse_args()

    schemas, schema_hash = production_schemas()
    out = FT_DIR / "data"
    out.mkdir(exist_ok=True)
    (FT_DIR / "tools.json").write_text(
        json.dumps(schemas, ensure_ascii=False, indent=1), encoding="utf-8")

    seen: set = set()
    counts = {"train": args.train, "validation": args.validation,
              "test": args.test}
    actual: dict[str, int] = {}
    hashes = {}
    for split, n in counts.items():
        rng_split = random.Random(args.seed * 1000
                                  + {"train": 0, "validation": 1,
                                     "test": 2}[split])
        rows = build_split(schemas, split, n, rng_split, seen)
        path = out / f"{split}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        if len(rows) != n:
            raise SystemExit(
                f"count mismatch for {split}: requested {n}, built "
                f"{len(rows)} — pools/weights exhausted; refusing to write "
                "a silently short dataset")
        hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        actual[split] = len(rows)
        print(f"  {split:<11}{len(rows):>6}  -> {path.name}")

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"],
                                capture_output=True, text=True,
                                cwd=FT_DIR.parents[2]).stdout.strip()
    except Exception:
        commit = ""
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_commit": commit,
        "provenance": {
            "build_dataset_sha256": _sha256_file(FT_DIR / "build_dataset.py"),
            "dataset_spec_sha256": _sha256_file(FT_DIR / "dataset_spec.py"),
            "schema_sha256": schema_hash,
            "seed": args.seed,
        },
        "counts": actual,
        "schema_hash": schema_hash,
        "file_sha256": hashes,
        "negative_share": spec.NEGATIVE_SHARE,
        "target_rates": spec.TARGET_RATES,
        "system_facts": spec.SYSTEM_FACTS,
        "gold_convention": "sparse/evidenced-only: arguments omitted "
                           "when the query does not evidence them "
                           "(needle finetune convention, gold_ab_report); "
                           "empty arguments dict is legal",
    }
    (FT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  manifest   {FT_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
