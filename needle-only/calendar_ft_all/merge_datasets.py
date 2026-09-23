#!/usr/bin/env python3
"""Baut das calendar_all-Merged-Dataset: 3 Task-Sets, ein 3-Tool-Katalog.

Regeln (needle finetuning doc + Cactus-Blog):
  - Jedes Beispiel rendert den KOMPLETTEN Tool-Katalog (frozen order:
    calendar_write -> calendar_read -> reminder) -> Routing wird mittrainiert.
  - Cross-Task-Negatives aus den Einzelsets sind im gemergten Katalog FALSCHE
    Labels (die Query passt zu einem deklarierten Tool) und werden anhand
    ihres `reasoning`-Markers gefiltert; nur echtes Off-Topic bleibt.
  - Seed-Daten: seed/ambiguous.jsonl (Routing-Grenzfälle, korrekt aufgelöst)
    und seed/offtopic.jsonl (neue deutsche Off-Topic-Negatives; 2/3 train,
    1/3 held-out eval, deterministisch).

Usage:  python merge_datasets.py
Out:    data/train/calendar_all.jsonl, data/eval/calendar_all.jsonl
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ALL_DIR = Path(__file__).resolve().parent
FT_DIR = ALL_DIR.parent / "calendar_ft"

TASKS = ["calendar_write", "calendar_read", "reminder"]  # frozen order (Blog: Reihenfolge einfrieren)
OFF_TOPIC_REASONING = "off-topic, no calendar intent"


def load_catalog() -> list:
    return [json.loads((ALL_DIR / "schemas" / f"{t}.json").read_text()) for t in TASKS]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def with_catalog(ex: dict, catalog: list) -> dict:
    return {**ex, "tools": catalog}


def merge(split: str, catalog: list) -> list:
    merged, seen = [], set()

    def add(ex: dict) -> bool:
        key = ex["query"].strip().lower()
        if key in seen:
            return False
        seen.add(key)
        merged.append(with_catalog(ex, catalog))
        return True

    stats = {}
    for task in TASKS:
        rows = load_jsonl(FT_DIR / "data" / split / f"{task}.jsonl")
        pos = neg = dropped = 0
        for ex in rows:
            if ex.get("answers"):
                if add(ex):
                    pos += 1
            elif ex.get("reasoning", "").strip() == OFF_TOPIC_REASONING:
                if add(ex):  # Cross-Task-Negatives wegfiltern — falsche Labels im Katalog
                    neg += 1
            else:
                dropped += 1
        stats[task] = (pos, neg, dropped)

    # Seeds (nur im Merge-Sinnd neu): ambiguous -> train; offtopic 2/3 train, 1/3 eval
    amb_train = amb_eval = off_train = off_eval = 0
    if split == "train":
        for ex in load_jsonl(ALL_DIR / "seed" / "ambiguous.jsonl"):
            amb_train += add(ex)
        seed_off = load_jsonl(ALL_DIR / "seed" / "offtopic.jsonl")
        rng = random.Random(42)
        rng.shuffle(seed_off)
        for i, ex in enumerate(seed_off):
            if i % 3 == 2:  # jede dritte Zeile held-out für FP-Messung
                continue
            off_train += add(ex)
    else:
        seed_off = load_jsonl(ALL_DIR / "seed" / "offtopic.jsonl")
        rng = random.Random(42)
        rng.shuffle(seed_off)
        for i, ex in enumerate(seed_off):
            if i % 3 == 2:
                off_eval += add(ex)

    pos_all = sum(p for p, _, _ in stats.values())
    seed_neg = off_train if split == "train" else off_eval
    neg_all = sum(n for _, n, _ in stats.values()) + seed_neg
    print(f"[{split}] pos={pos_all} neg={neg_all} (builder+seed) + ambiguous={amb_train or amb_eval} "
          f"| dropped-cross-task={sum(d for _, _, d in stats.values())} "
          f"=> {len(merged)} Beispiele ({neg_all / max(1, len(merged)) * 100:.1f}% negatives)")
    for t, (p, n, d) in stats.items():
        print(f"   {task if False else t:15s} pos={p:4d} neg(off)={n:3d} dropped={d:3d}")
    return merged


def main() -> int:
    catalog = load_catalog()
    for split in ("train", "eval"):
        merged = merge(split, catalog)
        out = ALL_DIR / "data" / split / "calendar_all.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(ex, ensure_ascii=False) for ex in merged) + "\n")
        print(f"  -> {out} ({len(merged)} Zeilen)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
