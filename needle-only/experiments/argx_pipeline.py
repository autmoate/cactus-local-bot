"""ArgEx-Pipeline (plan Phase 2-5): separate tool selection from argument
extraction. Gemma/any source gives the text; Needle selects the tool with
complete(); a per-tool extraction schema (single, flat) pulls the args;
Python resolves and executes.

Variants measured against the SAME bakeoff cases + metrics:
  baseline      production complete() (tool + args in one call)
  argx          tool-select -> extract(CreateArgs/...) with "" defaults
  argx_none     same but optional fields are `str | None = None` (plan §4)
  argx_expr     temporal expressions only (plan §5):
                start_expression / end_expression / until_expression —
                Python resolves afterwards
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Literal

import needle
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bakeoff_cases import CASES  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import execute_call  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402

SYS = "date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi"
REF = cal.now().date()

# ------------------------------------------------------- per-tool arg schemas

class Extract_CreateArgs(BaseModel):
    """Calendar entry data described in text."""
    title: str
    date: str | None = None
    until: str | None = None
    time: str | None = None
    end_time: str | None = None
    participants: str | None = None


class Extract_MoveArgs(BaseModel):
    """Calendar mutation described in text."""
    title: str
    date: str | None = None
    time: str | None = None


class Extract_DeleteArgs(BaseModel):
    """Calendar removal described in text."""
    title: str | None = None
    date: str | None = None


class Extract_ListArgs(BaseModel):
    """Calendar list query described in text."""
    person: str | None = None
    date: str | None = None
    until: str | None = None
    horizon: Literal["today", "week", "month"] | None = None


class Extract_FindSlotArgs(BaseModel):
    """Calendar slot query described in text."""
    persons: str
    duration_min: int | None = None
    date: str | None = None
    until: str | None = None


class Extract_CreateArgsNone(BaseModel):
    """Calendar entry data described in text."""
    title: str | None = None
    date: str | None = None
    until: str | None = None
    time: str | None = None
    end_time: str | None = None
    participants: str | None = None


ARG_SCHEMAS = {"create": Extract_CreateArgs, "move": Extract_MoveArgs, "delete": Extract_DeleteArgs,
               "list": Extract_ListArgs, "find": Extract_FindSlotArgs}

ARG_SCHEMAS_NONE = {  # plan §4: optional fields as `str | None = None`
    "create": Extract_CreateArgsNone, "move": Extract_MoveArgs,
    "delete": Extract_DeleteArgs, "list": Extract_ListArgs,
    "find": Extract_FindSlotArgs}

class Extract_CreateArgsExpr(BaseModel):
    """Temporal expressions described in text."""
    title: str | None = None
    start_expression: str | None = None
    end_expression: str | None = None
    until_expression: str | None = None
    participants: str | None = None

ARG_SCHEMAS_EXPR = {"create": Extract_CreateArgsExpr, "move": Extract_MoveArgs,
                    "delete": Extract_DeleteArgs, "list": Extract_ListArgs,
                    "find": Extract_FindSlotArgs}


def _schemas_for(pipeline: str) -> dict:
    return {"argx": ARG_SCHEMAS, "argx_none": ARG_SCHEMAS_NONE,
            "argx_expr": ARG_SCHEMAS_EXPR}[pipeline]


def _resolve_expr_args(args: CreateArgsExpr) -> dict:
    """Python resolves expression-style args into production handler args."""
    out = {"title": args.title}
    if args.start_expression:
        out["date"] = args.start_expression
        t = cal.extract_time_from_text(args.start_expression)
        if t:
            out["time"] = args.start_expression  # resolver handles merged forms
    if args.end_expression:
        out["end_time"] = args.end_expression
    if args.until_expression:
        out["until"] = args.until_expression
    if args.participants:
        out["participants"] = args.participants
    return out


def _sem_check(args: dict, want: dict, ref=None) -> bool:
    ref = ref or cal.now().date()
    for key, want_val in want.items():
        got_val = args.get(key)
        if key in ("date", "until"):
            ra = cal.resolve_date(str(got_val or ""), REF, roll=False)
            rb = cal.resolve_date(str(want_val), REF, roll=False)
            if ra and rb:
                if ra != rb:
                    return False
            elif str(got_val or "").lower() != str(want_val).lower():
                return False
        elif key == "time":
            ta, tb = cal.resolve_time(str(got_val or "")), cal.resolve_time(str(want_val))
            if ta and tb:
                if ta != tb:
                    return False
            elif str(got_val or "").lower() != str(want_val).lower():
                return False
        elif key == "persons":
            pg = {p.lower() for p in cal.parse_persons(str(got_val or ""))}
            pw = {p.lower() for p in cal.parse_persons(str(want_val))}
            if not pw <= pg:
                return False
        elif key == "title":
            if str(want_val).lower() not in str(got_val or "").lower():
                return False
        else:
            if str(got_val or "").strip().lower() != str(want_val).strip().lower():
                return False
    return True


def _final_state(store, want: dict, result: str = "") -> bool:
    if not want:
        return True
    if "result_contains" in want:
        return want["result_contains"].lower() in (result or "").lower()
    events = store.events_between(cal.now() - cal.timedelta(days=400),
                                  cal.now() + cal.timedelta(days=500))
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


def run_argx(pipeline: str, cases, repeats: int = 3) -> dict:
    """Two-call pipeline: Needle selects the tool (production complete on the
    full toolset), then extract() fills the per-tool args schema."""
    from local_calendar.agent import build_tools
    all_rows = []
    for _ in range(repeats):
        for case in cases:
            store = CalendarStore(Path(tempfile.mkdtemp()) / "case.db")
            for step in case.get("setup") or []:
                execute_call(store, step["tool"], step["args"])
            prod = build_tools(store)
            agent = needle.Needle(tools=list(prod.values()), system=SYS)
            needle_calls = 0
            inp = case["raw"]
            agent.reset()
            resp = agent.complete(inp)
            needle_calls += 1
            calls = resp.get("function_calls") or []
            got = calls[0] if calls else {}
            got_name = got.get("name")
            role = case["tool"]
            tool_ok = (role == "off-topic" and not got_name) or (
                got_name is not None
                and got_name == {"create": "calendar_create",
                                 "move": "calendar_move",
                                 "delete": "calendar_delete",
                                 "list": "calendar_list",
                                 "find": "calendar_find_slot"}.get(role))
            args_ok = False
            final_ok = False
            if tool_ok and case["should_execute"] and role != "off-topic":
                schema = _schemas_for(pipeline).get(role)
                if schema is not None:
                    extracted = needle.extract(inp, schema, system=SYS,
                                               strict=False)  # keep system facts!
                    needle_calls += 1
                    args = extracted.model_dump() if extracted else {}
                    if pipeline == "argx_expr" and role == "create":
                        # plan §5: temporal expressions — Python resolves
                        if args.get("start_expression"):
                            args["date"] = args.pop("start_expression")
                            t = cal.extract_time_from_text(args["date"])
                            args["time"] = args["date"]
                        if args.get("end_expression"):
                            args["end_time"] = args.pop("end_expression")
                        if args.get("until_expression"):
                            args["until"] = args.pop("until_expression")
                    args = {k: v for k, v in args.items()
                            if v not in (None, "") or k == "title"}
                    args_ok = _sem_check(args, case["args"], REF) \
                        if case["args"] else True
                    handler = {"create": "calendar_create",
                               "move": "calendar_move",
                               "delete": "calendar_delete",
                               "list": "calendar_list",
                               "find": "calendar_find_slot"}[role]
                    out = execute_call(store, handler, args, inp)
                    final_ok = bool(out.get("ok")) and \
                        _final_state(store, case["final"],
                                     out.get("message", ""))
            all_rows.append({"id": case["id"], "tool_ok": tool_ok,
                             "args_ok": tool_ok and args_ok,
                             "final_ok": final_ok,
                             "needle_calls": needle_calls})
    n = len(all_rows)
    return {"pipeline": pipeline,
            "tool_ok": round(sum(r["tool_ok"] for r in all_rows) / n, 3),
            "args_ok": round(sum(r["tool_ok"] and r["args_ok"]
                                 for r in all_rows) / n, 3),
            "final_ok": round(sum(r["final_ok"] for r in all_rows) / n, 3),
            "needle_calls_per_request": round(
                statistics.mean(r["needle_calls"] for r in all_rows), 2)}


def run_baseline(cases, repeats: int = 3) -> dict:
    """Production complete() baseline with identical metric wiring."""
    from local_calendar.agent import build_tools
    all_rows = []
    for _ in range(repeats):
        for case in cases:
            store = CalendarStore(Path(tempfile.mkdtemp()) / "case.db")
            for step in case.get("setup") or []:
                execute_call(store, step["tool"], step["args"])
            prod = build_tools(store)
            agent = needle.Needle(tools=list(prod.values()), system=SYS)
            agent.reset()
            resp = agent.complete(case["raw"])
            calls = resp.get("function_calls") or []
            got = calls[0] if calls else {}
            got_name = got.get("name")
            role = case["tool"]
            tool_ok = (role == "off-topic" and not got_name) or (
                got_name is not None
                and got_name == {"create": "calendar_create",
                                 "move": "calendar_move",
                                 "delete": "calendar_delete",
                                 "list": "calendar_list",
                                 "find": "calendar_find_slot"}.get(role))
            args_ok = False
            final_ok = False
            if tool_ok and case["should_execute"] and role != "off-topic":
                args = got.get("arguments") or {}
                args_ok = _sem_check(args, case["args"], REF) \
                    if case["args"] else True
                handler = {"create": "calendar_create", "move": "calendar_move",
                           "delete": "calendar_delete", "list": "calendar_list",
                           "find": "calendar_find_slot"}[role]
                out = execute_call(store, handler, args, case["raw"])
                final_ok = bool(out.get("ok")) and \
                    _final_state(store, case["final"], out.get("message", ""))
            all_rows.append({"tool_ok": tool_ok, "args_ok": tool_ok and args_ok,
                             "final_ok": final_ok, "needle_calls": 1})
    n = len(all_rows)
    return {"pipeline": "baseline-complete",
            "tool_ok": round(sum(r["tool_ok"] for r in all_rows) / n, 3),
            "args_ok": round(sum(r["tool_ok"] and r["args_ok"]
                                 for r in all_rows) / n, 3),
            "final_ok": round(sum(r["final_ok"] for r in all_rows) / n, 3),
            "needle_calls_per_request": 1.0}


def main() -> None:
    cases = [c for c in CASES if c["tool"] != "off-topic"]
    results = {"baseline": run_baseline(cases)}
    results["argx"] = run_argx("argx", cases)
    results["argx_none"] = run_argx("argx_none", cases)
    results["argx_expr"] = run_argx("argx_expr", cases)
    print(json.dumps(results, ensure_ascii=False, indent=1))
    out = Path(__file__).parent / f"argx_{int(time.time())}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
