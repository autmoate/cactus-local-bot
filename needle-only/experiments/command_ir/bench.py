#!/usr/bin/env python3
"""command_ir benchmark: compiler oracle + base-model contract comparison (§7/§10).

Run (N2):
  PYTHONPATH=src experiments/command_ir/bench.py --oracle
  PYTHONPATH=src .venv/bin/python experiments/command_ir/bench.py --model N2 --contract A

Run (N3): use the N3 venv python (cactus-needle 3.0.4) with PYTHONPATH=src.

Safety (§12/§14): the model output is never written directly. interpret ->
compile ALL -> resolve ALL -> only if the WHOLE plan is valid apply it to the
fresh per-case fixture, then diff against the independently authored gold.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cases as C          # noqa: E402
import compiler as comp    # noqa: E402
import fixtures as fx      # noqa: E402
import metrics as M        # noqa: E402
from contracts import PARAMS, build_tools  # noqa: E402

TODAY = __import__("datetime").date.fromisoformat(C.TODAY)


# --------------------------------------------------------------------- oracle


def run_oracle(contract: str, tally: M.Tally) -> None:
    for case in C.CASES:
        if contract not in C.contracts_for(case):
            continue
        store, ctx, _ = fx.build_store(case["fixture"])
        ref = fx.apply_expected_ops(case["fixture"], case["expected_ops"])
        calls = case[f"gold_surface_{contract}"]
        grounded, total, _ = M.span_grounding(calls, case["query"])
        commands, errors, statuses = comp.compile_all(calls, contract, ctx, store, TODAY)

        if case["expected_status"] in ("ambiguous", "not_found"):
            want = case["expected_status"]
            got = statuses[0] if statuses else "?"
            ok = bool(errors) and got == want
            tally.add(case["family"], {
                "tool_ok": ok, "compile_ok": ok, "final_ok": ok,
                "grounded": grounded, "span_total": total,
                "ambiguous_total": 1 if want == "ambiguous" else 0,
                "ambiguous_ok": 1 if (want == "ambiguous" and ok) else 0,
                "notfound_total": 1 if want == "not_found" else 0,
                "notfound_ok": 1 if (want == "not_found" and ok) else 0,
                "failure": None if ok else {"id": case["id"], "gold": calls,
                                            "errors": errors, "statuses": statuses},
            })
            continue

        compile_ok = not errors and len(commands) == len(calls)
        final_ok = 0
        if compile_ok and case["expected_ops"]:
            fx.apply_commands(commands, store, ctx)
            final_ok = int(fx.snapshot(store, ctx) == ref)
        elif compile_ok:
            final_ok = int(fx.snapshot(store, ctx) == ref)
        tally.add(case["family"], {
            "tool_ok": compile_ok, "compile_ok": compile_ok,
            "final_ok": final_ok, "grounded": grounded, "span_total": total,
            "failure": None if final_ok else {
                "id": case["id"], "gold": calls, "errors": errors,
                "got": fx.snapshot(store, ctx), "want": ref},
        })


# ---------------------------------------------------------------------- model


def make_needle(contract: str):
    import needle
    tools = build_tools(contract)
    return needle.Needle(tools=list(tools.values()),
                         system=f"date: {C.TODAY} Thu 10:00; locale: de-DE")


def _gold_tools(calls) -> set:
    return {c.get("name") for c in (calls or [])}


def run_model(model: str, contract: str, tally: M.Tally, limit: int | None = None,
              quiet: bool = False) -> None:
    n = make_needle(contract)
    selected = C.CASES[:limit] if limit else C.CASES
    for case in selected:
        if contract not in C.contracts_for(case):
            continue
        store, ctx, _ = fx.build_store(case["fixture"])
        ref = fx.apply_expected_ops(case["fixture"], case["expected_ops"])
        t0 = time.perf_counter()
        n.reset()
        resp = n.complete(case["query"]) or {}
        calls = resp.get("function_calls") or []
        lat = (time.perf_counter() - t0) * 1000
        grounded, total, bad = M.span_grounding(calls, case["query"])
        commands, errors, statuses = comp.compile_all(calls, contract, ctx, store,
                                                      TODAY)
        tool_ok = _gold_tools(calls) == _gold_tools(case[f"gold_surface_{contract}"])

        # negative: correct when no call at all
        if case["family"] == "negative":
            applied = fx.apply_commands(commands, store, ctx)
            wrong = int(bool(applied) and fx.snapshot(store, ctx) != ref)
            tally.add("negative", {
                "refusal_total": 1, "refusal_ok": int(len(calls) == 0),
                "compile_ok": int(not errors), "wrong_mutation": wrong,
                "grounded": grounded, "span_total": total, "latency_ms": lat,
                "taxonomy": None if not calls else "MODEL_EXTRA",
                "failure": {"id": case["id"], "calls": calls} if calls else None})
            continue

        # ambiguous / not_found
        if case["expected_status"] in ("ambiguous", "not_found"):
            want = case["expected_status"]
            got = statuses[0] if statuses else "?"
            ok = bool(errors) and got == want
            taxo = None if ok else ("AMBIGUOUS" if want == "ambiguous"
                                    else "COMPILER_TARGET")
            tally.add(case["family"], {
                "tool_ok": tool_ok, "compile_ok": ok, "final_ok": int(ok),
                "grounded": grounded,
                "span_total": total, "latency_ms": lat,
                "ambiguous_total": 1 if want == "ambiguous" else 0,
                "ambiguous_ok": int(ok and want == "ambiguous"),
                "notfound_total": 1 if want == "not_found" else 0,
                "notfound_ok": int(ok and want == "not_found"),
                "taxonomy": taxo,
                "failure": None if ok else {"id": case["id"], "calls": calls,
                                            "statuses": statuses, "want": want}})
            continue

        # write / show / availability: preflight all-or-nothing
        plan_valid = not errors
        applied = []
        if plan_valid:
            applied = fx.apply_commands(commands, store, ctx)
        final_ok = int(plan_valid and fx.snapshot(store, ctx) == ref)
        expected_ops = len(case["expected_ops"])
        pred_ops = len(applied)
        all_intents = int(final_ok and pred_ops == expected_ops)
        silent = int(expected_ops > pred_ops)
        wrong = int(bool(applied) and fx.snapshot(store, ctx) != ref)

        taxo = None
        if not tool_ok and case["family"] in ("add", "change", "remove"):
            taxo = "MODEL_TOOL"
        elif total and grounded < total:
            taxo = "MODEL_SPAN"
        elif not plan_valid:
            taxo = errors[0]["code"] if errors else "DOMAIN"
        elif silent:
            taxo = "MODEL_OMISSION"
        elif pred_ops > expected_ops:
            taxo = "MODEL_EXTRA"
        elif not final_ok:
            taxo = "EXECUTION"

        tally.add(case["family"], {
            "tool_ok": tool_ok, "compile_ok": int(plan_valid),
            "final_ok": final_ok, "grounded": grounded, "span_total": total,
            "write_cases": expected_ops, "wrong_mutation": wrong,
            "ops_expected": expected_ops, "ops_pred": pred_ops,
            "all_intents": all_intents, "silent_omission": silent,
            "latency_ms": lat, "taxonomy": taxo,
            "failure": None if final_ok else {
                "id": case["id"], "calls": calls, "errors": errors,
                "got": fx.snapshot(store, ctx), "want": ref}})


# ------------------------------------------------- old production N2-FT baseline


def run_old_baseline(tally: M.Tally, weights: str, limit: int | None = None) -> None:
    """Optional §10 baseline: the OLD normalized 5-tool production contract +
    N2-FT on the SAME user goals -> final DB state only."""
    import inspect
    import needle
    from local_calendar.agent import build_tools as old_tools, execute_call
    probe, _, _ = fx.build_store({"kind": "private", "actor": "Ada", "events": []})
    tools = old_tools(probe)
    kwargs = {"tools": list(tools.values()), "system": f"date: {C.TODAY} Thu 10:00"}
    if weights:
        kwargs["weights"] = weights
    n = needle.Needle(**kwargs)
    selected = C.CASES[:limit] if limit else C.CASES
    for case in selected:
        if case["family"] in ("show", "availability"):
            continue  # reads have no final-state delta
        store, ctx, _ = fx.build_store(case["fixture"])
        ref = fx.apply_expected_ops(case["fixture"], case["expected_ops"])
        n.reset()
        calls = (n.complete(case["query"]) or {}).get("function_calls") or []
        writes = 0
        for call in calls:
            out = execute_call(store, call.get("name"), call.get("arguments") or {},
                               case["query"], commit=True, scope=ctx)
            if out.get("ok") and call.get("name") in ("calendar_create",
                                                      "calendar_move",
                                                      "calendar_delete"):
                writes += 1
        snap = fx.snapshot(store, ctx)
        final_ok = int(snap == ref)
        if case["family"] == "negative":
            tally.add("old_negative", {"refusal_total": 1,
                                       "refusal_ok": int(len(calls) == 0),
                                       "final_ok": int(len(calls) == 0),
                                       "wrong_mutation": int(writes > 0)})
        else:
            tally.add(f"old_{case['family']}", {
                "final_ok": final_ok, "write_cases": len(case["expected_ops"]),
                "wrong_mutation": int(writes > 0 and snap != ref),
                "failure": None if final_ok else
                {"id": case["id"], "calls": calls, "got": snap, "want": ref}})


# ----------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracle", action="store_true")
    ap.add_argument("--model", choices=["N2", "N3"])
    ap.add_argument("--contract", choices=["A", "B"])
    ap.add_argument("--old-baseline", action="store_true")
    ap.add_argument("--weights", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    tally = M.Tally()
    label = "oracle"
    if args.oracle:
        for contract in ("A", "B"):
            t = M.Tally()
            run_oracle(contract, t)
            rep = t.report()
            print(f"\n=== ORACLE contract {contract} ===")
            for fam, m in rep["families"].items():
                print(f"  {fam:14} n={m['n']:3} cr_ok={m['compile_ok']:.2f} "
                      f"final={m['final_ok']:.2f}")
            if args.out:
                Path(args.out).write_text(json.dumps(rep, indent=2))
            label = f"oracle_{contract}"
        return 0

    if args.old_baseline:
        print(f"  old production N2-FT baseline weights={args.weights}")
        run_old_baseline(tally, args.weights, limit=args.limit)
        rep = tally.report()
        print(json.dumps(rep["families"], indent=2))
        print("taxonomy:", rep["taxonomy"])
        if args.out:
            Path(args.out).write_text(json.dumps(rep, indent=2))
        return 0

    if not args.model or not args.contract:
        ap.error("--model and --contract required unless --oracle")
    print(f"  model={args.model} contract={args.contract} "
          f"cases={len(C.CASES) if not args.limit else args.limit}")
    run_model(args.model, args.contract, tally, limit=args.limit)
    rep = tally.report()
    print(json.dumps(rep["families"], indent=2))
    print("taxonomy:", rep["taxonomy"])
    if args.out:
        Path(args.out).write_text(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
