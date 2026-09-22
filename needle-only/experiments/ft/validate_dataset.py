#!/usr/bin/env python3
"""Hard dataset validator + coverage report (plan §16).

Fails (exit != 0) on: unknown tool, unknown argument, wrong argument type,
missing required field, cross-split exact duplicates, template-family in
two splits, held-out value in train, invalid temporal class, empty gold on
a positive, non-grounded string argument. Otherwise writes a coverage
report to reports/dataset_coverage.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
NEEDLE_ONLY = FT_DIR.parents[1]
sys.path.insert(0, str(FT_DIR))
sys.path.insert(0, str(NEEDLE_ONLY / "src"))  # local_calendar (Root-venv)

import dataset_spec as spec  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402

REQUIRED = {"calendar_create": ["title"], "calendar_move": ["title"],
            "calendar_delete": ["title"], "calendar_list": [],
            "calendar_find_slot": ["persons"]}
REQUIRED_FOR = REQUIRED

errors: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def validate_row(row: dict, schemas: dict, families_by_split: dict,
                 seen: dict, held_out: list[str]) -> dict:
    tool = row["meta"]["tool"]
    answers = row.get("answers") or []
    meta = row["meta"]
    split = meta.get("split")
    query = row["query"]

    # family/split exclusivity (plan §10)
    fam = families_by_split.get(meta.get("source_family"))
    if fam is None:
        err(f"{row['id']}: unknown family {meta.get('source_family')!r}")
    elif fam["split"] != split:
        err(f"{row['id']}: family {fam['id']} used in split {split} "
            f"but belongs to {fam['split']}")
    # exact duplicate control (plan §16)
    key = (query.strip().lower(), json.dumps(answers, sort_keys=True))
    if key in seen:
        err(f"{row['id']}: exact duplicate of earlier row")
    seen.add(key)
    # held-out values must not appear in train (plan §9, word-boundary)
    if split == "train":
        for v in held_out:
            if re.search(rf"\b{re.escape(v)}\b", query, re.IGNORECASE):
                err(f"{row['id']}: held-out value {v!r} leaked into train")

    if tool == "none":
        if answers:
            err(f"{row['id']}: negative row carries answers")
        return {"negative": True}
    if answers and tool != answers[0].get("name"):
        err(f"{row['id']}: meta.tool {tool} != answers name")
        return {}
    if not answers:
        err(f"{row['id']}: no answers on positive row")
        return {}
    # sparse convention: empty arguments dict is LEGAL when no field is
    # evidenced (all list/find params are optional with handler defaults) —
    # required fields are checked separately
    if tool not in schemas:
        err(f"{row['id']}: unknown tool {tool!r}")
        return {}

    props = schemas[tool]["parameters"]["properties"]
    args = answers[0]["arguments"]
    for k in args:
        if k not in props:
            err(f"{row['id']}: unknown argument {k!r} for {tool}")
    for k in REQUIRED_FOR.get(tool, []):
        if not str(args.get(k, "")).strip():
            err(f"{row['id']}: required field {k!r} empty for {tool}")
    # types
    for k, prop in props.items():
        v = args.get(k)
        if v is None:
            continue
        if prop.get("type") == "integer":
            if not isinstance(v, int) or isinstance(v, bool):
                err(f"{row['id']}: {k} must be int, got {v!r}")
        elif "enum" in prop and str(v) not in prop["enum"]:
            err(f"{row['id']}: {k}={v!r} not in enum {prop['enum']}")
        elif prop.get("type") == "string" and not isinstance(v, str):
            err(f"{row['id']}: {k} must be string, got {v!r}")
    # grounding: string values are query substrings (plan §13); documented
    # exceptions: enums (horizon) + ints are structural, empty strings skip
    for k, prop in props.items():
        v = args.get(k, "")
        if prop.get("type") == "string" and v and "enum" not in prop \
                and v.lower() not in query.lower():
            err(f"{row['id']}: arg {k}={v!r} is not a query substring")
    # sparse convention: default-carrying fields need query evidence
    # (user review Sep 16: never supervise handler defaults)
    for k, trig in spec.EVIDENCE_TRIGGERS.items():
        v = args.get(k)
        if v in ("", None):
            continue
        import re as _re
        if not _re.search(trig, query, _re.IGNORECASE):
            err(f"{row['id']}: {k}={v!r} set without query evidence "
                "(sparse convention violation)")
    # temporal class validity
    tc = meta.get("temporal_class", "none")
    if tc == "other" and str(args.get("date", "")):
        err(f"{row['id']}: unclassified temporal span {args.get('date')!r}")
    return {"negative": False, "tool": tool,
            "temporal_class": tc, "language": meta.get("language"),
            "participant_count": meta.get("participant_count"),
            "range_type": meta.get("range_type"),
            "family": meta.get("source_family")}


REQUIRED_FOR = REQUIRED  # alias for the loop below


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(FT_DIR / "data"))
    args = ap.parse_args()
    data = Path(args.data)

    tools = {s["name"]: s for s in
             json.load(open(FT_DIR / "tools.json", encoding="utf-8"))}
    families_by_split = {f["id"]: f for f in spec.FAMILIES}
    held_out = [t.lower() for t in spec.TITLES_EVAL_ONLY
                + spec.ABSENCE_TITLES_EVAL_ONLY + spec.NAMES_EVAL_ONLY]

    seen: set = set()
    rows_by_split: dict[str, list[dict]] = {}
    stats = {s: Counter() for s in ("train", "validation", "test")}
    field_stats = Counter()
    fam_splits: dict[str, set] = {}
    for split in ("train", "validation", "test"):
        path = data / f"{split}.jsonl"
        rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        rows_by_split[split] = rows
        for row in rows:
            info = validate_row(row, tools, {f["id"]: f for f in spec.FAMILIES},
                                seen, held_out)
            fam_splits.setdefault(row["meta"]["source_family"], set()).add(split)
            if info.get("negative"):
                stats[split]["negative"] += 1
            elif info.get("tool"):
                stats[split][info["tool"]] += 1
                stats[split]["temporal:" + info["temporal_class"]] += 1
                stats[split]["lang:" + info["language"]] += 1
                for k, v in tools.get(info["tool"], {}).get(
                        "parameters", {}).get("properties", {}).items():
                    if row["answers"][0]["arguments"].get(k):
                        field_stats[k] += 1
    # family split-exclusivity across the whole dataset
    for fam_id, splits in fam_splits.items():
        if len(splits) > 1:
            err(f"family {fam_id} appears in splits {sorted(splits)}")

    manifest = json.load(open(FT_DIR / "manifest.json", encoding="utf-8"))
    for split, rows in rows_by_split.items():
        want = manifest["file_sha256"].get(f"{split}.jsonl")
        got = hashlib.sha256((data / f"{split}.jsonl").read_bytes()).hexdigest()
        if want and got != want:
            err(f"{split}.jsonl: hash mismatch vs manifest")
    sh = json.load(open(FT_DIR / "tools.json", encoding="utf-8"))
    digest = hashlib.sha256(json.dumps(sh, sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()
    if digest != manifest["schema_hash"]:
        err("tools.json schema hash != manifest (production schema changed?)")

    coverage = {"errors": errors[:50], "error_count": len(errors),
                "counts": {s: len(r) for s, r in rows_by_split.items()},
                "tools": {},
                "temporal": {}, "languages": {},
                "arg_field_usage": dict(field_stats),
                "schema_hash": digest}
    for s, st in stats.items():
        coverage["tools"][s] = {x: v for x, v in st.items()
                                if not x.startswith(("temporal:", "lang:"))}
        coverage["temporal"][s] = {x.split(':', 1)[1]: v
                                   for x, v in st.items()
                                   if x.startswith("temporal:")}
        coverage["languages"][s] = {x.split(':', 1)[1]: v
                                    for x, v in st.items()
                                    if x.startswith("lang:")}
    (FT_DIR / "reports" / "dataset_coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=1), encoding="utf-8")

    if errors:
        print(f"VALIDATION FAILED: {len(errors)} errors")
        for e in errors[:30]:
            print("  -", e)
        raise SystemExit(1)
    print("validation: OK")
    print(json.dumps(coverage, ensure_ascii=False, indent=1)[:2000])


if __name__ == "__main__":
    main()
