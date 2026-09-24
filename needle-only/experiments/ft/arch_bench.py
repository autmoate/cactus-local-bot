#!/usr/bin/env python3
"""Architektur-Bakeoff: wie viel Gemma-Compute sparen wir bei gleicher Qualität?

Pipelines (unverändert daneben, nichts Bestehendes umgebaut):
  P0 hybrid    Gemma → N2-FT → Python            (Referenz; braucht laufendes Gemma)
  P1 direct    N3 → Python                        (maximale Rationalisierung)
  P2 fallback  N3 → Python → (nur bei Bedarf) Gemma → N2-FT → Python

Gemessen werden Business-Metriken, keine Submetriken:
  final_goal_ok · autonomous_ok · escalated (Gemma-Bedarf) · wrong_writes ·
  missed_actions · Latenz p50/p95 — plus Familien-Breakdown.

Deterministische Eskalationssignale (P2, KEIN semantischer Router): leerer Call,
unbekanntes Tool, leerer Titel bei Write, nicht eindeutig auflösbare Entity,
Create ohne Datum/Zeit, Ausführungsfehler.

Usage:
  NEEDLE_WEIGHTS=<n3.cact> PYTHONPATH=src .venv-ft3/bin/python \
      experiments/ft/arch_bench.py --pipeline direct --tag n3-v4-e5
  NEEDLE_WEIGHTS=<n3.cact> PYTHONPATH=src .venv-ft3/bin/python \
      experiments/ft/arch_bench.py --pipeline fallback --tag n3-v4-e5
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import statistics as st
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import needle  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import build_tools, Gemma  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402
from arch_cases import build_cases, _iso  # noqa: E402
import eval_mutations as mut  # noqa: E402

WRITES = ("calendar_create", "calendar_move", "calendar_delete")
READS = ("calendar_list", "calendar_find_slot")


def _wrap(tools, trace):
    out = {}
    for name, fn in tools.items():
        def make(fn, name):
            def call(**args):
                try:
                    res = fn(**args)
                except Exception as exc:  # noqa: BLE001
                    res = f"{type(exc).__name__}: {exc}"
                # keep enough result text to validate read answers (titles+dates)
                trace.append({"tool": name, "args": args, "result": str(res)[:600]})
                return res
            return call
        w = make(fn, name)
        if hasattr(fn, "_needle_tool"):
            w._needle_tool = fn._needle_tool
        out[name] = w
    return out


def _events(store):
    return store.events_between(cal.now() - timedelta(days=3), cal.now() + timedelta(days=70))


def _specs(present):
    """(title, day, hm, persons) with symbolic days resolved to ISO."""
    return [(t, _iso(d) if d else "", hm, p) for (t, d, hm, p) in present]


def _failed(attempt):
    r = attempt.get("result", "") or ""
    return "❌" in r or "Error" in r or "Fehler" in r


def _check(expect, before: dict, after: dict, trace) -> dict:
    """Return {ok, missed, correct_mutations, wrong_mutations, why}.

    no_writes/ambiguous now also validate the READ answer content — 'no write
    happened' alone is not success. Write goals classify real DB mutations
    (diff), so a create-instead-of-delete is a wrong_mutation even when the
    call count matches (user review Sep 24).
    """
    diff = mut.db_diff(before, after)
    writes = [t for t in trace if t["tool"] in WRITES]
    present = expect.get("present", [])
    absent = expect.get("absent", [])
    if expect.get("ambiguous") or expect.get("no_writes"):
        if writes:
            return {"ok": False, "missed": len(present), "correct_mutations": 0,
                    "wrong_mutations": mut.mutation_count(diff),
                    "why": "write obwohl verboten/ungeklärt"}
        if present and not mut.read_answer_ok(trace, _specs(present)):
            return {"ok": False, "missed": len(present), "correct_mutations": 0,
                    "wrong_mutations": 0, "why": "read-antwort fehlt/falsch"}
        reason = ("ok (Rückfrage)" if expect.get("ambiguous")
                  else ("ok (read)" if present else "ok (kein Write)"))
        return {"ok": True, "missed": 0, "correct_mutations": 0,
                "wrong_mutations": 0, "why": reason}
    missed = 0
    present_specs = _specs(present)
    for spec in present_specs:
        if not any(mut.event_matches(e, *spec) for e in after.values()):
            missed += 1
    for title in absent:
        was_there = any(title.lower() in e["title"].lower() for e in before.values())
        now_gone = not any(title.lower() in e["title"].lower() for e in after.values())
        if not (was_there and now_gone):
            missed += 1
    correct_mut, wrong_mut = mut.classify(diff, present_specs, absent)
    ok = missed == 0 and wrong_mut == 0
    return {"ok": ok, "missed": missed, "correct_mutations": correct_mut,
            "wrong_mutations": wrong_mut,
            "why": "ok" if ok else f"missed={missed} wrong_mut={wrong_mut}"}


def _inspect(calls, tools, store):
    """P2: deterministisch prüfen, ob die vorgeschlagenen Calls selbst ausführbar sind."""
    if not calls:
        # Refusal/leer = SAFE, kein Write, kein Gemma nötig. (Der Fall wird nur
        # dann zum "missed action", wenn der Goal-Typ einen Write verlangte —
        # genau die Silent-Omission-Klasse, die P2 nicht detektieren kann.)
        return True, "no_calls_refusal"
    for c in calls:
        name = c["name"]
        args = c.get("arguments") or {}
        if name not in tools:
            return False, f"unknown_tool:{name}"
        title = str(args.get("title", "")).strip()
        if name in ("calendar_create", "calendar_move", "calendar_delete") and not title:
            return False, "empty_title"
        if name in ("calendar_move", "calendar_delete"):
            hits = [e for e in _events(store) if title.lower() in e.title.lower()]
            if len(hits) != 1:
                return False, f"unresolved_entity({len(hits)})"
        if name == "calendar_create" and not args.get("date") and not args.get("time"):
            return False, "no_date_time"
    return True, "executable"


def _canonical(gemma, goal: str) -> tuple[str, bool]:
    """Gemma canonicalize for P0/P-c. Returns (canonical, used). OFF_TOPIC -> ('', True)."""
    if gemma is None:
        return goal, False
    try:
        c = gemma.canonicalize(goal).strip()
    except Exception:  # noqa: BLE001
        return "", True
    if "OFF_TOPIC" in c.upper():
        return "", True
    return c, True


def run_case(case, pipeline, weights, gemma=None) -> dict:
    td = Path(tempfile.mkdtemp())
    store = CalendarStore(td / "arch.db")
    raw = build_tools(store)
    trace: list[dict] = []
    tools = _wrap(raw, trace)
    for (title, day, hm, p) in case["fixture"]:
        raw["calendar_create"](title=title, date=day, time=hm, participants=p)
    before = mut.snapshot(store)
    t0 = time.perf_counter()
    escalated, esc_reason, gemma_used = False, "", False
    agent = needle.Needle(tools=list(tools.values()),
                          system=f"date: {cal.now().date()}",
                          weights=weights)

    def complete(inp: str) -> list:
        agent.reset()
        return agent.complete(inp).get("function_calls") or []

    if pipeline == "hybrid":
        # P0: Gemma first (canonicalize), then N2 → Python.
        inp, gemma_used = _canonical(gemma, case["goal"])
        try:
            calls = complete(inp) if inp else []
        except Exception as exc:  # noqa: BLE001
            calls, esc_reason = [], f"inference_error:{type(exc).__name__}"
    else:
        try:
            calls = complete(case["goal"])
        except Exception as exc:  # noqa: BLE001
            calls, escalated, esc_reason = [], True, f"inference_error:{type(exc).__name__}"
        if pipeline == "fallback" and not escalated:
            ok_exec, esc_reason = _inspect(calls, tools, store)
            escalated = not ok_exec
        if escalated and gemma is not None:
            # P-c: real Gemma→N2 fallback (nothing was written yet).
            inp, gemma_used = _canonical(gemma, case["goal"])
            if inp:
                try:
                    calls = complete(inp)
                    esc_reason = f"{esc_reason}·gemma"
                except Exception as exc:  # noqa: BLE001
                    calls, esc_reason = [], f"{esc_reason}·gemma_error:{type(exc).__name__}"

    write_attempts = 0
    failed_attempts = 0
    if escalated and not gemma_used:
        # not autonomous, no Gemma available → nothing written, stays open
        after = mut.snapshot(store)
        chk = _check(case["expect"], before, after, trace)
        chk["ok"] = False
        chk["why"] = f"escalated({esc_reason}) · gemma/n/a"
    else:
        for c in calls[:6]:
            if c["name"] in tools:
                trace_before = len(trace)
                tools[c["name"]](**(c.get("arguments") or {}))
                if c["name"] in WRITES:
                    write_attempts += 1
                    if len(trace) > trace_before and _failed(trace[-1]):
                        failed_attempts += 1
        after = mut.snapshot(store)
        chk = _check(case["expect"], before, after, trace)
        if escalated:
            chk["why"] = f"escalated({esc_reason}) → {chk['why']}"
    ms = round((time.perf_counter() - t0) * 1000)
    return {"id": case["id"], "family": case["family"], "goal": case["goal"],
            "calls": [c["name"] for c in calls], "escalated": escalated,
            "gemma_used": gemma_used, "esc_reason": esc_reason,
            "goal_ok": chk["ok"], "missed": chk["missed"],
            "correct_mutations": chk["correct_mutations"],
            "wrong_mutations": chk["wrong_mutations"],
            "successful_mutations": chk["correct_mutations"] + chk["wrong_mutations"],
            "write_attempts": write_attempts, "failed_write_attempts": failed_attempts,
            "ms": ms, "why": chk["why"],
            "writes": [t["tool"] for t in trace if t["tool"] in WRITES]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline", choices=["direct", "fallback", "hybrid"], required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-gemma", action="store_true",
                    help="never call Gemma (N3-venv cannot load N2 anyway); "
                         "P2 stays 'gemma/n/a' for offline case-id composition")
    args = ap.parse_args()
    weights = os.environ.get("NEEDLE_WEIGHTS") or None
    gemma = None
    if args.pipeline in ("hybrid", "fallback") and not args.no_gemma:
        g = Gemma()
        if g.available():
            gemma = g
            print(f"Gemma available ({g.model_id()})")
        elif args.pipeline == "hybrid":
            raise SystemExit("P0/hybrid braucht laufendes `cactus serve` "
                             "(CACTUS_BASE_URL) — Gemma nicht erreichbar")
        else:
            print("Gemma nicht erreichbar — P2 bleibt bei gemma/n/a (nur N3-Eskalation)")
    cases = build_cases()
    if args.limit:
        cases = cases[:args.limit]
    rows = [run_case(c, args.pipeline, weights, gemma) for c in cases]
    n = len(rows)

    def pct(sel):
        return round(sum(1 for r in rows if sel(r)) / n, 3)

    lats = [r["ms"] for r in rows]
    fams = {}
    for r in rows:
        f = fams.setdefault(r["family"], {"n": 0, "goal_ok": 0, "esc": 0,
                                          "wrong_mut": 0, "successful_mut": 0,
                                          "missed": 0})
        f["n"] += 1
        f["goal_ok"] += r["goal_ok"]
        f["esc"] += r["escalated"]
        f["wrong_mut"] += r["wrong_mutations"]
        f["successful_mut"] += r["successful_mutations"]
        f["missed"] += r["missed"]
    summary = {"tag": args.tag, "pipeline": args.pipeline, "weights": weights, "n": n,
               "final_goal_ok": pct(lambda r: r["goal_ok"]),
               "autonomous_ok": pct(lambda r: r["goal_ok"] and not r["escalated"]),
               "escalation_rate": pct(lambda r: r["escalated"]),
               "gemma_used_rate": pct(lambda r: r["gemma_used"]),
               "wrong_mutations": sum(r["wrong_mutations"] for r in rows),
               "successful_mutations": sum(r["successful_mutations"] for r in rows),
               "write_attempts": sum(r["write_attempts"] for r in rows),
               "failed_write_attempts": sum(r["failed_write_attempts"] for r in rows),
               "missed_actions": sum(r["missed"] for r in rows),
               "latency_p50_ms": round(st.median(lats)),
               "latency_p95_ms": round(sorted(lats)[int(n * 0.95) - 1]),
               "peak_rss_mb": round(resource.getrusage(
                   resource.RUSAGE_SELF).ru_maxrss / 1024),
               "gemma_available": gemma is not None,
               "families": fams, "rows": rows}
    out = HERE / "reports" / f"arch_{args.tag}_{args.pipeline}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"[{args.tag}/{args.pipeline}] n={n} · goal_ok {summary['final_goal_ok']} "
          f"· autonomous {summary['autonomous_ok']} · escalation_rate {summary['escalation_rate']}"
          f" · wrong_mutations {summary['wrong_mutations']} · missed {summary['missed_actions']}"
          f" · p50 {summary['latency_p50_ms']}ms p95 {summary['latency_p95_ms']}ms")
    print("  Familien:")
    for fam, f in sorted(fams.items()):
        print(f"    {fam:<14} n={f['n']:<3} goal_ok {f['goal_ok']}/{f['n']} · esc {f['esc']}"
              f" · wrong_mut {f['wrong_mut']} · missed {f['missed']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
