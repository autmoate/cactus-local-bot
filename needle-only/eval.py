"""Needle-only Eval v3.1 „Plan-Werkstatt": Ops → Plan → Judge (auto, ohne Approval).
Seeds werden pro Fall eingeseedt (deterministische Fixtures). Kein serve nötig.
Aufruf: uv run python needle-only/eval.py [--repeat 2] [--filter x]  (NEEDLE_LANG=en optional)"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib.util  # noqa: E402

from eval_cases import CASES  # noqa: E402

_spec = importlib.util.spec_from_file_location("gemma_eval", str(ROOT / "scripts" / "eval.py"))
_gemma_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gemma_eval)
check = _gemma_eval.check  # noqa: E402
from orga import _norm_dt  # noqa: E402
from run import WRITE, build, fix_args  # noqa: E402
from translate import de2en  # noqa: E402

_OP_NAME = {"move": "upsert_event", "add": "upsert_event", "cancel": "cancel_event",
            "rem_add": "upsert_reminder", "rem_move": "upsert_reminder",
            "rem_done": "complete_reminder", "note": "remember_note"}


def _turn(agent, tools, fns, text, lang="de"):
    """Needle-Turn (inkl. Multi-Op-Retry) → recorded: [(name, flat_args, text)] für Ops + Reads."""
    if lang == "en":
        text, _ = de2en(text)
    from run import draft_calls
    calls = draft_calls(agent, fns, text, lang)
    reads, writes = [], []
    for call in calls:
        if call["tool"] in WRITE:
            writes.append(call)
        else:
            reads.append((call["tool"], call["arguments"], tools.execute(call["tool"], call["arguments"], text)))
    plan = tools.plan(writes, text) if writes else {"lines": [], "ops": [], "warn": []}
    op_recs = [(_OP_NAME.get(op["kind"], op["kind"]),
                {"title": op.get("title"), "start_at": op.get("start"), "due_at": op.get("due")},
                "\n".join(plan["lines"] + [f"⚠ {w}" for w in plan["warn"]])) for op in plan["ops"]]
    return reads, op_recs


def judge(expect, recorded_ops) -> tuple[bool, str]:
    if expect in ("NOWRITE", "GATEDWRITE"):
        bad = [r[0] for r in recorded_ops if r[0] in WRITE]
        return (False, f"write: {bad}") if bad else (True, "kein Write")
    if not isinstance(expect, list):
        expect = [(expect, [])]
    if len(recorded_ops) != len(expect):
        got = [r[0] for r in recorded_ops]
        want = [e[0][1] if isinstance(e[0], tuple) else e[0] for e in expect]
        return False, f"ops={got} (erwartet {want})"
    for (spec, checks), (name, flat, lines) in zip(expect, recorded_ops):
        allowed = spec[1] if isinstance(spec, tuple) and spec[0] == "TOOL_IN" else [spec]
        if name not in allowed:
            return False, f"op={name} (erwartet {allowed})"
        for s in checks:
            if s[0] == "result~":
                if s[1].lower() not in lines.lower():
                    return False, f"plan ohne '{s[1]}': {lines[:70]}"
            elif not check(s[0], flat, s):
                return False, f"{s[0]} fehlgeschlagen: {s} args={flat}"
    return True, ""


def reset_data(tools) -> None:
    tools.orga._q("truncate n_events, n_reminders, n_notes restart identity")


def seed(tools, seeds) -> None:
    for title, value in seeds or []:
        if value.startswith("R "):
            tools.orga._q("insert into n_reminders (title, due_at) values (%s,%s)",
                          (title, _norm_dt(value[2:])))
        elif "T" in value:
            tools.orga._q("insert into n_events (title, start_at) values (%s,%s)",
                          (title, _norm_dt(value)))
        else:
            tools.orga._q("insert into n_notes (subject, body) values (%s,%s)", (title, value))


def run_suite(tools, agent, fns, cases, lang: str) -> list[tuple[str, bool, str]]:
    results = []
    t0 = time.time()
    for name, text, expect, seeds in cases:
        ct = time.time()
        reset_data(tools)
        seed(tools, seeds)
        try:
            reads, op_recs = _turn(agent, tools, fns, text, lang)
            recorded = op_recs + reads  # Plan-Ops zuerst, dann Reads (List-Fälle)
            ok, why = judge(expect, recorded)
        except Exception as exc:
            import traceback
            ok = False
            why = "fehler: " + (traceback.format_exc() if os.environ.get("EVAL_TRACE") else str(exc)[:70])
        results.append((name, ok, why))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time()-ct:.1f}s)  {why}", flush=True)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"[{lang}] {passed}/{len(cases)} · ø {(time.time()-t0)/len(cases):.1f}s/Fall")
    return results


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--filter", default="")
    args = ap.parse_args()
    lang = os.environ.get("NEEDLE_LANG", "de")
    tools, agent, fns = build()
    cases = [c for c in CASES if args.filter in c[0]] or CASES
    runs = []
    for i in range(args.repeat):
        print(f"\n--- Lauf {i+1}/{args.repeat} ---")
        runs.append(run_suite(tools, agent, fns, cases, lang))
    ok_sets = [frozenset(n for n, ok, _ in r if ok) for r in runs]
    stable = all(s == ok_sets[0] for s in ok_sets[1:])
    print(f"\nErgebnisse: {[f'{sum(1 for _, ok, _ in r if ok)}/{len(r)}' for r in runs]} · "
          f"deterministisch: {'ja' if stable else 'NEIN'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
