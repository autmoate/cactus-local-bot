#!/usr/bin/env python3
"""Frozen Base-Needle baseline against the synthetic test split + the real
challenge set (plan §17/§10). Saves reports/base_test_report.json and
base_challenge_report.json.

Metrics: tool_ok, args_ok (semantic), exact_args_ok (FT gold convention),
refusals, per-field accuracy, latency. final_db_ok stays with the existing
E2E harness (Level 2, plan §23).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import needle

FT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FT_DIR))
sys.path.insert(0, str(FT_DIR.parent))

import dataset_spec as spec  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402

FIELDS = ("date", "until", "time", "end_time", "title", "persons",
          "person", "horizon", "duration_min", "days", "participants")


def _sem_equal(field: str, got, want) -> bool:
    g, w = str(got or ""), str(want or "")
    if field in ("date", "until"):
        if not w:
            return not g
        rg = cal.resolve_date(g, cal.now().date(), roll=False)
        rw = cal.resolve_date(w, cal.now().date(), roll=False)
        return rg == rw if (rg and rw) else g.lower() == w.lower()
    if field in ("time", "end_time"):
        if not w:
            return not g
        tg, tw = cal.resolve_time(g), cal.resolve_time(w)
        return tg == tw if (tg and tw) else g.lower() == w.lower()
    if field == "persons":
        pg = {p.lower() for p in cal.parse_persons(g)}
        pw = {p.lower() for p in cal.parse_persons(w)}
        return pw <= pg
    if field == "title":
        return w.lower() in g.lower() or g.lower() in w.lower()
    return g.strip().lower() == w.strip().lower()


def run_set(name: str, items: list[dict], repeats: int = 1) -> dict:
    all_rows = []
    for _ in range(repeats):
        tools = json.load(open(FT_DIR / "tools.json", encoding="utf-8"))
        agent = needle.Needle(tools=tools, system=spec.SYSTEM_FACTS)
        for item in items:
            agent.reset()
            t0 = time.perf_counter()
            try:
                resp = agent.complete(item["input"])
            except Exception as exc:
                resp = {"function_calls": []}
            ms = round((time.perf_counter() - t0) * 1000)
            calls = resp.get("function_calls") or []
            got = calls[0] if calls else {}
            got_name = got.get("name")
            got_args = dict(got.get("arguments") or {})
            if item["tool"] == "none":
                tool_ok = not got_name
            else:
                tool_ok = got_name == item["tool"]
            missing = [k for k, v in item["args"].items()
                       if v and not got_args.get(k)]
            extra_bad = [k for k, v in got_args.items()
                         if v and k in item["args"] and item["args"][k]
                         and not _sem_equal(k, v, item["args"][k])]
            wrong_empty = [k for k, v in got_args.items()
                           if v and (k not in item["args"]
                                     or not item["args"][k])]
            args_ok = tool_ok and not missing and not extra_bad and \
                not wrong_empty
            exact_ok = tool_ok and got_args == item["args"]
            all_rows.append({"id": item["id"], "tool_ok": tool_ok,
                             "args_ok": args_ok, "exact_args_ok": exact_ok,
                             "refusal": not got_name, "ms": ms,
                             "got": got_args, "want": item["args"]})
    n = len(all_rows)
    report = {"set": name, "n": n, "repeats": repeats,
              "tool_ok": round(sum(r["tool_ok"] for r in all_rows) / n, 3),
              "args_ok": round(sum(r["args_ok"] for r in all_rows) / n, 3),
              "exact_args_ok":
                  round(sum(r["exact_args_ok"] for r in all_rows) / n, 3),
              "refusals": round(sum(r["refusal"] for r in all_rows) / n, 3),
              "median_ms": round(statistics.median(r["ms"] for r in all_rows))}
    field = {f: {"expected": 0, "correct": 0} for f in FIELDS}
    for r in all_rows:
        for f, w in r["want"].items():
            if w:
                field[f]["expected"] += 1
                if _sem_equal(f, r["got"].get(f), w):
                    field[f]["correct"] += 1
    report["per_field"] = {f: (round(v["correct"] / v["expected"], 3)
                               if v["expected"] else None)
                           for f, v in field.items()}
    return report


def load_items(path: str) -> list[dict]:
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    return [{"id": r["id"], "input": r["query"], "tool": r["meta"]["tool"],
             "args": (r["answers"][0]["arguments"] if r.get("answers")
                      else {})} for r in rows]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", default=str(FT_DIR / "data" / "test.jsonl"))
    ap.add_argument("--challenge", default=str(
        FT_DIR / "challenge" / "challenge_traces.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    items = load_items(args.test)
    if args.limit:
        items = items[:args.limit]
    test_report = run_set("synthetic-test", items, args.repeats)

    ch_rows = [json.loads(l) for l in open(args.challenge, encoding="utf-8")
               if l.strip()]
    ch_items = [{"id": c["id"], "input": c["input"], "tool": c["tool"],
                 "args": c["args"]} for c in ch_rows]
    challenge_report = run_set("challenge", ch_items, args.repeats)

    reports = FT_DIR / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "base_test_report.json").write_text(
        json.dumps(test_report, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (reports / "base_challenge_report.json").write_text(
        json.dumps(challenge_report, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(json.dumps(test_report, ensure_ascii=False))
    print(json.dumps(challenge_report, ensure_ascii=False))


if __name__ == "__main__":
    main()
