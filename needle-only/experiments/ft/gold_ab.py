#!/usr/bin/env python3
"""Gold-supervision A/B (pre-FT): full/default-filled vs sparse/evidenced-only.

Question (user review, Stopper 1): which supervision is right for Needle —
the current convention (every schema string field present, ints/enums carry
defaults) or evidenced-only (omit every argument without a query span)?

Method: Base-Needle outputs are already frozen in reports/base_*_rows.jsonl
(same model call, no re-inference). Each frozen output is scored against
BOTH gold conventions:
  A (full)   got == full gold (current convention)
  B (sparse) got == sparse gold (default/empty fields dropped)
  B_norm     strip_defaults(got) == sparse gold (tolerates the model still
             emitting defaults while the target is sparse)
plus per-field missing/wrong/default-extra and the semantic args_ok.

Needle's own finetune generator (_GEN_TEMPLATE in needle/model/finetune.py)
prescribes: "arguments must match them exactly and contain only values
evidenced in the query" — sparse. This script measures whether Base Needle
behaves the same way in production.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FT_DIR))
sys.path.insert(0, str(FT_DIR.parent))

from local_calendar import calendar as cal  # noqa: E402

DEFAULTS = {"horizon": "week", "duration_min": 60, "days": 7}
TRIGGER = {
    "horizon": r"heute|diese woche|nächste woche|diesen monat|this week|today|this month|kommende woche",
    "duration_min": r"\d+\s*(minute|min\b|stunde|std\b|hour)",
    "days": r"\bdays?\b|tage",
}
FIELDS = ("date", "until", "time", "end_time", "title", "persons", "person",
          "horizon", "duration_min", "days", "participants")


def sparse_gold(full: dict, query: str) -> dict:
    out = {}
    for k, v in full.items():
        if v in ("", None):
            continue
        if k in DEFAULTS and v == DEFAULTS[k] and not re.search(
                TRIGGER.get(k, r"(?!)"), query, re.IGNORECASE):
            continue
        out[k] = v
    return out


def strip_defaults(args: dict, query: str) -> dict:
    return {k: v for k, v in args.items()
            if not (v in ("", None)
                    or (k in DEFAULTS and v == DEFAULTS[k]
                        and not re.search(TRIGGER.get(k, r"(?!)"), query,
                                          re.IGNORECASE)))}


def _sem(field: str, got, want) -> bool:
    g, w = str(got or ""), str(want or "")
    if field in ("date", "until"):
        rg = cal.resolve_date(g, cal.now().date(), roll=False)
        rw = cal.resolve_date(w, cal.now().date(), roll=False)
        return (rg == rw) if (rg and rw) else g.lower() == w.lower()
    if field in ("time", "end_time"):
        tg, tw = cal.resolve_time(g), cal.resolve_time(w)
        return (tg == tw) if (tg and tw) else g.lower() == w.lower()
    if field in ("persons", "participants"):
        pg = {p.lower() for p in cal.parse_persons(g)}
        pw = {p.lower() for p in cal.parse_persons(w)}
        return pw <= pg
    if field == "title":
        return w.lower() in g.lower() or g.lower() in w.lower()
    return g.strip().lower() == w.strip().lower()


def score(rows: list[dict], labels: dict, kind: str) -> dict:
    n = len(rows)
    exact_full = exact_sparse = exact_norm = sem = 0
    field = {f: Counter() for f in FIELDS}
    default_extra = Counter()
    for r in rows:
        if r["id"] not in labels:
            continue
        full, q = labels[r["id"]]
        got = r["got"]
        sp = sparse_gold(full, q)
        if got == full:
            exact_full += 1
        if got == sp:
            exact_sparse += 1
        if strip_defaults(got, q) == sp:
            exact_norm += 1
        if all(_sem(f, got.get(f), v) for f, v in sp.items()) \
                and not [k for k, v in got.items()
                         if v and k not in sp]:
            sem += 1
        for f, want in full.items():
            if want in ("", None):
                if got.get(f) not in ("", None):
                    field[f]["hallucinated"] += 1
                continue
            field[f]["expected"] += 1
            g = got.get(f)
            if g in ("", None):
                field[f]["missing"] += 1
                if f in DEFAULTS:
                    field[f]["missing_" + ("in_gold" if f in sp
                                           else "default")] += 1
            elif _sem(f, g, want):
                field[f]["correct"] += 1
            else:
                field[f]["wrong"] += 1
        for f in ("horizon", "duration_min", "days"):
            if f not in sp and got.get(f) not in ("", None):
                default_extra[f] += 1
    return {"kind": kind, "n": n,
            "exact_full": round(exact_full / n, 3),
            "exact_sparse": round(exact_sparse / n, 3),
            "exact_sparse_norm": round(exact_norm / n, 3),
            "args_ok_semantic": round(sem / n, 3),
            "per_field": {f: dict(c) for f, c in field.items() if c},
            "default_extra": dict(default_extra)}


def main() -> None:
    labels = {}
    for src in (FT_DIR / "data" / "test.jsonl",
                FT_DIR / "challenge" / "challenge_traces.jsonl"):
        for line in open(src, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if "answers" in r and r["answers"]:
                labels[r["id"]] = (r["answers"][0]["arguments"],
                                   r.get("query", r.get("input", "")))
            elif "args" in r:
                labels[r["id"]] = (r["args"], r.get("input", ""))
    out = {}
    for name, path in (("test", "base_test_rows.jsonl"),
                       ("challenge", "base_challenge_rows.jsonl")):
        rows = [json.loads(l) for l in
                open(FT_DIR / "reports" / path, encoding="utf-8")]
        out[name] = score(rows, labels, name)
    (FT_DIR / "reports" / "gold_ab_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, rep in out.items():
        print(f"== {name} (n={rep['n']})")
        print(f"   exact_full={rep['exact_full']}  exact_sparse={rep['exact_sparse']}"
              f"  exact_sparse_norm={rep['exact_sparse_norm']}"
              f"  args_ok_sem={rep['args_ok_semantic']}")
        print("   default_extra:", rep["default_extra"])
        for f in ("date", "until", "time", "horizon", "duration_min", "days"):
            if f in rep["per_field"]:
                print(f"   {f:<12}", rep["per_field"][f])


if __name__ == "__main__":
    main()
