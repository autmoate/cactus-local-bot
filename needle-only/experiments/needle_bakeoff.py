"""Needle Environment Bakeoff (plan §6-16): measure Needle variants against
the same case set. No production changes, no post-hoc fixes — we measure
Needle as intended: tool design, complete(), raw scores.

Metrics (plan §15/§22): raw tool-call correctness vs final domain correctness
are kept apart; deterministic temporal resolution is reported separately.

Usage: uv run python experiments/needle_bakeoff.py
"""

import json
import statistics
import sys
import time
from pathlib import Path

import needle

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

CASES = [
    # (input, semantic tool, required argument pairs)
    ("Create an appointment tomorrow at 14:00",
     "create", {"date": "tomorrow", "time": "14:00"}),
    ("Termin Zahnarzt morgen um 14 Uhr",
     "create", {"date": "tomorrow", "time": "14:00"}),
    ("Move my dentist appointment to Friday",
     "move", {"date": "friday"}),
    ("Lösch den Zahnarzttermin.",
     "delete", {}),
    ("Show my appointments next week",
     "list", {}),
    ("I am on vacation from August 3 to August 18",
     "create", {"date": "august 3", "until": "august 18"}),
    ("When are Lisa and Max free tomorrow afternoon?",
     "find", {"persons": "Lisa", "date": "tomorrow afternoon"}),
    ("Meeting am 17.9. von 13 bis 16 Uhr",
     "create", {"date": "17.9.", "time": "13:00", "end_time": "16:00"}),
]

# ---------------------------------------------------------------- tool variants
# (semantic_role, name, description, [(param, description)])

TOOLS_CURRENT = [
    ("create", "calendar_create",
     "Create a calendar entry or all-day absence (vacation, trip).",
     [("title", "short entry title"),
      ("date", "first day like 'tomorrow' or 'august 3'"),
      ("until", "last day for multi-day absences; empty for single day"),
      ("time", "time of day like '14:00'; empty for all-day absences"),
      ("end_time", "end time of day like '16:00'"),
      ("participants", "comma-separated participant names")]),
    ("move", "calendar_move", "Move an existing calendar entry.",
     [("title", "existing entry title"), ("date", "new day like 'friday'"),
      ("time", "new time like '15:00'")]),
    ("delete", "calendar_delete", "Delete an existing calendar entry.",
     [("title", "title of the entry to delete"), ("date", "optional day hint")]),
    ("list", "calendar_list", "List calendar entries.",
     [("person", "name filter"), ("date", "specific day like '7.9.'"),
      ("until", "last day of a range"),
      ("horizon", "'today', 'week' or 'month'")]),
    ("find", "calendar_find_slot", "Find common free slots for persons.",
     [("persons", "comma-separated names"),
      ("duration_min", "minimum slot length in minutes"),
      ("date", "day like 'tomorrow afternoon'"),
      ("until", "last day of the range"),
      ("days", "search horizon in days")]),
]

TOOLS_SHORT = [
    ("create", "create", "Create a calendar entry.",
     [("title", "title"), ("date", "day"), ("until", "last day"),
      ("time", "time of day"), ("end_time", "end time"), ("participants", "names")]),
    ("move", "move", "Move an entry.",
     [("title", "title"), ("date", "new day"), ("time", "new time")]),
    ("delete", "delete", "Delete an entry.",
     [("title", "title"), ("date", "day")]),
    ("list", "list", "List entries.",
     [("person", "name"), ("date", "day"), ("until", "last day")]),
    ("find", "find", "Find free slots.",
     [("persons", "names"), ("duration_min", "minutes"),
      ("date", "day"), ("until", "last day"), ("days", "horizon days")]),
]

