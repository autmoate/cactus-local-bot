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
NEEDLE_ONLY = FT_DIR.parents[1]
sys.path.insert(0, str(FT_DIR))
sys.path.insert(0, str(NEEDLE_ONLY / "src"))  # local_calendar (Root-venv)

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


def run_set(name: str, items: list[dict], repeats: int = 1,
            dump_path: Path | None = None, weights: str | None = None) -> dict:
    all_rows = []
    for _ in range(repeats):
        tools = json.load(open(FT_DIR / "tools.json", encoding="utf-8"))
        agent = needle.Needle(tools=tools, system=spec.SYSTEM_FACTS,
                              weights=weights)
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
            # args_ok (production-tolerant): every gold field present and
            # semantically right; EXTRA fields are tolerated (the model may
            # emit handler-identical defaults like horizon=week/duration 60)
            missing = [k for k, v in item["args"].items()
                       if v and not got_args.get(k)]
            extra_bad = [k for k, v in got_args.items()
                         if v and k in item["args"] and item["args"][k]
                         and not _sem_equal(k, v, item["args"][k])]
            args_ok = tool_ok and not missing and not extra_bad
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
    if dump_path is not None:
        with open(dump_path, "w", encoding="utf-8") as fh:
            for r in all_rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return report


def recompute_from_rows(rows_path: Path, labels: dict, name: str) -> dict:
    """Aggregate a report from frozen model outputs (no re-inference)."""
    import statistics
    rows = [json.loads(l) for l in open(rows_path, encoding="utf-8")]
    for r in rows:
        full, q = labels.get(r["id"], ({}, r.get("query", "")))
        r["want"] = full
        got = r["got"]
        missing = [k for k, v in full.items() if v and not got.get(k)]
        extra_bad = [k for k, v in got.items()
                     if v and k in full and full[k]
                     and not _sem_equal(k, v, full[k])]
        args_ok = r["tool_ok"] and not missing and not extra_bad
        exact_ok = r["tool_ok"] and got == full
        r["args_ok"], r["exact_args_ok"] = args_ok, exact_ok
    n = len(rows)
    report = {"set": name, "n": n, "repeats": 1,
              "tool_ok": round(sum(r["tool_ok"] for r in rows) / n, 3),
              "args_ok": round(sum(r["args_ok"] for r in rows) / n, 3),
              "exact_args_ok":
                  round(sum(r["exact_args_ok"] for r in rows) / n, 3),
              "refusals": round(sum(r["refusal"] for r in rows) / n, 3),
              "median_ms": round(statistics.median(r["ms"] for r in rows))}
    field = {f: {"expected": 0, "correct": 0} for f in FIELDS}
    for r in rows:
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
    ap.add_argument("--weights", default=None,
                    help="optionales .cact (FT-Modell); ohne = Base-Needle")
    ap.add_argument("--tag", default="base",
                    help="Report-Prefix (base = frozen, sonst z.B. Run-Name)")
    ap.add_argument("--from-cache", action="store_true",
                    help="re-score frozen base_*_rows.jsonl statt "
                         "re-inferencing (nur sinnvoll mit --tag base)")
    args = ap.parse_args()

    reports = FT_DIR / "reports"
    reports.mkdir(exist_ok=True)

    if args.from_cache:
        labels = {}
        for src in (FT_DIR / "data" / "test.jsonl",
                    FT_DIR / "challenge" / "challenge_traces.jsonl"):
            for line in open(src, encoding="utf-8"):
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("answers"):
                    labels[r["id"]] = (r["answers"][0]["arguments"],
                                       r.get("query", r.get("input", "")))
                elif "args" in r:
                    labels[r["id"]] = (r["args"], r.get("input", ""))
        test_report = recompute_from_rows(
            reports / "base_test_rows.jsonl", labels, "synthetic-test")
        challenge_report = recompute_from_rows(
            reports / "base_challenge_rows.jsonl", labels, "challenge")
    else:
        test_items = load_items(args.test)
        ch_rows = [json.loads(l) for l in open(args.challenge, encoding="utf-8")
                   if l.strip()]
        ch_items = [{"id": c["id"], "input": c["input"], "tool": c["tool"],
                     "args": c["args"]} for c in ch_rows]
        if args.limit:
            test_items = test_items[:args.limit]
            ch_items = ch_items[:args.limit]
        print(f"[{args.tag}] test n={len(test_items)} · challenge n={len(ch_items)} "
              f"· weights={args.weights or 'base'}")
        challenge_report = run_set(
            "challenge", ch_items, args.repeats,
            dump_path=reports / f"{args.tag}_challenge_rows.jsonl",
            weights=args.weights)
        test_report = run_set(
            "synthetic-test", test_items, args.repeats,
            dump_path=reports / f"{args.tag}_test_rows.jsonl",
            weights=args.weights)

    for name, rep, frozen in (("test", test_report, "base_test_report.json"),
                              ("challenge", challenge_report,
                               "base_challenge_report.json")):
        out = reports / (frozen if args.tag == "base"
                         else f"{args.tag}_{name}_report.json")
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        print(f"  -> {out.name}: {json.dumps(rep, ensure_ascii=False)}")

    # Erfolgskriterien §22 gegen die Frozen Baseline (nur mit --weights)
    if args.weights:
        base = json.loads((reports / "base_test_report.json").read_text())
        # "Refusals" mischt korrekte Negativ-Refusals mit falschen auf Positiven.
        # Die Zerlegung macht den §22-Vergleich ehrlich: entscheidend ist, dass
        # das FT-Modell gültige Anfragen NICHT öfter verweigert als Base.
        neg_ids = set()
        for line in open(FT_DIR / "data" / "test.jsonl", encoding="utf-8"):
            r = json.loads(line)
            if r["meta"]["tool"] == "none":
                neg_ids.add(r["id"])

        def false_refusals(rows_path: Path) -> tuple[int, int, int]:
            n_pos = n_false = n_correct = 0
            for line in open(rows_path, encoding="utf-8"):
                r = json.loads(line)
                if r["id"] in neg_ids:
                    n_correct += bool(r.get("refusal"))
                else:
                    n_pos += 1
                    n_false += bool(r.get("refusal"))
            return n_false, n_pos, n_correct

        ft_false, ft_pos, ft_correct = false_refusals(
            reports / f"{args.tag}_test_rows.jsonl")
        b_false, b_pos, b_correct = false_refusals(
            reports / "base_test_rows.jsonl")
        checks = {
            "exact_args_ok >= base+0.20": (test_report["exact_args_ok"],
                                           round(base["exact_args_ok"] + 0.20, 3)),
            "args_ok >= base+0.15": (test_report["args_ok"],
                                     round(base["args_ok"] + 0.15, 3)),
            "refusals <= base": (test_report["refusals"], base["refusals"]),
            "false_refusals_auf_positiven <= base": (ft_false, b_false),
        }
        print("\n§22-Kriterien (Level 1, synthetic test):")
        ok = True
        for label, (got, want) in checks.items():
            passed = got >= want if ">=" in label else got <= want
            ok = ok and passed
            print(f"  {'PASS' if passed else 'FAIL'}  {label}: {got} (Ziel {want})")
        print(f"  Kontext: falsche Refusals {ft_false}/{ft_pos} (Base {b_false}/{b_pos}) · "
              f"korrekte Negativ-Refusals {ft_correct} (Base {b_correct})")
        print(f"  §22 gesamt: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
