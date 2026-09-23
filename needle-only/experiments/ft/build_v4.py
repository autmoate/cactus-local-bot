#!/usr/bin/env python3
"""Dataset v4 — Preservation-Mix (Phase 2).

Ziel: drei Fähigkeiten gemeinsam schützen, statt v3 einfach mehrfach zu trainieren:
  atomic specialization · multi-call preservation · refusal/off-topic preservation

Mix (Defaults): 20 000 atomic · 5 000 multi · 3 000 negatives+near-negatives
(~71/18/11). Atomic wird über mehrere Generator-Seeds (Shards) gewonnen —
gleiche Template-Familien, andere Werte → mehr Vielfalt OHNE die eingefrorene
`dataset_spec` zu ändern.

Bewusst NICHT trainiert (eval-only): dependent chains (find_slot->create,
resolve->move/delete) — sie brauchen Toolresultate zwischen den Calls.

Erzeugt: data/train_v4.jsonl + data/v4_manifest.json (+ val-Slice für Modal).
Das frozen test.jsonl bleibt unangetastet.

Usage:  PYTHONPATH=src uv run python experiments/ft/build_v4.py
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
sys.path.insert(0, str(FT_DIR))
sys.path.insert(0, str(FT_DIR.parents[1] / "src"))

import build_v3  # noqa: E402

# ---------------------------------------------------------------- near-negatives
OFF_TOPIC = [
    "Wie wird das Wetter morgen in {city}?", "Erzähl mir einen Witz über {topic}.",
    "Wie viele Kalorien hat {food}?", "Was kostet ein {product} gerade?",
    "Übersetze '{word}' ins Englische.", "Wie spät ist es in {city}?",
    "Wer hat {topic} erfunden?", "Gibt es ein gutes Buch über {topic}?",
    "Wie wird '{word}' geschrieben?", "Was ist die Hauptstadt von {country}?",
    "Empfiehl mir ein Restaurant in {city}.", "Wie hoch ist der {product}-Preis?",
    "Was bedeutet '{word}' genau?", "Welche Farbe hat ein {food}?",
    "Wer gewann die letzte WM?", "Wie lange fliegt man nach {country}?",
    "Ist {product} wasserdicht?", "Wie alt ist die {month}-Ausgabe?",
    "Schreib mir ein Gedicht über {topic}.", "Was ist besser: {a} oder {b}?",
]
CALENDAR_ADJACENT = [
    "Wie viele Urlaubstage stehen mir gesetzlich zu?",
    "Was ist der Unterschied zwischen Termin und Frist?",
    "Wie funktioniert eine Kalender-Synchronisation technisch?",
    "Welche Kalender-App ist die beste?",
    "Erkläre mir die Begriffe Outlook und iCal.",
    "Wie viele Arbeitstage hat ein Jahr durchschnittlich?",
]
ORDINARY_LIST = [
    "Einkaufsliste: {a}, {b}, {c}.", "Packliste: {a}, {b}, {c}.",
    "Merkliste: {a}, {b}, {c}.", "Meine Lieblingsfilme: {a}, {b}, {c}.",
    "Bücher, die ich lesen will: {a}, {b}, {c}.",
    "Wochenplan Kochen: Montag {a}, Dienstag {b}, Mittwoch {c}.",
]
KNOWLEDGE_DATE = [
    "Wann wurde {person} geboren?", "Wie viele Tage hat der {month}?",
    "Was geschah im Jahr {year}?", "Wie viele Tage hat ein Schaltjahr?",
    "Welcher Wochentag war der {date}?",
]
UNACTIONABLE = [
    "Mach das mal.", "Kümmere dich darum.", "Ändere das bitte.",
    "Das passt so nicht.", "Mach irgendwas.", "Bitte korrigieren.",
    "Und jetzt?", "Wie meinst du das?", "Was hältst du davon?",
]
PREFIXES = ["", "Bitte: ", "Kurz gesagt: ", "Sag mir: ", "Ich möchte wissen: ",
            "Mal eine Frage: ", "Weißt du: "]

POOLS = {
    "city": ["Berlin", "Hamburg", "Wien", "Zürich", "Lissabon", "Rom", "Kopenhagen", "Prag"],
    "topic": ["Kaffee", "Dampfmaschinen", "Comics", "Käse", "Fahrräder", "Uhren",
              "Papier", "Tee", "Wolken", "Briefmarken"],
    "food": ["ein Apfel", "eine Pizza", "ein Brot", "eine Banane", "ein Kuchen",
             "eine Birne", "ein Salat"],
    "product": ["e-Bike", "Espressomaschine", "Zelt", "Tablet", "Wasserkocher",
                "Rucksack", "Staubsauger"],
    "word": ["serendipity", "deadline", "Kalenderblatt", "Termin"],
    "country": ["Kanada", "Japan", "Portugal", "Norwegen", "Chile", "Island"],
    "a": ["Milch", "Zelt", "Kaffee", "Inception", "Mehl", "Schokolade"],
    "b": ["Brot", "Schlafsack", "Tee", "Matrix", "Zucker", "Nudeln"],
    "c": ["Eier", "Regenjacke", "Zucker", "Interstellar", "Salz", "Reis"],
    "person": ["Mozart", "Marie Curie", "Konrad Zuse", "Ada Lovelace"],
    "month": ["September", "Februar", "April"], "year": ["1989", "2000", "1990"],
    "date": ["3. Oktober 1990", "1. Januar 2000"],
}


def _fill(tmpl: str, rng) -> str:
    import re
    for name in re.findall(r"\{(\w+)\}", tmpl):
        tmpl = tmpl.replace("{" + name + "}", rng.choice(POOLS[name]))
    return tmpl


def near_negatives(n: int, rng, forbidden: set[str] | None = None) -> list[str]:
    banks = [OFF_TOPIC, CALENDAR_ADJACENT, ORDINARY_LIST, KNOWLEDGE_DATE, UNACTIONABLE]
    forbidden = forbidden or set()
    out, seen = [], set()
    i = 0
    while len(out) < n and i < n * 40:
        bank = banks[i % len(banks)]
        q = _fill(bank[rng.randrange(len(bank))], rng)
        if rng.random() < 0.5:
            q = rng.choice(PREFIXES) + q
        i += 1
        k = q.lower()
        if k in seen or k in forbidden:
            continue
        seen.add(k)
        out.append(q)
    return out


def _shard(seed: int, tmp: Path) -> list[dict]:
    """Atomic-Shard über den Produktionsgenerator (Familien unverändert)."""
    d = tmp / f"s{seed}"
    subprocess.run([sys.executable, str(FT_DIR / "build_dataset.py"),
                    "--train", "10000", "--seed", str(seed),
                    "--out-dir", str(d), "--only-train"],
                   check=True, capture_output=True)
    return [json.loads(l) for l in open(d / "data" / "train.jsonl", encoding="utf-8")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--atomic", type=int, default=20000)
    ap.add_argument("--multi", type=int, default=5000)
    ap.add_argument("--negatives", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        atomic: list[dict] = []
        seen: set[str] = set()
        # v2 selbst (Seed 42) + zwei weitere Shards → mehr Wertevielfalt
        for seed, rows in [(42, [json.loads(l) for l in
                                 open(FT_DIR / "data" / "train.jsonl", encoding="utf-8")]),
                           (43, None), (44, None)]:
            rows = rows if rows is not None else _shard(seed, tmp)
            for r in rows:
                if r["meta"]["tool"] == "none":
                    continue
                k = r["query"].strip().lower()
                if k in seen:
                    continue
                seen.add(k)
                atomic.append(r)
    rng.shuffle(atomic)
    atomic = atomic[:args.atomic]
    print(f"atomic pool: {len(seen)} unique, genutzt {len(atomic)}")

    multi = build_v3.build("train", args.multi, random.Random(args.seed + 7))
    print(f"multi: {len(multi)}")

    # Negatives: v2-Off-Topic (aus dem Generator) + List-Negatives v3 + Near-Negatives
    v2_neg = [json.loads(l) for l in
              open(FT_DIR / "data" / "train.jsonl", encoding="utf-8")
              if json.loads(l)["meta"]["tool"] == "none"]
    v3_neg = [{"query": q, "answers": [],
               "reasoning": "off-topic: no calendar action applies"} for q in build_v3.NEGATIVES]
    forbidden = seen | {r["query"].strip().lower() for r in multi}
    nn = near_negatives(args.negatives - len(v2_neg) - len(v3_neg), rng, forbidden)
    near = [{"query": q, "answers": [],
             "reasoning": "off-topic: no calendar action applies"} for q in nn]
    negatives = v2_neg + v3_neg + near
    rng.shuffle(negatives)
    negatives = negatives[:args.negatives]
    print(f"negatives: {len(negatives)} (v2 {len(v2_neg)} / v3 {len(v3_neg)} / near {len(nn)})")

    def _tag(row: dict, kind: str, i: int) -> dict:
        r = dict(row)
        r["id"] = f"v4-{kind}-{i:06d}"
        r.setdefault("meta", {})
        r["meta"] = {**r["meta"], "v4_kind": kind}
        return r

    rows = ([_tag(r, "atomic", i) for i, r in enumerate(atomic)]
            + [_tag(r, "multi", i) for i, r in enumerate(multi)]
            + [_tag(r, "negative", i) for i, r in enumerate(negatives)])
    rng.shuffle(rows)

    # val-Slice für Modal (10 %, family-sicher genug für Loss-Sicht; kein Eval)
    n_val = max(200, len(rows) // 10)
    val, train = rows[:n_val], rows[n_val:]
    data = FT_DIR / "data"
    for name, part in (("train_v4.jsonl", train), ("validation_v4.jsonl", val)):
        (data / name).write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                           for r in part) + "\n", encoding="utf-8")
    # Mix NACH dem Split aus den tatsächlichen Tags zählen (vorher sums/val-Split=111%-Bug)
    from collections import Counter as _C
    tag_train = _C(r["meta"]["v4_kind"] for r in train)
    tag_val = _C(r["meta"]["v4_kind"] for r in val)
    counts = {"train": len(train), "validation": len(val),
              "atomic": len(atomic), "multi": len(multi), "negative": len(negatives),
              "train_by_kind": dict(tag_train), "validation_by_kind": dict(tag_val)}
    mix = {k: round(tag_train[k] / len(train), 3) for k in ("atomic", "multi", "negative")}
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed, "counts": counts, "mix_train": mix,
        "atomic_sources": ["data/train.jsonl (seed 42) + Generator-Shards seed 43/44"],
        "multi_source": "build_v3.py Familien (2-4 unabhängige Calls in Text-Reihenfolge)",
        "negative_categories": ["v2 off-topic", "v3 Listen-Negatives",
                                "near-neg: off-topic/adjacent/ordinary-list/"
                                "knowledge-with-dates/unactionable"],
        "not_trained": ["dependent chains (find_slot->create, resolve->move/delete) — eval-only"],
        "frozen_test_untouched": "data/test.jsonl (unverändert, relativ)",
        "file_sha256": {n: hashlib.sha256((data / n).read_bytes()).hexdigest()
                        for n in ("train_v4.jsonl", "validation_v4.jsonl")},
    }
    (data / "v4_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(f"v4 train={counts['train']} val={counts['validation']} mix={mix}")
    for r in train[:2]:
        print(f"  Bsp [{r['meta']['v4_kind']}]: {r['query'][:60]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
