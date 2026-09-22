#!/usr/bin/env python3
"""Dataset v3 = v2 (atomar) + Multi-Call-Anteil (~25 %) + Listen-Negatives.

Multi-Call = 2–4 unabhängige Aktionen in EINEM Request, Reihenfolge wie im Text
(Needle-3-Vertrag). Gold ist sparse/evidenced-only wie v2; jeder Argumentwert
ist ein literaler Substring der Query. Held-out-Werte des frozen v2-Tests
(TÜV, Miriam, Felix …) werden im v3-Train gemieden, damit die atomare
Testmessung nicht leakt.

Erzeugt: data/train_v3.jsonl, data/validation_v3.jsonl, data/v3_manifest.json
Nicht trainiert (eval-only): dependent chains (find_slot→create, resolve→move).

Usage:  PYTHONPATH=src uv run python experiments/ft/build_v3.py
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FT_DIR))
import dataset_spec as spec  # noqa: E402

SEED = 42
MC_SHARE = 0.25          # Anteil Multi-Call an den v3-Positives
N_TRAIN = 10000          # v2-Größe bleibt die Basis; Multi kommt zusätzlich
N_VAL = 1000

TITLES = ["Zahnarzt", "Teammeeting", "Kino", "Friseur", "Yoga", "Training",
          "Workshop", "Chorprobe", "Physiotherapie", "Sprachkurs", "Kegelabend",
          "Betriebsausflug", "Arztbesuch", "Buchhaltung", "Gitarrenunterricht",
          "Vorstandssitzung", "Mitarbeitergespräch", "Fuhrparktermin", "Steuertermin"]
TITLES2 = ["Meeting", "Sport", "Mittagessen", "Geburtstag", "Ausflug", "Umzug",
           "Probe", "Massage", "Vortrag", "Schulung", "Sprint", "Brunch"]
DOWS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
TIMES = ["8 Uhr", "9 Uhr", "10 Uhr", "11 Uhr", "13 Uhr", "14 Uhr", "15 Uhr", "16 Uhr", "17 Uhr", "18 Uhr"]
PERSONS = ["Lisa", "Max", "Jana", "Peter", "Sophie", "Tim", "Lena", "Jonas", "Marie", "Paul"]
HELD_OUT = set(spec.TITLES_EVAL_ONLY + spec.ABSENCE_TITLES_EVAL_ONLY + spec.NAMES_EVAL_ONLY)


def _c(title=None, date=None, time=None, participants=None):
    args = {}
    for k, v in (("title", title), ("date", date), ("time", time),
                 ("participants", participants)):
        if v:
            args[k] = v
    return {"name": "calendar_create", "arguments": args}


def _families(rng, split):
    """(query, calls, family_id) — Familien je Split exklusiv."""
    t = rng.sample(TITLES, 3)
    t2 = rng.sample(TITLES2, 3)
    p = rng.sample(PERSONS, 2)
    d = rng.sample(DOWS, 3)
    h = rng.sample(TIMES, 3)
    suffix = "val" if split == "validation" else "train"
    fams = [
        (f"mc-two-und-{suffix}",
         f"Trag {d[0]} um {h[0]} {t[0]} ein und {d[1]} um {h[1]} {t[1]} ein.",
         [_c(t[0], d[0], h[0]), _c(t[1], d[1], h[1])]),
        (f"mc-three-comma-{suffix}",
         f"{d[0]} {t[0]} {h[0]}, {d[1]} {t[1]} {h[1]}, {d[2]} {t[2]} {h[2]}",
         [_c(t[0], d[0], h[0]), _c(t[1], d[1], h[1]), _c(t[2], d[2], h[2])]),
        (f"mc-three-bullet-{suffix}",
         f"Mach am {d[0]}:\n- {h[0]} {t[0]}\n- {h[1]} {t[1]} mit {p[0]}\n- {h[2]} {t[2]}",
         [_c(t[0], d[0], h[0]), _c(t[1], d[0], h[1], p[0]), _c(t[2], d[0], h[2])]),
        (f"mc-four-{suffix}",
         f"Trag ein: {d[0]} {h[0]} {t[0]}, {d[0]} {h[1]} {t[1]}, {d[1]} {h[0]} {t[2]}, {d[1]} {h[1]} {t2[0]}",
         [_c(t[0], d[0], h[0]), _c(t[1], d[0], h[1]), _c(t[2], d[1], h[0]), _c(t2[0], d[1], h[1])]),
        (f"mc-move-delete-{suffix}",
         f"Verschieb {t[0]} auf {d[0]} {h[0]} und lösch {t[1]}.",
         [{"name": "calendar_move", "arguments": {"title": t[0], "date": d[0], "time": h[0]}},
          {"name": "calendar_delete", "arguments": {"title": t[1]}}]),
        (f"mc-delete-create-{suffix}",
         f"Lösch {t[0]} und trag {d[0]} {h[0]} {t2[1]} ein.",
         [{"name": "calendar_delete", "arguments": {"title": t[0]}},
          _c(t2[1], d[0], h[0])]),
        (f"mc-list-create-{suffix}",
         f"Zeig mir meine Termine am {d[0]} und trag {d[1]} {h[0]} {t[0]} ein.",
         [{"name": "calendar_list", "arguments": {"date": d[0]}},
          _c(t[0], d[1], h[0])]),
        (f"mc-two-mit-{suffix}",
         f"{t2[2]} mit {p[0]} am {d[0]} um {h[0]} und {t2[1]} am {d[1]} um {h[1]}.",
         [_c(t2[2], d[0], h[0], p[0]), _c(t2[1], d[1], h[1])]),
        (f"mc-three-und-{suffix}",
         f"Trag {d[0]} {h[0]} {t[0]} ein, {d[1]} {h[1]} {t[1]} und {d[2]} {h[2]} {t[2]}.",
         [_c(t[0], d[0], h[0]), _c(t[1], d[1], h[1]), _c(t[2], d[2], h[2])]),
        (f"mc-create-two-{suffix}",
         f"Erstelle einen Termin {t[0]} am {d[0]} um {h[0]} und einen Termin {t[1]} am {d[1]} um {h[1]}.",
         [_c(t[0], d[0], h[0]), _c(t[1], d[1], h[1])]),
    ]
    return fams


NEGATIVES = [
    "Einkaufsliste: Milch, Brot, Eier, Käse.",
    "Packliste: Zelt, Schlafsack, Wanderschuhe, Regenjacke.",
    "Meine Lieblingsfilme: Inception, Matrix, Interstellar.",
    "Wochenplan Kochen: Montag Pasta, Dienstag Curry, Mittwoch Salat.",
    "Bücher, die ich lesen will: Faust, Siddhartha, Der Prozess.",
    "Merkliste: Batterien, Glühbirne, Klebeband.",
    "Geburtstagsgäste: Anna, Ben, Clara, David.",
]


def _grounded(calls, query) -> bool:
    q = query.lower()
    for c in calls:
        for k, v in c["arguments"].items():
            if str(v).lower() not in q:
                return False
    return True


def build(split: str, n_mc: int, rng) -> list[dict]:
    rows, seen = [], set()
    fams = _families(rng, split)
    i = 0
    while len(rows) < n_mc:
        rng2 = random.Random(rng.randrange(1 << 30))
        fam_id, query, calls = _families(rng2, split)[i % len(fams)]
        i += 1
        if query.lower() in seen or not _grounded(calls, query):
            continue
        if any(v in HELD_OUT for c in calls for v in c["arguments"].values()):
            continue
        seen.add(query.lower())
        rows.append({"id": f"mc-{'val' if split == 'validation' else 'tra'}-{len(rows):05d}",
                     "query": query, "answers": calls,
                     "reasoning": "; ".join(
                         f"{c['name']}: " + ", ".join(f"{k} span from query" for k in c["arguments"])
                         for c in calls),
                     "meta": {"tool": "multi", "n_calls": len(calls),
                              "source_family": fam_id, "split": split,
                              "language": "de", "temporal_class": "none",
                              "range_type": "none", "participant_count": 0}})
    if split == "train":
        for q in NEGATIVES:
            rows.append({"id": f"mc-tra-neg-{len(rows):05d}", "query": q, "answers": [],
                         "reasoning": "off-topic: no calendar action applies",
                         "meta": {"tool": "none", "n_calls": 0, "source_family": "mc_neg",
                                  "split": split, "language": "de", "temporal_class": "none",
                                  "range_type": "none", "participant_count": 0}})
    return rows


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    data = FT_DIR / "data"
    train = [json.loads(l) for l in open(data / "train.jsonl", encoding="utf-8")]
    val = [json.loads(l) for l in open(data / "validation.jsonl", encoding="utf-8")]
    n_pos_v2 = sum(1 for r in train if r.get("answers"))
    n_mc = round(n_pos_v2 * MC_SHARE)

    mc_train = build("train", n_mc, random.Random(SEED))
    mc_val = build("validation", max(20, n_mc // 10), random.Random(SEED + 1))

    # Multi-Call-zeilen tragen noch kein tools/system (Injektion macht train_rtx)
    v3_train = train + mc_train
    v3_val = val + mc_val
    random.Random(SEED).shuffle(v3_train)
    random.Random(SEED).shuffle(v3_val)
    for name, rows in (("train_v3.jsonl", v3_train), ("validation_v3.jsonl", v3_val)):
        (data / name).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                                 encoding="utf-8")

    counts = Counter("multi" if r["meta"].get("n_calls") is not None else "v2"
                     for r in v3_train)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED, "multi_call_share": MC_SHARE,
        "counts": {"train": len(v3_train), "validation": len(v3_val),
                   "multi_call_train": len(mc_train), "multi_call_val": len(mc_val)},
        "mix_train": dict(counts),
        "file_sha256": {n: _sha(data / n) for n in ("train_v3.jsonl", "validation_v3.jsonl")},
        "base_dataset": "data/train.jsonl + data/validation.jsonl (v2, atomar)",
        "gold_convention": "sparse/evidenced-only (wie v2)",
        "not_trained": ["dependent chains (find_slot->create, resolve->move/delete) — eval-only"],
    }
    (data / "v3_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"v3 train={len(v3_train)} (davon multi {len(mc_train)}) val={len(v3_val)} (multi {len(mc_val)})")
    print(f"  mix_train={dict(counts)}")
    for r in mc_train[:2]:
        print(f"  Bsp: {r['query'][:60]!r} -> {[c['name'] for c in r['answers']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
