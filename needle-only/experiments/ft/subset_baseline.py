#!/usr/bin/env python3
"""Same-subset baselines: split the all-5-tool frozen results by Read/Write.

Why (user review): comparing a Narrow (read-only/write-only) toolset score
against the ALL-5 global exact (over 1800 cases) is not apples-to-apples. This
recomputes the all-5 model on exactly the same read/write subsets the toolset
ablation uses, so the comparison is fair.

Input: reports/<tag>_test_rows.jsonl (frozen all-5 results) + data/test.jsonl
(gold tool per id). No model required.

Usage:
  PYTHONPATH=src uv run python experiments/ft/subset_baseline.py \
      --rows reports/sa-r16-lr1e-4-e8-seed44_test_rows.jsonl --tag n2ft-all5
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
READ = ("calendar_list", "calendar_find_slot")
WRITE = ("calendar_create", "calendar_move", "calendar_delete")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    gold = {}
    for line in open(HERE / "data" / "test.jsonl", encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            gold[r["id"]] = r["meta"]["tool"]
    rows_path = Path(args.rows)
    if not rows_path.exists():
        rows_path = HERE / "reports" / rows_path.name
    rows = [json.loads(l) for l in open(rows_path, encoding="utf-8") if l.strip()]

    def subset(sel):
        r = [x for x in rows if sel(gold.get(x["id"], "none"))]
        n = len(r) or 1
        return {"n": len(r),
                "tool_ok": round(sum(x["tool_ok"] for x in r) / n, 3),
                "args_ok": round(sum(x["args_ok"] for x in r) / n, 3),
                "exact": round(sum(x["exact_args_ok"] for x in r) / n, 3)}

    out = {"tag": args.tag, "rows": args.rows,
           "read": subset(lambda t: t in READ),
           "write": subset(lambda t: t in WRITE),
           "none": subset(lambda t: t == "none"),
           "all": subset(lambda t: True)}
    (HERE / "reports" / f"subset_baseline_{args.tag}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    for part in ("read", "write", "none", "all"):
        m = out[part]
        print(f"{args.tag:<18} {part:<6} n={m['n']:<5} tool {m['tool_ok']} "
              f"args {m['args_ok']} exact {m['exact']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
