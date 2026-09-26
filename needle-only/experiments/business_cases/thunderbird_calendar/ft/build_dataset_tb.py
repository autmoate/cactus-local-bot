#!/usr/bin/env python3
"""Deterministic builder for the Thunderbird FT dataset (FT_PLAN.md §7).

- Schema is never re-declared: it is taken from `extractor._make_tools()` (the
  frozen when-first contract) and hashed into the manifest.
- Families are split-exclusive; validation/test use held-out value pools.
- Counts are exact: the builder hard-fails instead of writing a short split.
- Gold is sparse and verbatim: `when` always, `title`/`location` only when they
  appear literally in the model input. Negatives are the empty call `[]`.

Output: data/{train,validation,test}.jsonl, data/smoke.jsonl, tools_tb.json,
manifest.json — all under ft/.

Run:  cd needle-only && uv run python \
        experiments/business_cases/thunderbird_calendar/ft/build_dataset_tb.py
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import dataset_spec_tb as spec  # noqa: E402

SEED = 42
COUNTS = {"train": 7000, "validation": 800, "test": 1400}


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tool_schema() -> tuple[dict, str]:
    """The frozen when-first extract_event schema, straight from extractor."""
    import os
    os.environ.setdefault("TB_CONTRACT_ORDER", "when")
    from extractor import _make_tools
    fn = _make_tools()[0]
    schema = fn._needle_tool
    digest = hashlib.sha256(
        json.dumps(schema, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return schema, digest


def render(case: dict) -> dict:
    if case.get("selection"):
        query = case["selection"]
        mode = "selection"
    else:
        query = f"Betreff: {case['subject']}\n\n{case['body']}"
        mode = "message"
    if case["event"]:
        args = {"when": case["when"]}
        if case["title"]:
            args["title"] = case["title"]
        if case["location"]:
            args["location"] = case["location"]
        answers = [{"name": spec.TOOL_NAME, "arguments": args}]
        reasoning = f"when span '{case['when']}' copied from the message"
        tool = spec.TOOL_NAME
    else:
        answers = []
        reasoning = "no concrete new event -> empty call"
        tool = "none"
    return {
        "query": query,
        "answers": answers,
        "reasoning": reasoning,
        "meta": {
            "tool": tool, "mode": mode, "family": case["family"],
            "temporal_class": case["temporal_class"], "language": case["language"],
            "event": case["event"], "review": case["review"],
            "subject": case["subject"],
        },
    }


def _slots(families: list, n: int) -> list:
    cycle: list = []
    for fn, w in families:
        cycle += [fn] * w
    return [cycle[i % len(cycle)] for i in range(n)]


def build_split(split: str, n: int, rng: random.Random, seen: set) -> list[dict]:
    groups = ["message_pos", "selection_pos", "near_miss", "offtopic"]
    share = spec.TARGET
    n_msg = round(n * share["message_pos"])
    n_sel = round(n * share["selection_pos"])
    n_neg = round(n * share["near_miss"])
    n_off = n - n_msg - n_sel - n_neg
    plan = {"message_pos": n_msg, "selection_pos": n_sel,
            "near_miss": n_neg, "offtopic": n_off}

    rows: list[dict] = []
    for group in groups:
        need = plan[group]
        fns = [fn for fn, _ in spec.FAMILIES[group]]
        weights = [w for _, w in spec.FAMILIES[group]]
        slots = _slots(list(zip(fns, weights)), need)
        got = 0
        for fn in slots:
            for _ in range(40):
                case = fn(rng, split)
                # Family id is split-qualified: the same template may be reused
                # across splits, but validation/test draw held-out values.
                case["family"] = f"{fn.__name__}_{split[:3]}"
                row = render(case)
                key = row["query"].strip().lower()
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
                got += 1
                break
        if got != need:
            raise SystemExit(f"{split}/{group}: emitted {got} != {need} "
                             "(pools exhausted?)")
    rng.shuffle(rows)
    for i, r in enumerate(rows):
        r["id"] = f"{split[:3]}-{i:05d}"
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--train", type=int, default=COUNTS["train"])
    ap.add_argument("--validation", type=int, default=COUNTS["validation"])
    ap.add_argument("--test", type=int, default=COUNTS["test"])
    ap.add_argument("--smoke", type=int, default=200)
    args = ap.parse_args()

    schema, schema_hash = tool_schema()
    (HERE / "tools_tb.json").write_text(
        json.dumps([schema], ensure_ascii=False, indent=1), encoding="utf-8")

    out = HERE / "data"
    out.mkdir(parents=True, exist_ok=True)
    counts = {"train": args.train, "validation": args.validation,
              "test": args.test}
    seen: set = set()
    actual, hashes, rows_by_split = {}, {}, {}
    for split, n in counts.items():
        rng = random.Random(args.seed * 1000
                            + {"train": 0, "validation": 1, "test": 2}[split])
        rows = build_split(split, n, rng, seen)
        path = out / f"{split}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        actual[split] = len(rows)
        hashes[path.name] = _sha256(path)
        rows_by_split[split] = rows
        print(f"  {split:<11}{len(rows):>6}  -> {path.name}")

    smoke_rows = rows_by_split["train"][:args.smoke]
    smoke_path = out / "smoke.jsonl"
    with open(smoke_path, "w", encoding="utf-8") as fh:
        for r in smoke_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    hashes["smoke.jsonl"] = _sha256(smoke_path)

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                text=True, cwd=HERE.parents[4]).stdout.strip()
    except Exception:  # noqa: BLE001
        commit = ""
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_commit": commit,
        "provenance": {
            "build_dataset_tb_sha256": _sha256(HERE / "build_dataset_tb.py"),
            "dataset_spec_tb_sha256": _sha256(HERE / "dataset_spec_tb.py"),
            "schema_sha256": schema_hash, "seed": args.seed,
        },
        "counts": actual, "target_mix": spec.TARGET, "file_sha256": hashes,
        "system_facts": spec.SYSTEM_FACTS, "tool_name": spec.TOOL_NAME,
        "gold_convention": "sparse + verbatim: when always; title/location only "
                           "when literally present; negatives = empty call []",
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  manifest    -> {HERE / 'manifest.json'}  (schema {schema_hash[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
