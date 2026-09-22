#!/usr/bin/env python3
"""Multi-Call-Benchmark (Phase D): mehrere Aktionen in einem Turn.

Misst Call-Anzahl, Reihenfolge, Tool- und Argument-Treffer, "alle Aktionen
korrekt" und Latenz. Venv-agnostisch (nur `import needle`) — läuft mit
needle 2 (FT) und needle 3 (Base/FT).

Usage:
  # needle2-FT
  NEEDLE_WEIGHTS=experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact \
    PYTHONPATH=src ../.venv-ft/bin/python experiments/ft/multi_call_bench.py --tag ft2-seed44
  # needle3-Base
  PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/multi_call_bench.py \
    --tag needle3-base --no-auto-date
  # needle3 run()-Loop statt complete()
  PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/multi_call_bench.py \
    --tag needle3-run --no-auto-date --mode run
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import statistics
import sys
import time
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FT_DIR))
sys.path.insert(0, str(FT_DIR.parents[1] / "src"))

import needle  # noqa: E402
import dataset_spec as spec  # noqa: E402

# expect: Liste von (tool, {arg: erwarteter Substring}) in gewünschter Reihenfolge
CASES = [
    {"id": "2-create", "expect": [("calendar_create", {"title": "Zahnarzt", "time": "10"}),
                                  ("calendar_create", {"title": "Meeting", "time": "14"})],
     "input": "Trag morgen 10 Uhr Zahnarzt ein und morgen 14 Uhr Meeting."},
    {"id": "3-create", "expect": [("calendar_create", {"title": "Zahnarzt"}),
                                  ("calendar_create", {"title": "Mittagessen"}),
                                  ("calendar_create", {"title": "TÜV"})],
     "input": "Mach am Freitag: 9 Uhr Zahnarzt, 12 Uhr Mittagessen mit Lisa, 16 Uhr TÜV."},
    {"id": "create+delete", "expect": [("calendar_create", {"title": "Sport"}),
                                       ("calendar_delete", {"title": "Kegelabend"})],
     "input": "Trag morgen 11 Uhr Sport ein und lösch den Termin Kegelabend."},
    {"id": "move+create", "expect": [("calendar_move", {"title": "Zahnarzt"}),
                                     ("calendar_create", {"title": "Meeting"})],
     "input": "Verschieb Zahnarzt auf Freitag 15 Uhr und trag Montag 9 Uhr Meeting ein."},
    {"id": "list+create", "expect": [("calendar_list", {}),
                                     ("calendar_create", {"title": "Yoga"})],
     "input": "Zeig mir meine Termine diese Woche und trag Freitag 10 Uhr Yoga ein."},
    {"id": "3-create-days", "expect": [("calendar_create", {"title": "Teammeeting"}),
                                       ("calendar_create", {"title": "Arzt"}),
                                       ("calendar_create", {"title": "Urlaub"})],
     "input": "Montag Teammeeting 9 Uhr, Dienstag Arzt 11 Uhr, Freitag Urlaub."},
    {"id": "2-create-en", "expect": [("calendar_create", {"title": "Dentist"}),
                                     ("calendar_create", {"title": "Meeting"})],
     "input": "Create dentist tomorrow at 10 and meeting at 14."},
    {"id": "find+create", "expect": [("calendar_find_slot", {}),
                                     ("calendar_create", {"title": "Meeting"})],
     "input": "Finde nächste Woche einen freien Slot mit Lisa und trag dort ein Meeting ein."},
    {"id": "4-create", "expect": [("calendar_create", {"title": "A"}),
                                  ("calendar_create", {"title": "B"}),
                                  ("calendar_create", {"title": "C"}),
                                  ("calendar_create", {"title": "D"})],
     "input": "Trag ein: Montag 9 Uhr A, Montag 11 Uhr B, Dienstag 9 Uhr C, Dienstag 11 Uhr D."},
    {"id": "delete+create", "expect": [("calendar_delete", {"title": "Kegelabend"}),
                                       ("calendar_create", {"title": "Kino"})],
     "input": "Lösch Kegelabend und trag Samstag 20 Uhr Kino ein."},
]


def _arg_hit(got: dict, want: dict) -> bool:
    for k, v in want.items():
        g = str(got.get(k, "")).lower()
        if str(v).lower() not in g:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--mode", choices=["complete", "run"], default="complete")
    ap.add_argument("--no-auto-date", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    tools = json.loads((FT_DIR / "tools.json").read_text())
    kwargs = {"tools": tools, "system": spec.SYSTEM_FACTS,
              "weights": os.environ.get("NEEDLE_WEIGHTS") or None}
    if "auto_date" in inspect.signature(needle.Needle.__init__).parameters:
        kwargs["auto_date"] = not args.no_auto_date
    agent = needle.Needle(**kwargs)

    cases = CASES[:args.limit] if args.limit else CASES
    rows = []
    for case in cases:
        agent.reset()
        t0 = time.perf_counter()
        try:
            if args.mode == "run":
                resp = agent.run(case["input"])
                calls = [{"name": c.get("name"), "arguments": c.get("arguments", {})}
                         for c in (resp.get("function_calls") or [])]
            else:
                resp = agent.complete(case["input"])
                calls = resp.get("function_calls") or []
        except Exception as exc:  # noqa: BLE001
            resp, calls = {"error": f"{type(exc).__name__}: {exc}"}, []
        ms = round((time.perf_counter() - t0) * 1000)
        got = [(c.get("name"), dict(c.get("arguments") or {})) for c in calls]
        want = case["expect"]
        n_ok = len(got) == len(want)
        order_ok = n_ok and all(g[0] == w[0] for g, w in zip(got, want))
        tool_ok = sorted(g[0] for g in got) == sorted(w[0] for w in want)
        args_ok = (len(got) == len(want)
                   and all(_arg_hit(g[1], w[1]) for g, w in zip(got, want)))
        rows.append({"id": case["id"], "confidence": resp.get("confidence"),
                     "n_calls": len(got), "want_calls": len(want),
                     "n_ok": n_ok, "order_ok": order_ok, "tool_ok": tool_ok,
                     "args_ok": args_ok, "all_ok": n_ok and order_ok and args_ok,
                     "ms": ms,
                     "got": [g[0] for g in got],
                     "want": [w[0] for w in want],
                     "err": resp.get("error")})
        print(f"  {'OK ' if rows[-1]['all_ok'] else 'FAIL'} {case['id']:<14} "
              f"calls {len(got)}/{len(want)} order={order_ok} args={args_ok} "
              f"{ms}ms got={[g[0] for g in got]} err={resp.get('error')}")

    n = len(rows)
    summary = {
        "tag": args.tag, "weights": kwargs["weights"], "mode": args.mode,
        "auto_date": kwargs.get("auto_date"), "n": n,
        "call_count_ok": round(sum(r["n_ok"] for r in rows) / n, 3),
        "order_ok": round(sum(r["order_ok"] for r in rows) / n, 3),
        "tool_ok": round(sum(r["tool_ok"] for r in rows) / n, 3),
        "args_ok": round(sum(r["args_ok"] for r in rows) / n, 3),
        "all_actions_correct": round(sum(r["all_ok"] for r in rows) / n, 3),
        "median_ms": round(statistics.median(r["ms"] for r in rows)),
        "rows": rows,
    }
    confs = [r["confidence"] for r in rows if r.get("confidence") is not None]
    if confs:
        summary["confidence"] = {
            "mean": round(sum(confs) / len(confs), 3),
            "share_below_0.3": round(sum(1 for c in confs if c < 0.3) / len(confs), 3)}
    out = FT_DIR / "reports" / f"multicall_{args.tag}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"\n[{args.tag}] call_count {summary['call_count_ok']} · order {summary['order_ok']}"
          f" · tool {summary['tool_ok']} · args {summary['args_ok']}"
          f" · **all_actions_correct {summary['all_actions_correct']}** · {summary['median_ms']}ms")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
