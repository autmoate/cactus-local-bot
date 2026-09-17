#!/usr/bin/env python3
"""Gold-supervision A/B (pre-FT): full/default-filled vs sparse/evidenced-only.

Question (user review, Stopper 1): which supervision is right for Needle —
a default-filled full-call gold (every schema string field present, ints/
enums carrying schema defaults) or evidenced-only (omit every argument
without a query span)?

Method: Base-Needle outputs are frozen in reports/base_*_rows.jsonl (same
model call, no re-inference). Each frozen output is scored against BOTH
gold conventions:
  A (full)    got == default-filled gold
  B (sparse)  got == evidenced-only gold (the shipped dataset convention)
  B_norm      strip_defaults(got) == sparse gold (tolerates the model still
              emitting defaults while the target is sparse)

Reproducibility (user review Sep 17): the dataset ships sparse gold, so the
historical full gold comes from TWO sources, both auditable:
  1. the frozen rows-cache itself — `want` holds the full gold the model
     run was originally scored against (primary), and
  2. a deterministic reconstruction from sparse + production schema
     (every schema string field present, '' when unevidenced; ints/enums
     carry defaults duration_min=60, days=7, horizon=week otherwise),
     used as fallback and as a consistency check against (1).

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

DEFAULT_FILL = {"horizon": "week", "duration_min": 60, "days": 7}
TRIGGER = {
    "horizon": r"heute|diese woche|nächste woche|diesen monat|this week|today|this month|kommende woche|next week",
    "duration_min": r"\d+\s*(minute|min\b|stunde|std\b|hour)",
    "days": r"\bdays?\b|tage",
}


def full_from_sparse(sparse: dict, query: str, tool: str,
                     schemas: dict) -> dict:
    """Deterministic reconstruction of the historical default-filled gold."""
    props = schemas[tool]["parameters"]["properties"]
    out = {}
    for name, prop in props.items():
        if name in sparse and sparse[name] not in ("", None):
            out[name] = sparse[name]
        elif prop.get("type") == "integer":
            out[name] = DEFAULT_FILL.get(name, 0)
        elif "enum" in prop:
            out[name] = DEFAULT_FILL.get(name, prop["enum"][0])
        else:
            out[name] = ""
    return out


def strip_defaults(args: dict, query: str) -> dict:
    return {k: v for k, v in args.items()
            if not (v in ("", None)
                    or (k in DEFAULT_FILL and v == DEFAULT_FILL[k]
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


def score(rows: list[dict], labels: dict, schemas: dict, kind: str) -> dict:
    n = len(rows)
    exact_full = exact_sparse = exact_norm = sem = 0
    recon_mismatch = 0
    for r in rows:
        sp, q, tool = labels.get(r["id"], (None, r.get("query", ""), "none"))
        if not sp and tool == "none":
            continue  # negative rows carry no args to score
        got = r["got"]
        full_cached = r.get("want") or {}
        # prefer the historically conserved full gold (want in the frozen
        # rows cache); fall back to deterministic sparse+schema reconstruction
        full = full_cached or full_from_sparse(sp, q, tool, TOOL_SCHEMAS)
        if full_cached and full_cached != full_from_sparse(
                sp, q, tool, TOOL_SCHEMAS):
            recon_mismatch += 1
        if got == full:
            exact_full += 1
        if got == sp:
            exact_sparse += 1
        if strip_defaults(got, q) == sp:
            exact_norm += 1
        if all(_sem(f, got.get(f), v) for f, v in sp.items()) \
                and not [k for k, v in got.items() if v and k not in sp]:
            sem += 1
    return {"kind": kind, "n": n,
            "exact_full": round(exact_full / n, 3),
            "exact_sparse": round(exact_sparse / n, 3),
            "exact_sparse_norm": round(exact_norm / n, 3),
            "args_ok_semantic": round(sem / n, 3),
            "recon_mismatch": recon_mismatch}


def main() -> None:
    global TOOL_SCHEMAS
    TOOL_SCHEMAS = {s["name"]: s for s in
                    json.load(open(FT_DIR / "tools.json", encoding="utf-8"))}
    labels = {}
    for src in (FT_DIR / "data" / "test.jsonl",
                FT_DIR / "challenge" / "challenge_traces.jsonl"):
        for line in open(src, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("answers"):
                labels[r["id"]] = (r["answers"][0]["arguments"],
                                   r.get("query", r.get("input", "")),
                                   r["meta"]["tool"] if "meta" in r
                                   else r["tool"])
            elif "args" in r:
                labels[r["id"]] = (r["args"], r.get("input", ""), r["tool"])
    out = {}
    for name, path in (("test", "base_test_rows.jsonl"),
                       ("challenge", "base_challenge_rows.jsonl")):
        rows = [json.loads(l) for l in
                open(FT_DIR / "reports" / path, encoding="utf-8")]
        out[name] = score(rows, labels, TOOL_SCHEMAS, name)
    (FT_DIR / "reports" / "gold_ab_report.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for name, rep in out.items():
        print(f"== {name} (n={rep['n']})  recon_mismatch={rep['recon_mismatch']}")
        print(f"   exact_full={rep['exact_full']}  exact_sparse={rep['exact_sparse']}"
              f"  exact_sparse_norm={rep['exact_sparse_norm']}"
              f"  args_ok_sem={rep['args_ok_semantic']}")


if __name__ == "__main__":
    main()