TOOLS_SPLIT = [
    ("create", "create_event", "Create an appointment (timed).",
     [("title", "short title"), ("date", "day like 'tomorrow'"),
      ("time", "start time like '14:00'"),
      ("end_time", "end time like '16:00'"), ("participants", "names")]),
    ("create", "create_absence",
     "Create an all-day absence (vacation, sick day, trip).",
     [("title", "short title"), ("date", "first day like 'august 3'"),
      ("until", "last day like 'august 18'"), ("participants", "names")]),
    ("move", "move_event", "Move an existing appointment.",
     [("title", "existing title"), ("date", "new day"), ("time", "new time")]),
    ("delete", "delete_event", "Delete an existing entry.",
     [("title", "title"), ("date", "day")]),
    ("list", "list_events", "List entries.",
     [("person", "name"), ("date", "day"), ("until", "last day")]),
    ("find", "find_free_slots", "Find free slots for persons.",
     [("persons", "names"), ("duration_min", "minutes"), ("date", "day"),
      ("until", "last day"), ("days", "horizon days")]),
]


def _schemas(tools, order=None):
    seq = tools if order is None else sorted(tools, key=lambda t: order.index(t[1]))
    out = []
    for _role, name, desc, params in seq:
        props = {p: {"type": "string", "description": d} for p, d in params}
        out.append({"name": name, "description": desc,
                    "parameters": {"type": "object", "properties": props}})
    return out


def _roles(tools):
    return {name: role for role, name, *_ in tools}


VARIANTS = {
    "A": {"label": "current calendar-*", "tools": TOOLS_CURRENT},
    "B": {"label": "short verbs (create/delete/...)", "tools": TOOLS_SHORT},
    "C": {"label": "split create_event/create_absence", "tools": TOOLS_SPLIT},
}


def run_variant(tag: str, tools, cases, order=None):
    schemas = _schemas(tools, order)
    roles = _roles(tools)
    agent = needle.Needle(tools=schemas,
                          system="date: 2026-09-13 Sun 12:00; locale: de-DE; "
                                 "device: raspberry-pi")
    rows = []
    for inp, want_role, want_args in cases:
        agent.reset()
        t0 = time.perf_counter()
        resp = agent.complete(inp)
        ms = round((time.perf_counter() - t0) * 1000)
        calls = resp.get("function_calls") or []
        got = calls[0] if calls else {}
        got_name, got_args = got.get("name"), got.get("arguments") or {}
        tool_ok = roles.get(got_name) == want_role
        args_ok = all(str(got_args.get(k, "")).strip().lower()
                      == str(v).strip().lower()
                      for k, v in want_args.items())
        rows.append({"input": inp, "tool": got_name, "args": got_args,
                     "tool_ok": tool_ok,
                     "args_ok": tool_ok and args_ok, "ms": ms,
                     "conf": resp.get("confidence")})
    n = len(rows)
    return {"variant": tag,
            "tool_acc": round(sum(r["tool_ok"] for r in rows) / n, 2),
            "args_acc": round(sum(r["tool_ok"] and r["args_ok"] for r in rows) / n, 3),
            "refusals": sum(1 for r in rows if not r["tool"]),
            "median_conf": round(statistics.median([r["conf"] or 0 for r in rows]), 3),
            "median_ms": round(statistics.median([r["ms"] for r in rows])),
            "rows": rows}


ORDERS = {
    "O1 create-first": ["calendar_create", "calendar_move", "calendar_delete",
                        "calendar_list", "calendar_find_slot"],
    "O2 list-first": ["calendar_list", "calendar_find_slot", "calendar_create",
                      "calendar_move", "calendar_delete"],
}


def main() -> None:
    results = []
    for tag, spec in VARIANTS.items():
        if tag == "A":
            for otag, order in ORDERS.items():
                results.append(run_variant(f"{tag}+{otag}", spec["tools"],
                                           CASES, order))
        else:
            results.append(run_variant(tag, spec["tools"], CASES))
    print(json.dumps([{k: v for k, v in r.items() if k != "rows"}
                      for r in results], ensure_ascii=False, indent=1))
    out = Path(__file__).parent / f"bakeoff_{int(time.time())}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
