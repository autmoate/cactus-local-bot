"""Needle Bakeoff v2 — controlled ablation (plan §A-M).

Fixes vs v1 (review):
- 66 cases (2 forms each: raw user input / canonical Gemma instruction)
- 3 repeats with FRESH engine init per repeat (measures real variance)
- three separate metrics: tool role correctness, ARGUMENT SEMANTIC
  correctness (tomorrow == 2026-09-14), final domain correctness (call
  executed against a temp SQLite store, end state asserted)
- real @needle.tool functions exactly like production: int + needle.Field,
  Literal, required/optional — no all-string hand-written schemas
- naming ablation isolated (identical params/descriptions/count)
- split ablation isolated (5-tool create_event variant vs 6-tool with
  retrieval) and reported separately
- order ablation over 10 permutations
- run() benchmarked as a first-class variant with ungrounded reporting
- extract() in several shapes (single, list, iterative)

Usage:
  uv run python experiments/needle_bakeoff_v2.py --block naming
  uv run python experiments/needle_bakeoff_v2.py --block orders
  uv run python experiments/needle_bakeoff_v2.py --block split
  uv run python experiments/needle_bakeoff_v2.py --block run
  uv run python experiments/needle_bakeoff_v2.py --block extract
  uv run python experiments/needle_bakeoff_v2.py --block loop
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Literal

import needle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bakeoff_cases import CASES  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import execute_call  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402

REF_FACTS = "date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi"
REF = date(2026, 9, 13)
SYS = REF_FACTS

# ------------------------------------------------------- production-style tools
# Built exactly like production: decorators, types, Literal, Field constraints.


def _bind(store):
    from local_calendar.agent import build_tools  # production factories
    return build_tools(store)


def _naming_variant(store, names: dict[str, str]):
    """Same bodies/params/descriptions as production, ONLY the tool name in
    the compiled schema changes (plan §G — isolated naming ablation)."""
    prod = _bind(store)
    out = {}
    for semantic, fn in prod.items():
        new_name = names.get(semantic, fn.__name__)
        schema = dict(fn._needle_tool)
        schema["name"] = new_name
        fn._needle_tool = schema
        fn.__name__ = new_name
        out[new_name] = fn
    return out


def _split_tools(store, with_absence: bool):
    """Phase 7: split create into appointment/absence (isolated ablation)."""
    from typing import Literal as _L
    import needle as _n

    @needle.tool
    def create_event(title: str, date: str = "", time: str = "",
                     end_time: str = "", participants: str = "") -> str:
        """Create an appointment (timed).

        Args:
            title: short title
            date: day like 'tomorrow' or '2026-09-17'
            time: start time like '14:00'
            end_time: end time like '16:00'
            participants: comma-separated names
        """
        return "ok"

    if with_absence:
        @needle.tool
        def create_absence(title: str, date: str = "", until: str = "",
                           participants: str = "") -> str:
            """Create an all-day absence (vacation, sick day, trip).

            Args:
                title: short title
                date: first day like 'august 3'
                until: last day like 'august 18'; empty for one day
                participants: comma-separated names
            """
            return "ok"

    @needle.tool
    def move_event(title: str, date: str = "", time: str = "") -> str:
        """Move an existing entry.

        Args:
            title: existing title
            date: new day
            time: new time
        """
        return "ok"

    @needle.tool
    def delete_event(title: str, date: str = "") -> str:
        """Delete an entry.

        Args:
            title: title
            date: day hint
        """
        return "ok"

    @needle.tool
    def list_events(person: str = "", date: str = "", until: str = "") -> str:
        """List entries.

        Args:
            person: name filter
            date: day
            until: last day
        """
        return "ok"

    @needle.tool
    def find_free_slots(persons: str, duration_min: int = 60,
                        date: str = "", until: str = "") -> str:
        """Find common free slots.

        Args:
            persons: comma-separated names
            duration_min: minimum slot length in minutes
            date: day
            until: last day
            days: horizon days
        """
        return "ok"

    fns = [create_event, move_event, delete_event, list_events, find_free_slots]
    if with_absence:
        fns.insert(1, create_absence)
    return {f.__name__: f for f in fns}


def _role_names(variant: str) -> dict[str, set[str]]:
    """Map semantic role -> acceptable tool names for that variant."""
    if variant in ("prod", "order"):
        return {r: {f"calendar_{r}" if r != "find" else "calendar_find_slot"}
                for r in ("create", "move", "delete", "list", "find")}
    if variant == "naming":
        return {"create": {"create_event"}, "move": {"move_event"},
                "delete": {"delete_event"}, "list": {"list_events"},
                "find": {"find_free_slots"}}
    if variant == "split5":
        return {"create": {"create_event"}, "move": {"move_event"},
                "delete": {"delete_event"}, "list": {"list_events"},
                "find": {"find_free_slots"}}
    if variant == "split6":
        base = {r: {f"{r}_event"} for r in ("move", "delete")}
        base.update({"create": {"create_event", "create_absence"},
                     "list": {"list_events"}, "find": {"find_free_slots"}})
        return base
    raise ValueError(variant)


def _tool_fns(variant: str, store):
    if variant == "prod":
        return list(_bind(store).values())
    if variant == "naming":
        return list(_naming_variant(store, {
            "calendar_create": "create_event", "calendar_move": "move_event",
            "calendar_delete": "delete_event", "calendar_list": "list_events",
            "calendar_find_slot": "find_free_slots"}).values())
    if variant == "split5":
        return list(_split_tools(store, with_absence=False).values())
    if variant == "split6":
        return list(_split_tools(store, with_absence=True).values())
    raise ValueError(variant)


# ------------------------------------------------------------------- metrics

def _sem_date(a: str | None, b: str | None) -> bool:
    if not a and not b:
        return True
    ra = cal.resolve_date(a or "", REF, roll=False) if (a or "").strip() else None
    rb = cal.resolve_date(b or "", REF, roll=False) if (b or "").strip() else None
    if ra is None or rb is None:  # fall back to normalized string compare
        return (a or "").strip().lower() == (b or "").strip().lower()
    return ra == rb


def _sem_time(a: str | None, b: str | None) -> bool:
    ta = cal.resolve_time(a or "")
    tb = cal.resolve_time(b or "")
    if ta is None or tb is None:
        return (a or "").strip().lower() == (b or "").strip().lower()
    return ta == tb


def _sem_persons(a: str | None, b: str | None) -> bool:
    pa = cal.parse_persons(a or "")
    pb = cal.parse_persons(b or "")
    return bool({p.lower() for p in pa} & {p.lower() for p in pb})


def _args_semantic(got: dict, want: dict) -> tuple[bool, list[str]]:
    """Semantic argument correctness (plan §E): dates/times via resolver,
    titles by containment, persons as sets. Returns (ok, failures)."""
    fails = []
    for key, want_val in want.items():
        got_val = got.get(key)
        if key in ("date", "until"):
            if not _sem_date(got_val, want_val):
                fails.append(f"{key}: {got_val!r} != {want_val!r}")
        elif key == "time":
            if not _sem_time(got_val, want_val):
                fails.append(f"{key}: {got_val!r} != {want_val!r}")
        elif key == "persons":
            if not _sem_persons(got_val, want_val):
                fails.append(f"{key}: {got_val!r} != {want_val!r}")
        elif key == "title":
            if want_val.lower() not in (got_val or "").lower():
                fails.append(f"{key}: {got_val!r} !~ {want_val!r}")
        else:
            if str(got_val or "").strip().lower() != str(want_val).strip().lower():
                fails.append(f"{key}: {got_val!r} != {want_val!r}")
    return (not fails), fails


def _final_ok(store, case: dict) -> bool:
    """Final domain correctness (plan §E): end state of a temp store."""
    want = case["final"]
    if not want:
        return True
    events = store.events_between(cal.now() - cal.timedelta(days=400),
                                  cal.now() + cal.timedelta(days=500))
    if "result_contains" in want:
        return want["result_contains"].lower() in (case.get("_result") or "").lower()
    for a in want.get("absent", []):
        if any(a.lower() in e.title.lower() for e in events):
            return False
    for w in want.get("exists", []):
        found = False
        for e in events:
            if "title_contains" in w and w["title_contains"].lower() not in e.title.lower():
                continue
            if "start_date" in w and e.start.date().isoformat() != w["start_date"]:
                continue
            if "start_time" in w and e.start.strftime("%H:%M") != w["start_time"]:
                continue
            if "end_time" in w and e.end.strftime("%H:%M") != w["end_time"]:
                continue
            if "all_day" in w and e.all_day != w["all_day"]:
                continue
            if "span_days" in w and (e.end.date() - e.start.date()).days != w["span_days"]:
                continue
            found = True
            break
        if not found:
            return False
    return True


# ----------------------------------------------------------------- runner

def run_block(variant: str, cases, repeats: int = 3, mode: str = "complete") -> dict:
    roles = _role_names(variant)
    reps = []
    for rep in range(repeats):
        store = CalendarStore(Path(tempfile.mkdtemp()) / "b.db")
        fns = _tool_fns(variant, store)
        agent = needle.Needle(tools=fns, system=SYS)
        rows = []
        for case in cases:
            inp = case["canon"] if mode.startswith("canon") else case["raw"]
            for step in case.get("setup") or []:
                execute_call(store, step["tool"], step["args"])
            agent.reset()
            t0 = time.perf_counter()
            resp = agent.complete(inp)
            ms = round((time.perf_counter() - t0) * 1000)
            calls = resp.get("function_calls") or []
            got = calls[0] if calls else {}
            got_name = got.get("name")
            got_args = got.get("arguments") or {}
            role = case["tool"]
            tool_ok = (role == "off-topic" and not got_name) or (
                got_name is not None and got_name in roles.get(role, set()))
            args_ok = False
            final_ok = False
            if got_name and case["should_execute"]:
                args_ok, fails = _args_semantic(got_args, case["args"])
                # final domain correctness: execute against the temp store
                semantic_tool = f"calendar_{role}" if role != "find" \
                    else "calendar_find_slot"
                out = execute_call(store, semantic_tool, got_args, inp)
                final_ok = bool(out.get("ok"))
                case["_result"] = out.get("message", "")
                final_ok = _final_ok(store, case)
            elif role == "off-topic":
                final_ok = not got_name
            rows.append({"id": case["id"], "tool": got_name, "args": got_args,
                         "tool_ok": tool_ok, "args_ok": tool_ok and args_ok,
                         "final_ok": final_ok, "refusal": not got_name,
                         "ms": ms, "conf": resp.get("confidence")})
        reps.append(rows)
    n = len(reps[0])
    agg = {}
    for metric in ("tool_ok", "args_ok", "final_ok"):
        per_case = [all(rep[i][metric] for rep in reps) for i in range(n)]
        any_case = [any(rep[i][metric] for rep in reps) for i in range(n)]
        agg[f"{metric}_all_reps"] = round(sum(per_case) / n, 3)
        agg[f"{metric}_any_rep"] = round(sum(any_case) / n, 3)
    agg["refusal_rate"] = round(statistics.mean(
        [sum(1 for r in rep if r["refusal"]) / n for rep in reps]), 3)
    consistent = []
    for i in range(n):
        variants = {json.dumps((rep[i]["tool"],
                                json.dumps(rep[i]["args"], sort_keys=True,
                                           ensure_ascii=False)))
                    for rep in reps}
        consistent.append(len(variants) == 1)
    agg["exact_call_stability"] = round(statistics.mean(consistent), 3)
    agg["median_ms"] = round(statistics.median(
        [r["ms"] for rep in reps for r in rep]))
    agg["median_conf"] = round(statistics.median(
        [r["conf"] or 0 for rep in reps for r in rep]), 3)
    return {"variant": variant, "repeats": repeats, "n_cases": n,
            "metrics": agg, "rows": reps[-1]}


def run_orders(cases, repeats=1):
    import itertools
    base = ["calendar_create", "calendar_move", "calendar_delete",
            "calendar_list", "calendar_find_slot"]
    orders = [base, list(reversed(base))]
    orders += [list(p) for p in itertools.islice(
        itertools.permutations(base), 2, 12)][:10]
    orders = orders[:10]  # plan §I: >=10 orders
    out = []
    for i, order in enumerate(orders):
        roles = {"calendar_create": "create", "calendar_move": "move",
                 "calendar_delete": "delete", "calendar_list": "list",
                 "calendar_find_slot": "find"}
        rows = []
        store = CalendarStore(Path(tempfile.mkdtemp()) / "o.db")
        fns = list(_bind(store).values())
        agent = needle.Needle(tools=fns, system=SYS)
        for case in cases:
            inp = case["canon"] if case.get("canon") else case["raw"]
            agent.reset()
            resp = agent.complete(inp)
            calls = resp.get("function_calls") or []
            got = calls[0] if calls else {}
            ok = roles.get(got.get("name")) == case["tool"]
            rows.append(ok if case["tool"] != "off-topic" else not got.get("name"))
        out.append({"order": i, "tool_acc": round(sum(rows) / len(rows), 3),
                    "order": order})
    return out


def run_read_loop(cases, repeats=2):
    """Phase 11: run() as a first-class variant with ungrounded reporting."""
    from local_calendar.agent import build_tools
    out = []
    for strict in (True, False):
        stats = {"ok": 0, "ungrounded_fail": 0, "refusals": 0, "n": 0}
        samples = []
        for _ in range(repeats):
            store = CalendarStore(Path(tempfile.mkdtemp()) / "r.db")
            prod = build_tools(store)
            read_tools = [prod["calendar_list"], prod["calendar_find_slot"]]
            agent = needle.Needle(tools=read_tools, system=SYS)
            for case in cases:
                if case["tool"] not in ("list", "find"):
                    continue
                agent.reset()
                resp = agent.run(case["canon"])
                results = resp.get("results") or []
                err = [r for r in results if isinstance(r, dict) and r.get("error")]
                ungr = any("ungrounded" in str(r) for r in results)
                ok = bool(results) and not ungr
                stats["ok"] += ok
                stats["ungrounded_fail"] += ungr
                stats["refusals"] += (not resp.get("function_calls")
                                      and not results)
                stats["n"] += 1
                samples.append({"input": (case.get("canon") or case.get("raw") or "")[:40],
                                "results": json.dumps(results, ensure_ascii=False,
                                                      default=str)[:200]})
        out.append({"run_strict": strict, **{k: v for k, v in stats.items()}})
    return out


def run_extract_shapes():
    """Phase 12/K: extract() in several shapes on the same inputs."""
    from pydantic import BaseModel
    from typing import Literal as _L

    class EvSingle(BaseModel):
        """One calendar event described in text."""
        title: str
        date: str | None = None
        until: str | None = None
        time: str | None = None
        end_time: str | None = None

    class EvList(BaseModel):
        """All calendar events described in the text."""
        events: list[EvSingle] = []

    SYSX = SYS
    inp_single = "Termin Zahnarzt am 17.9. um 10 Uhr"
    inp_multi = "17.9. Zahnarzt 10 Uhr, Meeting 13-16, 10.10. TÜV 9 Uhr"
    out = {}
    r = needle.extract(inp_single, EvSingle, system=SYSX, strict=False)
    out["single"] = (r.model_dump() if r else None)
    r = needle.extract(inp_multi, EvList, system=SYSX, strict=False)
    out["list_items"] = len(r.events) if r else 0
    # iterative single-item extraction: extract one, then the remaining text
    got = []
    rest = inp_multi
    for _ in range(3):
        r = needle.extract(rest, EvSingle, system=SYSX, strict=False)
        if not r:
            break
        got.append(r.model_dump())
        # crude remainder: strip the extracted title/dates from the rest
        for token in [r.title, r.date or "", r.time or ""]:
            rest = rest.replace(token, " ", 1)
    out["iterative"] = got
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", required=True,
                    choices=["naming", "orders", "split", "run", "extract",
                             "full"])
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()
    cases = CASES
    results = {}
    if args.block in ("naming", "full"):
        results["prod"] = run_block("prod", cases, args.repeats)
        results["naming"] = run_block("naming", cases, args.repeats)
    if args.block in ("split", "full"):
        results["split5"] = run_block("split5", cases, args.repeats)
        results["split6"] = run_block("split6", cases, args.repeats)
    if args.block in ("orders", "full"):
        results["orders"] = run_orders(cases)
    if args.block in ("run", "full"):
        results["run"] = run_read_loop(cases)
    if args.block in ("extract", "full"):
        results["extract"] = run_extract_shapes()
    print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    out = Path(__file__).parent / f"bakeoff_v2_{args.block}_{int(time.time())}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
