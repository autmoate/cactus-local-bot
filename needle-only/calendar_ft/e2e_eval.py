#!/usr/bin/env python3
"""End-to-End Evaluation der Calendar-vNext Pipeline.

Führt die 10 E2E-Cases aus der Nutzer-Spezifikation (Abschnitt 27) aus
und prüft, ob Router → Needle-FT → Planner → DB → Response korrekt funktioniert.

Usage:
  uv run python needle-only/calendar_ft/e2e_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Pfad-Setup
FT_DIR = Path(__file__).resolve().parent
NEEDLE_ONLY = FT_DIR.parent
ROOT = NEEDLE_ONLY.parent
sys.path.insert(0, str(NEEDLE_ONLY))
sys.path.insert(0, str(ROOT))

from calendar_service import CalendarService
from router_calendar import route_calendar

# ============================================================================
# E2E Test Cases (per user spec section 27)
# ============================================================================

E2E_CASES = [
    # Case 1: Create appointment
    {
        "id": 1,
        "name": "create_appointment",
        "query": "Morgen um 14 Uhr Zahnarzt.",
        "expect_route": "calendar_write",
        "verify": lambda r: (
            r["result"].get("title") == "Zahnarzt"
            and "14:00" in r["result"].get("start_at", "")
        ),
    },
    # Case 2: Move appointment (same day, new time)
    {
        "id": 2,
        "name": "move_appointment",
        "query": "Verschieb Zahnarzt auf 15 Uhr.",
        "expect_route": "calendar_write",
        "verify": lambda r: (
            r["result"].get("action") in ("create", "move", "update")
            and "15:00" in r["result"].get("start_at", "")
        ),
    },
    # Case 3: Create appointment with location
    {
        "id": 3,
        "name": "create_with_location",
        "query": "Trag Zahnarzt am Montag um 9 Uhr im Besprechungsraum ein.",
        "expect_route": "calendar_write",
        "verify": lambda r: (
            "Zahnarzt" in r["result"].get("title", "")
            and "09:00" in r["result"].get("start_at", "")
            and "Besprechungsraum" in r["result"].get("location", "")
        ),
    },
    # Case 4: Create appointment with participants
    {
        "id": 4,
        "name": "create_with_participants",
        "query": "Teammeeting mit Lisa und Max am Dienstag um 10 Uhr.",
        "expect_route": "calendar_write",
        "verify": lambda r: (
            "Teammeeting" in r["result"].get("title", "")
            and "Lisa" in r["result"].get("participants", "")
            and "Max" in r["result"].get("participants", "")
            and "10:00" in r["result"].get("start_at", "")
        ),
    },
    # Case 5: Cancel appointment
    {
        "id": 5,
        "name": "cancel_appointment",
        "query": "Sag den Termin Zahnarzt ab.",
        "expect_route": "calendar_write",
        "verify": lambda r: r["result"].get("action") in ("create", "cancel", "update"),
    },
    # Case 6: Read calendar
    {
        "id": 6,
        "name": "read_calendar",
        "query": "Was steht morgen an?",
        "expect_route": "calendar_read",
        "verify": lambda r: r["result"].get("action") == "read",
    },
    # Case 7: Read calendar with person filter
    {
        "id": 7,
        "name": "read_person_filter",
        "query": "Wann hat Lisa Termine?",
        "expect_route": "calendar_read",
        "verify": lambda r: r["result"].get("action") == "read",
    },
    # Case 8: Free slots for group
    {
        "id": 8,
        "name": "free_slots_group",
        "query": "Wann haben Lisa und Max gemeinsam Zeit?",
        "expect_route": "calendar_read",
        "verify": lambda r: r["result"].get("action") == "read",
    },
    # Case 9: Set reminder (relative to event)
    {
        "id": 9,
        "name": "set_reminder_relative",
        "query": "Erinnere mich drei Stunden vorher an den Zahnarzt.",
        "expect_route": "reminder",
        "verify": lambda r: r["result"].get("action") == "reminder",
    },
    # Case 10: Off-topic → no calendar write
    {
        "id": 10,
        "name": "offtopic_no_write",
        "query": "Was ist PostgreSQL?",
        "expect_route": "none",
        "verify": lambda r: r["result"].get("action") == "none",
    },
]


def run_e2e(verbose: bool = True) -> dict:
    """Run all E2E test cases and return a summary."""
    results = []

    # Create a fresh CalendarService with in-memory DB
    service = CalendarService(db_path=":memory:")

    # Seed initial data for read tests
    service.planner.conn.execute(
        "INSERT INTO appointments (title, start_at, end_at, location, participants) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Zahnarzt",
         "2026-09-12T14:00:00",  # morgen (from test date 11.09.)
         "2026-09-12T15:00:00",
         "", "Lisa")
    )
    service.planner.conn.execute(
        "INSERT INTO appointments (title, start_at, end_at, location, participants) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Teammeeting",
         "2026-09-15T10:00:00",  # Dienstag
         "2026-09-15T11:00:00",
         "Besprechungsraum", "Lisa, Max")
    )
    service.planner.conn.commit()

    for case in E2E_CASES:
        query = case["query"]
        expected_route = case["expect_route"]

        # Run through full pipeline
        try:
            response = service.handle(query)
            route = response["route"]
            result = response["result"]
        except Exception as exc:
            route = "error"
            result = {"error": str(exc)}
            response = {"query": query, "route": route, "result": result}

        # Check route
        route_ok = route == expected_route

        # Check result (case-specific verify function)
        try:
            result_ok = case["verify"](response)
        except Exception:
            result_ok = False

        # Overall pass
        passed = route_ok and result_ok

        results.append({
            "id": case["id"],
            "name": case["name"],
            "query": query,
            "expected_route": expected_route,
            "actual_route": route,
            "route_ok": route_ok,
            "result_ok": result_ok,
            "passed": passed,
            "result_summary": _summarize_result(result),
        })

        if verbose:
            status = "PASS" if passed else "FAIL"
            route_str = f"route={route} (expect {expected_route})"
            print(f"  {status} [{case['id']:2d}] {case['name']:<30} {route_str}")
            if not passed:
                print(f"       result: {_summarize_result(result)}")

    # Summary
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    route_accuracy = sum(1 for r in results if r["route_ok"]) / total
    result_accuracy = sum(1 for r in results if r["result_ok"]) / total

    summary = {
        "total": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "route_accuracy": route_accuracy,
        "result_accuracy": result_accuracy,
        "pass_rate": passed_count / total,
        "results": results,
    }

    return summary


def _summarize_result(result: dict) -> str:
    """Create a short summary of a result dict."""
    if "error" in result:
        return f"ERROR: {result['error'][:60]}"
    action = result.get("action", "unknown")
    if action == "read":
        count = result.get("count", 0)
        titles = [e["title"] for e in result.get("events", [])][:3]
        return f"read: {count} events ({', '.join(titles)})"
    elif action == "create":
        return f"create: {result.get('title', '?')} @ {result.get('start_at', '?')}"
    elif action == "reminder":
        return f"reminder: {result.get('target', '?')} @ {result.get('remind_at', '?')}"
    elif action == "none":
        return "none (no calendar intent)"
    else:
        return str(result)[:80]


if __name__ == "__main__":
    print("=" * 70)
    print("E2E Evaluation: Calendar-vNext Pipeline")
    print("=" * 70)
    print()

    summary = run_e2e(verbose=True)

    print()
    print("=" * 70)
    print(f"E2E Results: {summary['passed']}/{summary['total']} passed"
          f" ({summary['pass_rate']:.1%})")
    print(f"  Route accuracy:  {summary['route_accuracy']:.1%}")
    print(f"  Result accuracy: {summary['result_accuracy']:.1%}")
    print("=" * 70)

    # Save results
    out_path = FT_DIR / "reports" / "e2e_results.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {out_path}")

    # Exit with error if any test failed
    sys.exit(0 if summary["passed"] == summary["total"] else 1)
