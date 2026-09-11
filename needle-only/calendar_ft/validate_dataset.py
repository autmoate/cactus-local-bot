#!/usr/bin/env python3
"""Validate calendar-FT JSONL datasets.

Checks (positive examples):
  - valid JSONL, required keys present
  - exactly the expected tool
  - no unknown argument fields
  - argument values are substrings of the query (grounding)
  - optional fields are omitted, never "" placeholders
  - reasoning line present

Checks (negative examples):
  - answers == []

Cross-checks:
  - train/eval exact query duplicates == 0
  - report max token/char length, class distribution, field coverage

Exit code 0 = valid; non-zero = validation errors found.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
SCHEMAS = FT_DIR / "schemas"

EXPECTED_FIELDS = {
    "calendar_write": {"title", "date", "time", "end_time", "person",
                       "participants", "location", "action_span", "modifier"},
    "calendar_read": {"query_span", "when", "person", "persons", "target"},
    "reminder_parse": {"target", "when", "relative", "person"},
}

TASK_TOOL = {
    "calendar_write": "calendar_write",
    "calendar_read": "calendar_read",
    "reminder": "reminder_parse",
}

REQUIRED_FIELDS = {
    # Fields that MUST be present in every positive answer (task contract).
    # calendar_write: title is always the target of the write.
    # Declarative creates ("Zahnarzt morgen um 14 Uhr.") have no verb span.
    "calendar_write": {"title"},
    "calendar_read": {"query_span"},
    "reminder_parse": set(),  # reminder is fully optional-fields driven
}


def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path) as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"INVALID JSON at {path}:{lineno}: {exc}")
    return rows


def validate_dataset(path: str, task: str) -> tuple[list[str], dict]:
    errors = []
    warnings = []
    stats = {
        "total": 0, "positive": 0, "negative": 0,
        "fields": Counter(), "max_query_chars": 0,
        "action_spans": Counter(), "tool_names": Counter(),
    }
    tool = TASK_TOOL[task]
    expected_fields = EXPECTED_FIELDS[tool]
    required_fields = REQUIRED_FIELDS[tool]

    examples = load_jsonl(path)

    for idx, ex in enumerate(examples):
        stats["total"] += 1
        query = ex.get("query", "")
        answers = ex.get("answers", [])

        if not query:
            errors.append(f"[{idx}] empty query")
            continue

        stats["max_query_chars"] = max(stats["max_query_chars"], len(query))

        if not answers:
            stats["negative"] += 1
            if "reasoning" not in ex:
                warnings.append(f"[{idx}] negative without reasoning")
            continue

        stats["positive"] += 1
        if len(answers) > 1:
            errors.append(f"[{idx}] multiple answers not supported: {len(answers)}")

        for ans in answers:
            name = ans.get("name")
            stats["tool_names"][name] += 1
            if name != tool:
                errors.append(f"[{idx}] wrong tool: {name} (expected {tool})")
                continue
            arguments = ans.get("arguments", {})
            for key, value in arguments.items():
                if key not in expected_fields:
                    errors.append(f"[{idx}] unknown field: {key}")
                    continue
                stats["fields"][key] += 1
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"[{idx}] empty or non-string value for {key}")
                    continue
                if value not in query:
                    errors.append(
                        f"[{idx}] ungrounded value for {key}: {value!r} not in query {query!r}")
                if key == "action_span":
                    stats["action_spans"][value] += 1

            # Required fields check
            for field in required_fields:
                if field not in arguments:
                    errors.append(f"[{idx}] missing required field: {field}")

        if "reasoning" not in ex or not ex.get("reasoning", "").strip():
            if answers:  # positive
                errors.append(f"[{idx}] positive without reasoning")

    return errors, warnings, stats


def cross_check(train_path: str, eval_path: str) -> list[str]:
    errors = []
    train_queries = set()
    for ex in load_jsonl(train_path):
        train_queries.add(ex.get("query", "").strip().lower())
    for ex in load_jsonl(eval_path):
        q = ex.get("query", "").strip().lower()
        if q in train_queries:
            errors.append(f"train/eval duplicate query: {ex.get('query')}")
    return errors


def main():
    ap = argparse.ArgumentParser(description="Calendar-FT dataset validator")
    ap.add_argument("--dataset", required=True, help="JSONL dataset path")
    ap.add_argument("--task", required=True,
                    choices=["calendar_write", "calendar_read", "reminder"])
    ap.add_argument("--cross-train", default=None,
                    help="train dataset path for train/eval duplicate check")
    args = ap.parse_args()

    all_errors = []
    errors, warnings, stats = validate_dataset(args.dataset, args.task)
    all_errors.extend(errors)

    if args.cross_train:
        cross_errors = cross_check(args.cross_train, args.dataset)
        all_errors.extend(cross_errors)

    # Print stats
    print(f"Dataset: {args.dataset}")
    print(f"  total: {stats['total']}")
    print(f"  positive: {stats['positive']}")
    print(f"  negative: {stats['negative']}")
    print(f"  max query chars: {stats['max_query_chars']}")
    print(f"  field coverage: {dict(stats['fields'].most_common())}")
    print(f"  action spans: {dict(stats['action_spans'].most_common())}")
    if warnings:
        print(f"  warnings: {len(warnings)}")
        for w in warnings[:5]:
            print(f"    - {w}")

    if all_errors:
        print(f"\nVALIDATION FAILED: {len(all_errors)} errors")
        for e in all_errors[:20]:
            print(f"  - {e}")
        sys.exit(1)

    print("\nVALIDATION PASSED")
    sys.exit(0)


if __name__ == "__main__":
    main()
