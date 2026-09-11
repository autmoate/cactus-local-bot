#!/usr/bin/env python3
"""Compare base vs fine-tuned Needle evaluation reports.

Usage:
  python compare_runs.py reports/base_calendar_write.json reports/calendar_write_ft.json
  python compare_runs.py reports/ --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

METRIC_KEYS = [
    "tool_call_accuracy",
    "full_frame_exact_match",
    "field_precision",
    "field_recall",
    "field_f1",
    "hallucinated_field_rate",
    "missed_field_rate",
    "false_positive_tool_rate",
    "false_negative_tool_rate",
]


def load_report(path: str) -> dict:
    with open(path) as handle:
        return json.load(handle)


def format_comparison(base: dict, ft: dict, task: str) -> str:
    """Format a single base-vs-FT comparison as markdown."""
    lines = [f"## {task}", ""]
    lines.append("| Metric | Base | FT | Δ |")
    lines.append("|--------|------|-----|---|")

    for key in METRIC_KEYS:
        base_val = base.get(key, 0.0)
        ft_val = ft.get(key, 0.0)
        delta = ft_val - base_val
        arrow = "↑" if delta > 0.005 else ("↓" if delta < -0.005 else "→")
        lines.append(f"| {key} | {base_val:.4f} | {ft_val:.4f} | {arrow} {delta:+.4f} |")

    # Latency comparison
    base_lat = base.get("latency_ms", {})
    ft_lat = ft.get("latency_ms", {})
    lines.append("")
    lines.append("| Latency | Base (ms) | FT (ms) |")
    lines.append("|---------|-----------|---------|")
    for stat in ["mean", "p50", "p95"]:
        base_v = base_lat.get(stat, 0)
        ft_v = ft_lat.get(stat, 0)
        lines.append(f"| {stat} | {base_v:.0f} | {ft_v:.0f} |")

    # Per-field accuracy comparison
    lines.append("")
    lines.append("### Per-field exact accuracy")
    lines.append("")
    lines.append("| Field | Base | FT | Δ |")
    lines.append("|-------|------|-----|---|")
    fields = sorted(set(base.get("field_exact_accuracy", {}).keys()) |
                    set(ft.get("field_exact_accuracy", {}).keys()))
    for field in fields:
        base_v = base.get("field_exact_accuracy", {}).get(field, 0.0)
        ft_v = ft.get("field_exact_accuracy", {}).get(field, 0.0)
        delta = ft_v - base_v
        lines.append(f"| {field} | {base_v:.4f} | {ft_v:.4f} | {delta:+.4f} |")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Compare base vs FT evaluation reports")
    ap.add_argument("base_report", help="Path to base evaluation report JSON")
    ap.add_argument("ft_report", help="Path to FT evaluation report JSON")
    ap.add_argument("--out", default=None, help="Output markdown path")
    args = ap.parse_args()

    base = load_report(args.base_report)
    ft = load_report(args.ft_report)
    task = base.get("task", "unknown")

    comparison_md = format_comparison(base, ft, task)

    print(comparison_md)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as handle:
            handle.write(comparison_md)
        print(f"\nComparison written to {args.out}")


if __name__ == "__main__":
    main()
