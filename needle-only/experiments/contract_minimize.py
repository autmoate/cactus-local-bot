"""Contract-Minimization (letzte Base-Needle-Experimentphase).

Exactly one question: do `horizon` (calendar_list) and `days`
(calendar_find_slot) — internal convenience concepts — cost Needle
argument accuracy?

- Baseline: production contract unchanged (frozen reference).
- Minimal:  calendar_list(person, date, until) and
            calendar_find_slot(persons, duration_min, date, until) —
            horizon/days REMOVED from the needle schema.
- Handlers keep their deterministic defaults (they already do: list falls
  back to horizon=week, find_slot to days=7 when the args are absent —
  no handler changes, no new regex, resolver decides all expressions).
- Everything else frozen: names, order, descriptions, cases, metrics.

Metrics per contract: per-field (date/until/persons/duration_min), tool_ok,
args_ok, final_db_ok, refusals, latency — raw and canonical separately,
plus an isolated subset for horizon/days-adjacent cases (plan §7).
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bakeoff_cases import CASES  # noqa: E402
from local_calendar import calendar as cal  # noqa: E402
from local_calendar.agent import execute_call  # noqa: E402
from local_calendar.calendar import CalendarStore  # noqa: E402

SYS = "date: 2026-09-13 Sun 12:00; locale: de-DE; device: raspberry-pi"
REF = cal.now().date()

SUBSET_IDS = {"ls1", "ls2", "ls3", "ls4", "ls5", "fs1", "fs3", "fs4", "fs5",
              "cr-t1", "cr-t3"}  # horizon/days-adjacent + date-boundary cases

# ------------------------------------------------------------------- schemas


def _tools_contract_a(store):
    from local_calendar.agent import build_tools
    return {fn.__name__: fn for fn in build_tools(store).values()}


def _tools_contract_m(store):
    """Minimal contract: horizon/days removed from the schema — identical
    names, descriptions (minus the removed fields), bodies and order."""
    import needle as _n

    @_n.tool
    def calendar_list(person: str = "", date: str = "", until: str = "") -> str:
        """List calendar entries for a person, a specific day or a date range.

        Args:
            person: participant name to filter by, empty for the user themself
            date: specific day like '7.9.' or 'September 1'; empty uses the default range
            until: last day of a range (inclusive) like 'September 7'
        """
        return "ok"

    @_n.tool
    def calendar_find_slot(persons: str, duration_min: int = 60,
                           date: str = "", until: str = "") -> str:
        """Find common free time slots for a group of persons.

        Args:
            persons: comma-separated participant names
            duration_min: minimum slot length in minutes
            date: first day like 'tomorrow afternoon' or 'September 20'
            until: last day of the search range like 'September 24'; empty with date means one day
        """
        return "ok"

    prod = _tools_contract_a(store)
    return {"calendar_list": calendar_list,
            "calendar_find_slot": calendar_find_slot,
            "calendar_create": prod["calendar_create"],
            "calendar_move": prod["calendar_move"],
            "calendar_delete": prod["calendar_delete"]}


# -------------------------------------------------------------------- metrics

def _sem_date(a: str | None, b: str | None) -> bool:
    ra = cal.resolve_date(str(a or ""), REF, roll=False)
    rb = cal.resolve_date(str(b or ""), REF, roll=False)
    if ra and rb:
        return ra == rb
    return str(a or "").lower() == str(b or "").lower()


def _sem_check(args: dict, want: dict) -> bool:
    for key, want_val in want.items():
        got_val = args.get(key)
        if key in ("date", "until"):
            if not _sem_date(got_val, want_val):
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
            if "all_day" in w and e.all_day != w["all_day"]:
                continue
            if "span_days" in w and (e.end.date() - e.start.date()).days != w["span_days"]:
                continue
            found = True
            break
        if not found:
            return False
    return True


# ------------------------------------------------------------- field metrics

FIELDS = ("date", "until", "persons", "duration_min")


def _field_report(rows, cases_by_id) -> dict:
    out = {f: dict(present_expected=0, present_correct=0, missing=0,
                   wrong_value=0, hallucinated=0) for f in FIELDS}
    for row in rows:
        case = cases_by_id.get(row["id"])
        if not case or case["tool"] == "off-topic":
            continue
        got_args = row.get("args") or {}
        want_args = case.get("args") or {}
        for f in FIELDS:
            expected = want_args.get(f)
            if expected is None:
                if got_args.get(f) not in (None, ""):
                    out[f]["hallucinated"] += 1
                continue
            out[f]["present_expected"] += 1
            got = got_args.get(f)
            if got in (None, ""):
                out[f]["missing"] += 1
                continue
            if f in ("date", "until"):
                ok = _sem_date(got, expected)
            elif f == "persons":
                pg = {p.lower() for p in cal.parse_persons(str(got or ""))}
                pw = {p.lower() for p in cal.parse_persons(str(expected))}
                ok = pw <= pg
            else:
                ok = str(got).strip().lower() == str(expected).strip().lower()
            out[f]["present_correct" if ok else "wrong_value"] += 1
    for f in FIELDS:
        s = out[f]
        s["acc"] = round(s["present_correct"] / s["present_expected"], 3) \
            if s["present_expected"] else None
    return out


# ------------------------------------------------------------------- runners

HANDLERS = {"create": "calendar_create", "move": "calendar_move",
            "delete": "calendar_delete", "list": "calendar_list",
            "find": "calendar_find_slot"}


def run_contract(tag: str, tools: dict, cases, form: str, repeats: int = 3) -> dict:
    all_rows = []
    for _ in range(repeats):
        agent = needle.Needle(tools=list(tools.values()), system=SYS)
        for case in cases:
            store = CalendarStore(Path(tempfile.mkdtemp()) / "case.db")
            for step in case.get("setup") or []:
                execute_call(store, step["tool"], step["args"])
            inp = case["canon"] if form == "canon" and case.get("canon") \
                else case["raw"]
            agent.reset()
            t0 = time.perf_counter()
            resp = agent.complete(inp)
            ms = round((time.perf_counter() - t0) * 1000)
            calls = resp.get("function_calls") or []
            got = calls[0] if calls else {}
            got_name = got.get("name")
            got_args = got.get("arguments") or {}
            role = case["tool"]
            want_name = ("calendar_find_slot" if role == "find"
                         else f"calendar_{role}")
            tool_ok = (got_name == want_name) if role != "off-topic" \
                else not got_name
            args_ok = False
            exec_ok = False
            final_ok = False
            if tool_ok and case["should_execute"] and role != "off-topic":
                args_ok = _sem_check(got_args, case["args"]) \
                    if case["args"] else True
                out = execute_call(store, HANDLERS[role], got_args, inp)
                exec_ok = bool(out.get("ok"))
                final_ok = exec_ok and _final_state(
                    store, case["final"], out.get("message", ""))
            all_rows.append({"id": case["id"], "tool_ok": tool_ok,
                             "args_ok": tool_ok and args_ok,
                             "exec_ok": exec_ok, "final_ok": final_ok,
                             "refusal": not got_name, "ms": ms,
                             "args": got_args})
    n = len(all_rows)
    report = {"contract": tag, "form": form, "repeats": repeats,
              "tool_ok": round(sum(r["tool_ok"] for r in all_rows) / n, 3),
              "args_ok": round(sum(r["args_ok"] for r in all_rows) / n, 3),
              "final_ok": round(sum(r["final_ok"] for r in all_rows) / n, 3),
              "refusals": round(sum(r["refusal"] for r in all_rows) / n, 3),
              "median_ms": round(statistics.median(r["ms"] for r in all_rows)),
              "field": _field_report(all_rows, {c["id"]: c for c in cases})}
    subset = [r for r in all_rows if r["id"] in SUBSET_IDS]
    if subset:
        report["subset_metrics"] = {
            "n": len(subset),
            "tool_ok": round(sum(r["tool_ok"] for r in subset) / len(subset), 3),
            "args_ok": round(sum(r["args_ok"] for r in subset) / len(subset), 3),
            "final_ok": round(sum(r["final_ok"] for r in subset) / len(subset), 3)}
    return report


def main() -> None:
    cases = [c for c in CASES if c["tool"] != "off-topic"]
    store = CalendarStore(Path(tempfile.mkdtemp()) / "init.db")
    results = {}
    for form in ("raw", "canon"):
        tools_a = _tools_contract_a(store)
        tools_m = _tools_contract_m(store)
        results[f"{form}_A"] = run_contract("A", tools_a, cases, form)
        results[f"{form}_M"] = run_contract("M", tools_m, cases, form)
    print(json.dumps(results, ensure_ascii=False, indent=1))
    out = Path(__file__).parent / f"contract_min_{int(time.time())}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
