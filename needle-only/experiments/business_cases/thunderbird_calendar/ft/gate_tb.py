#!/usr/bin/env python3
"""Promotion gate for the Thunderbird calendar FT (FT_PLAN.md §9).

Reads eval reports and enforces the pre-registered gate. Exit code != 0 if any
criterion fails. The hard set must improve clearly over Base, not just the
synthetic test.

Usage:
  cd needle-only && uv run python \
    experiments/business_cases/thunderbird_calendar/ft/gate_tb.py \
      --ft-realism ft/reports/<ft>_realism.json \
      --ft-hard    ft/reports/<ft>_hard.json \
      --base-hard  ft/reports/n2_hard.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

THRESH = {
    "supported_final_event_ok": 0.85,
    "selection_final_event_ok": 0.90,
    "false_positive_rate": 0.05,
    "near_miss_fp_by_category": 0.05,
    "supported_approval_ready": 0.80,
    "review_routing_ok": 0.90,
    "evidence_grounded_rate": 0.95,
    "hard_improvement_over_base": 0.10,
}


def group(report: dict, mode: str) -> dict:
    return report.get("groups", {}).get(mode, {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ft-realism", required=True)
    ap.add_argument("--ft-hard", required=True)
    ap.add_argument("--base-hard", default=None)
    ap.add_argument("--base-realism", default=None)
    args = ap.parse_args()

    ft = json.loads(Path(args.ft_realism).read_text())
    fth = json.loads(Path(args.ft_hard).read_text())
    base_h = json.loads(Path(args.base_hard).read_text()) if args.base_hard else {}
    base_r = json.loads(Path(args.base_realism).read_text()) if args.base_realism else {}
    msg, sel = group(ft, "message"), group(ft, "selection")
    hmsg = group(fth, "message")

    def show(label, value, threshold, ok):
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label:34} {value:,.3f} (need "
              f"{'>=' if 'min' not in label else ''}{threshold:.2f})")

    checks: list[tuple[bool, str, float, float]] = []
    checks.append((msg.get("supported_final_event_ok", 0) >=
                   THRESH["supported_final_event_ok"],
                   "message supported_final_event_ok",
                   msg.get("supported_final_event_ok", 0),
                   THRESH["supported_final_event_ok"]))
    checks.append((sel.get("final_event_ok", 0) >= THRESH["selection_final_event_ok"],
                   "selection final_event_ok", sel.get("final_event_ok", 0),
                   THRESH["selection_final_event_ok"]))
    checks.append((msg.get("false_positive_rate", 1) <= THRESH["false_positive_rate"],
                   "message false_positive_rate", msg.get("false_positive_rate", 1),
                   THRESH["false_positive_rate"]))
    checks.append((msg.get("supported_approval_ready", 0) >=
                   THRESH["supported_approval_ready"],
                   "message supported_approval_ready",
                   msg.get("supported_approval_ready", 0),
                   THRESH["supported_approval_ready"]))
    checks.append((msg.get("review_routing_ok", 0) >= THRESH["review_routing_ok"],
                   "message review_routing_ok", msg.get("review_routing_ok", 0),
                   THRESH["review_routing_ok"]))
    checks.append((msg.get("evidence_grounded_rate", 0) >=
                   THRESH["evidence_grounded_rate"],
                   "message evidence_grounded_rate",
                   msg.get("evidence_grounded_rate", 0),
                   THRESH["evidence_grounded_rate"]))

    for cat, fp in (msg.get("false_positive_by_category") or {}).items():
        checks.append((fp <= THRESH["near_miss_fp_by_category"],
                       f"near-miss FP [{cat}]", fp,
                       THRESH["near_miss_fp_by_category"]))

    base_hard = group(base_h, "message").get("supported_final_event_ok", 0)
    ft_hard = hmsg.get("supported_final_event_ok", 0)
    checks.append((ft_hard >= base_hard + THRESH["hard_improvement_over_base"],
                   "hard supported_final vs base",
                   ft_hard, base_hard + THRESH["hard_improvement_over_base"]))

    print("=== Thunderbird FT gate ===")
    for ok, label, value, thr in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:34} {value:,.3f} "
              f"(need {thr:.2f})")
    passed = all(c[0] for c in checks)
    print(f"\nResult: {'GO' if passed else 'NO-GO'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
