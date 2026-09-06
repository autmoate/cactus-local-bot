"""Needle-only Eval v5.3 „CRUD": 7 Tools, CRUD-Semantik, Hard-Deletes.
Aufruf: uv run python needle-only/eval.py [--repeat 2] [--filter x]"""
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_cases import CASES  # noqa: E402
from orga import _norm_dt  # noqa: E402
from run import WRITE, build, draft_calls  # noqa: E402


def _turn(agent, tools, fns, text, lang="de"):
    """Needle-Turn → recorded: [(tool_name, args, result_str)]"""
    calls = draft_calls(agent, fns, text, lang)

    if not calls:
        return []

    recorded = []
    reads, writes = [], []
    for call in calls:
        name = call["tool"]
        args = call["arguments"]
        if name in WRITE:
            writes.append((name, args))
        else:
            result = tools.execute(name, args, text)
            reads.append((name, args, result))

    if writes:
        for name, args in writes:
            result = tools._execute_write(name, args)
            recorded.append((name, args, result))
    else:
        for name, args, result in reads:
            recorded.append((name, args, result))

    return recorded


def judge(expect, recorded) -> tuple[bool, str]:
    """Prüft, ob die recorded_ops der Erwartung entsprechen."""
    if expect == "NOWRITE":
        bad = [r[0] for r in recorded if r[0] in WRITE]
        return (False, f"write ohne approval: {bad}") if bad else (True, "kein write")
    if expect == "GATEDWRITE":
        return (True, "gated write (approval) ok")

    if not isinstance(expect, list):
        expect = [(expect, [])]

    if len(recorded) != len(expect):
        got = [r[0] for r in recorded]
        want = [e[0] for e in expect]
        return False, f"ops={got} (erwartet {want})"

    for (spec, checks), (name, args, result) in zip(expect, recorded):
        if name != spec:
            return False, f"op={name} (erwartet {spec})"
        for check in checks:
            if isinstance(check, tuple) and len(check) == 3:
                ctype, field, expected = check
                actual = args.get(field, "")
                if ctype == "eq" and str(actual).lower() != str(expected).lower():
                    return False, f"{field}={actual} (erwartet {expected})"
                if ctype == "contains" and str(expected).lower() not in str(actual).lower():
                    return False, f"{field}={actual} (erwartet contains '{expected}')"
            elif isinstance(check, str):
                if check.lower() not in (result or "").lower():
                    return False, f"result ohne '{check}': {result[:70] if result else 'kein result'}"

    return True, ""


def reset_data(tools) -> None:
    tools.orga._q("DELETE FROM entries")
    tools.orga._q("DELETE FROM n_notes")


def seed(tools, seeds) -> None:
    """Seeds: (title, value). value: ISO → appointment; 'R iso' → reminder; text → note."""
    for title, value in seeds or []:
        if value.startswith("R "):
            tools.orga._q(
                "INSERT INTO entries (kind, title, start_at) VALUES ('reminder', %s, %s)",
                (title, _norm_dt(value[2:])))
        elif re.match(r"\d{4}-\d{2}-\d{2}T", value):
            tools.orga._q(
                "INSERT INTO entries (kind, title, start_at) VALUES ('appointment', %s, %s)",
                (title, _norm_dt(value)))
        else:
            tools.orga._q(
                "INSERT INTO n_notes (subject, body) VALUES (%s, %s)",
                (title, value))


def run_suite(tools, agent, fns, cases, lang: str) -> list:
    results = []
    t0 = time.time()
    for name, text, expect, seeds in cases:
        ct = time.time()
        reset_data(tools)
        seed(tools, seeds)
        try:
            recorded = _turn(agent, tools, fns, text, lang)
            ok, why = judge(expect, recorded)
        except Exception as exc:
            import traceback
            ok = False
            why = "fehler: " + (traceback.format_exc() if os.environ.get("EVAL_TRACE") else str(exc)[:70])
        results.append((name, ok, why))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time()-ct:.1f}s)  {why}", flush=True)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"[{lang}] {passed}/{len(cases)} · ø {(time.time()-t0)/max(1,len(cases)):.1f}s/Fall")
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

    ok_sets = [frozenset(n for n, ok, _ in r if r) for r in runs]
    stable = all(s == ok_sets[0] for s in ok_sets[1:]) if len(ok_sets) > 1 else True
    print(f"\nErgebnisse: {[f'{sum(1 for _, ok, _ in r if ok)}/{len(r)}' for r in runs]} · "
          f"deterministisch: {'ja' if stable else 'NEIN'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
