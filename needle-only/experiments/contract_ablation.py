"""Contract ablation (review point 10 — the biggest lever):
Contract A (current): args carry normalized/symbolic values that Needle must
                      partially compute or leave empty (date/until/time/end_time).
Contract B (grounded): args are PURE input spans — date_expression/time_expression/
                       period (Literal) — Python resolves afterwards. This matches
                       Needle's documented grounding philosophy (input span -> arg).
Same cases, same tool bodies, same names — only the ARGUMENT CONTRACT differs.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bakeoff_cases import CASES  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import execute_call  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402

SYS = "date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi"
REF = cal.now().date()


def _tools_contract_a(store):
    """Contract A: production tools (current args)."""
    from local_calendar.agent import build_tools
    return list(build_tools(store).values())


def _tools_contract_b(store):
    """Contract B: grounded symbolic args — Needle extracts spans, Python resolves."""
    import needle as _n

    @needle.tool
    def create(title: str, date_expression: str = "", time_expression: str = "",
               end_time_expression: str = "", until_expression: str = "",
               participants: str = "") -> str:
        """Create a calendar entry.

        Args:
            title: short title (verbatim from the request)
            date_expression: when, e.g. 'tomorrow', '17.9.', 'august 3'
            time_expression: start time like '10:00' or 'afternoon'
            end_time_expression: end time like '16:00'
            until_expression: last day for multi-day like 'august 18'
            participants: comma-separated names
        """
        return "ok"

    @needle.tool
    def move(title: str, date_expression: str = "",
             time_expression: str = "") -> str:
        """Move an existing entry.

        Args:
            title: existing title
            date_expression: new day like 'friday'
            time_expression: new time like '15:00'
        """
        return "ok"

    @needle.tool
    def delete(title: str, date_expression: str = "") -> str:
        """Delete an existing entry.

        Args:
            title: existing title
            date_expression: day like 'last tuesday'
        """
        return "ok"

    @needle.tool
    def list(person: str = "", date_expression: str = "",
             until_expression: str = "") -> str:
        """List entries.

        Args:
            person: name filter
            date_expression: day
            until_expression: last day
        """
        return "ok"

    @needle.tool
    def find(persons: str, duration_min: int = 60,
             date_expression: str = "") -> str:
        """Find common free slots.

        Args:
            persons: comma-separated names
            duration_min: minimum slot length in minutes
            date_expression: day like 'tomorrow afternoon'
        """
        return "ok"

    return {f.__name__: f for f in (create, move, delete, list, find)}.values()


def _resolve_contract_b(args: dict) -> dict:
    """Python resolves symbolic expressions to production handler args."""
    out = dict(args)
    date_expr = out.pop("date_expression", "")
    time_expr = out.pop("time_expression", "")
    until_expr = out.pop("end_time_expression", "")
    until_day = out.pop("until_expression", "")
    if date_expr:
        out["date"] = date_expr
    if time_expr:
        out["time"] = time_expr
    if end_time_expression := until_expr:
        out["end_time"] = end_time_expression
    if until_day:
        out["until"] = until_day
    return out


# contract A also accepts symbolic date expressions (production resolver does);
# the semantic metric is identical for both contracts.
def run_contract(tag: str, fns, cases, repeats=3) -> dict:
    role_names = {f.__name__ for f in fns}
    role_of = {"calendar_create": "create", "create": "create",
               "calendar_move": "move", "move": "move",
               "calendar_delete": "delete", "delete": "delete",
               "calendar_list": "list", "list": "list",
               "calendar_find_slot": "find", "find": "find"}
    reps = []
    for _ in range(repeats):
        store = CalendarStore(Path(tempfile.mkdtemp()) / "c.db")
        agent = needle.Needle(tools=list(fns), system=SYS)
        rows = []
        for case in cases:
            store2 = CalendarStore(Path(tempfile.mkdtemp()) / "case.db")
            for step in case.get("setup") or []:
                execute_call(store2, step["tool"], step["args"])
            inp = case["raw"]
            agent.reset()
            resp = agent.complete(inp)
            calls = resp.get("function_calls") or []
            got = calls[0] if calls else {}
            got_name = got.get("name")
            got_args = got.get("arguments") or {}
            role = case["tool"]
            tool_ok = (role == "off-topic" and not got_name) or (
                got_name is not None and role_of.get(got_name) == role)
            args_ok = False
            if got_name and case["should_execute"]:
                resolved = (_resolve_contract_b(got_args)
                            if tag == "B" else got_args)
                args_ok, _ = _sem_check(resolved, case["args"])
            exec_ok = False
            if got_name and case["should_execute"]:
                resolved = (_resolve_contract_b(got_args) if tag == "B"
                            else got_args)
                handler = {"create": "calendar_create", "move": "calendar_move",
                           "delete": "calendar_delete", "list": "calendar_list",
                           "find": "calendar_find_slot"}[role]
                out = execute_call(store2, handler, resolved, inp)
                exec_ok = bool(out.get("ok"))
                case["_result"] = out.get("message", "")
            final_ok = _final_state(store2, case["final"],
                                    case.get("_result") or "")
            _ = exec_ok
            rows.append({"tool_ok": tool_ok, "args_ok": tool_ok and args_ok,
                         "final_ok": final_ok, "refusal": not got_name,
                         "raw_args": got_args})
        reps.append(rows)
    all_rows = [r for rep in reps for r in rep]
    n = len(all_rows)
    return {"variant": tag, "n_calls": n,
            "tool_ok": round(sum(r["tool_ok"] for r in all_rows) / n, 3),
            "args_ok": round(sum(r["tool_ok"] and r["args_ok"]
                                 for r in all_rows) / n, 3),
            "final_ok": round(sum(r["final_ok"] for r in all_rows) / n, 3),
            "refusals": round(sum(r["refusal"] for r in all_rows) / n, 3)}


def _final_state(store, want: dict, result: str = "") -> bool:
    """Final DB state / response check (plan §E)."""
    if not want:
        return True
    if "result_contains" in want:
        return want["result_contains"].lower() in (result or "").lower()
    events = store.events_between(
        cal.now() - cal.timedelta(days=400), cal.now() + cal.timedelta(days=500))
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


def _sem_check(args: dict, want: dict) -> tuple[bool, list[str]]:
    fails = []
    for key, want_val in want.items():
        got_val = args.get(key)
        if key in ("date", "until"):
            if not _d(got_val, want_val):
                fails.append(key)
        elif key == "time":
            if not _t(got_val, want_val):
                fails.append(key)
        elif key == "title":
            if str(want_val).lower() not in str(got_val or "").lower():
                fails.append(key)
        elif key == "persons":
            pa = {p.lower() for p in cal.parse_persons(got_val or "")}
            pb = {p.lower() for p in cal.parse_persons(want_val)}
            if not pb <= pa:
                fails.append(key)
        else:
            if str(got_val or "").lower() != str(want_val).lower():
                fails.append(key)
    return not fails, fails


def _d(a, b):
    ra = cal.resolve_date(a or "", REF, roll=False)
    rb = cal.resolve_date(b or "", REF, roll=False)
    return (ra == rb) if ra and rb else \
        str(a or "").lower() == str(b or "").lower()


def _t(a, b):
    ta, tb = cal.resolve_time(a or ""), cal.resolve_time(b or "")
    return (ta == tb) if ta and tb else str(a or "").lower() == str(b or "").lower()


def _resolve_contract_b(args: dict) -> dict:
    out = dict(args)
    out["date"] = out.pop("date_expression", "")
    out["time"] = out.pop("time_expression", "")
    out["end_time"] = out.pop("end_time_expression", "")
    out["until"] = out.pop("until_expression", "")
    return out


def main() -> None:
    cases = [c for c in CASES if c["tool"] != "off-topic"]
    reps = 3
    results = {}
    for tag, fns in (("A", _tools_contract_a), ("B", _tools_contract_b)):
        store = CalendarStore(Path(tempfile.mkdtemp()) / "ca.db")
        results[tag] = run_contract(tag, fns(store), cases, reps)
    print(json.dumps(results, ensure_ascii=False, indent=1))
    out = Path(__file__).parent / f"contract_{int(time.time())}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
