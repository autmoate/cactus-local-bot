#!/usr/bin/env python3
"""Hard validator for the Thunderbird FT dataset (FT_PLAN.md §7).

Checks (raises with a clear message on the first failure):
- schema hash matches the frozen contract; counts match the manifest
- every positive has a non-empty `when` that is a VERBATIM substring of the
  model input; `title`/`location`, when set, are substrings too
- if the input carries an end time, the gold `when` must carry it as well
- if the input carries a timezone, the gold `when` must carry a timezone
- negatives are exactly the empty call []
- families are split-exclusive; eval-only values never leak into train
- review flag matches the temporal class (timezone/fuzzy -> review)
- no duplicate queries

Also prints a coverage report. Run:
  cd needle-only && uv run python \
    experiments/business_cases/thunderbird_calendar/ft/validate_dataset_tb.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import dataset_spec_tb as spec  # noqa: E402
from build_dataset_tb import tool_schema  # noqa: E402

TZ_RE = re.compile(r"\b(CET|CEST|ET|EST|EDT|BST|GMT|UTC|PT|PST|PDT|Pacific)\b", re.I)
# Only a real clock range (uses "bis" or "Uhr -"), not a signature like "Mo 8-12".
RANGE_RE = re.compile(
    r"(?:\d{1,2}(?::\d{2})?\s*(?:uhr\s+)?bis\s+\d{1,2}"
    r"|\d{1,2}\s*uhr\s*[-–]\s*\d{1,2})", re.I)
REVIEW_CLASSES = {"timezone", "fuzzy"}
EVAL_ONLY = (spec.TITLES_EVAL + spec.PERSONS_EVAL + spec.ROOMS_EVAL)


def load(split: str) -> list[dict]:
    path = HERE / "data" / f"{split}.jsonl"
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def fail(msg: str):
    raise SystemExit(f"VALIDATION FAILED: {msg}")


def main() -> int:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    _, schema_hash = tool_schema()
    if schema_hash != manifest["provenance"]["schema_sha256"]:
        fail("schema hash mismatch (contract changed since build)")

    splits = ["train", "validation", "test"]
    data = {s: load(s) for s in splits}
    seen: dict[str, str] = {}
    family_split: dict[str, str] = {}
    coverage: dict = {}

    for split in splits:
        rows = data[split]
        if len(rows) != manifest["counts"][split]:
            fail(f"{split}: {len(rows)} rows != manifest "
                 f"{manifest['counts'][split]}")
        cov = {"total": len(rows), "positive": 0, "negative": 0,
               "message": 0, "selection": 0, "review": 0,
               "temporal_class": {}, "family": {}}
        for r in rows:
            key = r["query"].strip().lower()
            if key in seen:
                fail(f"duplicate query ({r['id']} == {seen[key]})")
            seen[key] = r["id"]
            meta, answers = r["meta"], r["answers"]
            fam = meta["family"]
            if fam in family_split and family_split[fam] != split:
                fail(f"family {fam} spans splits {family_split[fam]}/{split}")
            family_split[fam] = split
            cov["family"][fam] = cov["family"].get(fam, 0) + 1
            cov["temporal_class"][meta["temporal_class"]] = \
                cov["temporal_class"].get(meta["temporal_class"], 0) + 1
            cov["message" if meta["mode"] == "message" else "selection"] += 1
            if meta["review"]:
                cov["review"] += 1

            if not meta["event"]:
                if answers != [] or meta["tool"] != "none":
                    fail(f"{r['id']}: negative must be empty call []")
                cov["negative"] += 1
                continue
            cov["positive"] += 1
            if meta["tool"] != spec.TOOL_NAME:
                fail(f"{r['id']}: positive without {spec.TOOL_NAME}")
            if len(answers) != 1 or answers[0]["name"] != spec.TOOL_NAME:
                fail(f"{r['id']}: positive must be exactly one {spec.TOOL_NAME}")
            args = answers[0]["arguments"]
            when = args.get("when", "")
            if not when:
                fail(f"{r['id']}: positive with empty when")
            if when not in r["query"]:
                fail(f"{r['id']}: when {when!r} not a substring of input")
            for field in ("title", "location"):
                v = args.get(field)
                if v and v not in r["query"]:
                    fail(f"{r['id']}: {field} {v!r} not a substring of input")
            if RANGE_RE.search(r["query"]) and not re.search(r"(bis|[-–])", when):
                fail(f"{r['id']}: input has an end time but when lacks it")
            if TZ_RE.search(r["query"]) and not TZ_RE.search(when):
                fail(f"{r['id']}: input has a timezone but when lacks it")
            if (meta["temporal_class"] in REVIEW_CLASSES) != bool(meta["review"]):
                fail(f"{r['id']}: review flag inconsistent with "
                     f"{meta['temporal_class']}")
        coverage[split] = cov

    # held-out leak: eval-only values must never appear in train queries
    train_text = "\n".join(r["query"] for r in data["train"])
    for value in EVAL_ONLY:
        if re.search(rf"\b{re.escape(value)}\b", train_text):
            fail(f"held-out value {value!r} leaked into train")

    (HERE / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=1), encoding="utf-8")
    for split in splits:
        c = coverage[split]
        pos_rate = c["positive"] / c["total"]
        print(f"  {split:<11} n={c['total']:<5} pos={pos_rate:.2f} "
              f"msg={c['message']} sel={c['selection']} neg={c['negative']} "
              f"review={c['review']}")
    print("OK — schema, counts, grounding, end-time, timezone, leaks, dupes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
