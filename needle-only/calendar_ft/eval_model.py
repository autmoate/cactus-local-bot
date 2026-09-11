#!/usr/bin/env python3
"""Evaluate a Needle model (base or tuned .cact) against a calendar-FT dataset.

Metrics:
  - tool_call_accuracy       fraction of positives where the expected tool was called
  - full_frame_exact_match   fraction of examples where tool + all arguments match exactly
  - field_precision/recall/f1  micro-averaged over all argument fields
  - field_exact_accuracy     per-field exact-match accuracy
  - hallucinated_field_rate  fraction of emitted argument values NOT present in the query
  - missed_field_rate        fraction of expected fields that were omitted
  - false_positive_tool_rate fraction of negatives where the model emitted a tool call
  - false_negative_tool_rate fraction of positives where the model emitted no tool call
  - latency_ms               mean / p50 / p95 inference latency

Usage:
  python eval_model.py --task calendar_write \
      --dataset data/eval/calendar_write.jsonl \
      --out reports/base_calendar_write.json           # base model
  python eval_model.py --task calendar_write \
      --dataset data/eval/calendar_write.jsonl \
      --weights models/calendar_write.cact \
      --out reports/calendar_write_ft.json             # tuned .cact
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import needle

FT_DIR = Path(__file__).resolve().parent
SCHEMAS = FT_DIR / "schemas"

TASK_TOOL_FILE = {
    "calendar_write": "calendar_write.json",
    "calendar_read": "calendar_read.json",
    "reminder": "reminder.json",
}
TOOL_NAME = {
    "calendar_write": "calendar_write",
    "calendar_read": "calendar_read",
    "reminder": "reminder_parse",
}


def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def call_to_args(call: dict, tool_name: str) -> tuple[str, dict]:
    """Normalise a function_call into (tool_name, arguments)."""
    name = str(call.get("name", ""))
    args = call.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return name, {k: v for k, v in args.items() if v not in (None, "")}


def evaluate(dataset: list[dict], task: str, weights: str | None,
             tool_schema: dict, max_new_tokens: int = 256) -> dict:
    tool = TOOL_NAME[task]
    agent = needle.Needle(tools=[tool_schema], weights=weights)

    n_positive = sum(1 for ex in dataset if ex.get("answers"))
    n_negative = len(dataset) - n_positive

    # Metric accumulators
    tp = 0  # correct field values
    fp = 0  # wrong values or unexpected fields
    fn = 0  # expected fields that were omitted
    tool_calls_correct = 0
    full_frame_matches = 0
    hallucinated_fields = 0
    emitted_fields_total = 0
    missed_fields = 0
    expected_fields_total = 0
    false_positive_tool_calls = 0  # negatives where model emitted a tool call
    false_negative_no_calls = 0    # positives where model emitted no tool call
    field_exact = {}  # field -> [correct, total_expected]
    per_field_errors = {}

    latencies_ms = []
    worst_cases = []
    errors_jsonl = []

    for idx, ex in enumerate(dataset):
        query = ex["query"]
        expected_answers = ex.get("answers", [])
        is_positive = bool(expected_answers)

        t0 = time.time()
        try:
            agent.reset()
            response = agent.complete(query, max_new_tokens=max_new_tokens)
        except Exception as exc:
            response = {"error": str(exc), "function_calls": []}
        latency_ms = (time.time() - t0) * 1000.0
        latencies_ms.append(latency_ms)

        calls = response.get("function_calls") or []
        got_args = {}
        got_tool = None
        if calls:
            got_tool, got_args = call_to_args(calls[0], tool)

        # Default per-example outcome
        tool_ok = False
        frame_ok = False
        reason = ""

        if is_positive:
            expected_args = expected_answers[0].get("arguments", {})
            expected_tool = expected_answers[0].get("name", tool)
            expected_fields_total += len(expected_args)

            # Tool call accuracy: did the model call the expected tool?
            tool_ok = (got_tool == expected_tool and bool(calls))
            if tool_ok:
                tool_calls_correct += 1

            # No-call on positive = false negative
            if not calls:
                false_negative_no_calls += 1
                reason = "no tool call on positive"

            # Field-level metrics
            for field, expected_value in expected_args.items():
                got_value = got_args.get(field)
                correct = (got_value is not None and
                           str(got_value).strip().lower() == str(expected_value).strip().lower())
                field_exact.setdefault(field, [0, 0])
                field_exact[field][1] += 1
                if correct:
                    tp += 1
                    field_exact[field][0] += 1
                else:
                    fn += 1
                    per_field_errors.setdefault(field, []).append(
                        {"example": idx, "query": query, "expected": expected_value,
                         "got": got_value})

            # Extra / hallucinated fields
            for field, got_value in got_args.items():
                emitted_fields_total += 1
                if field not in expected_args:
                    fp += 1
                    reason = (reason + ";" if reason else "") + f"unexpected field {field}"
                # Grounding check: is the value a substring of the query?
                if str(got_value) not in query:
                    hallucinated_fields += 1

            # Missed fields
            missed = [f for f in expected_args if f not in got_args]
            missed_fields += len(missed)
            if missed:
                reason = (reason + ";" if reason else "") + f"missing fields {missed}"

            # Full frame exact match
            frame_ok = (got_tool == expected_tool and
                        {k: str(v).strip().lower() for k, v in got_args.items()} ==
                        {k: str(v).strip().lower() for k, v in expected_args.items()})
            if frame_ok:
                full_frame_matches += 1
        else:
            # Negative example: model should NOT call a tool
            if calls:
                false_positive_tool_calls += 1
                reason = f"tool call on negative: {calls[0].get('name')}"
            else:
                full_frame_matches += 1  # correct refusal counts as full-frame match

        # Track worst cases
        if is_positive and not frame_ok:
            worst_cases.append({
                "index": idx,
                "query": query,
                "expected": expected_answers[0],
                "got": {"tool": got_tool, "arguments": got_args},
                "latency_ms": latency_ms,
            })
            errors_jsonl.append({
                "query": query,
                "expected_arguments": expected_answers[0].get("arguments", {}),
                "got_arguments": got_args,
                "got_tool": got_tool,
                "reason": reason,
            })

    agent.close()

    # Compute aggregate metrics
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) > 0 else 0.0)

    lat_sorted = sorted(latencies_ms)
    p50 = lat_sorted[len(lat_sorted) // 2] if lat_sorted else 0
    p95 = lat_sorted[int(len(lat_sorted) * 0.95)] if lat_sorted else 0

    per_field = {}
    for field, (correct, total) in field_exact.items():
        per_field[field] = {
            "exact_accuracy": correct / total if total else 0.0,
            "support": total,
        }

    # Destructive-action check: any cancel/delete verb emitted on a non-cancel query?
    metrics = {
        "task": task,
        "weights": weights,
        "n_examples": len(dataset),
        "n_positive": n_positive,
        "n_negative": n_negative,
        "tool_call_accuracy": tool_calls_correct / n_positive if n_positive else 0.0,
        "full_frame_exact_match": full_frame_matches / len(dataset) if dataset else 0.0,
        "field_precision": precision,
        "field_recall": recall,
        "field_f1": f1,
        "field_exact_accuracy": {k: v["exact_accuracy"] for k, v in per_field.items()},
        "per_field_support": {k: v["support"] for k, v in per_field.items()},
        "hallucinated_field_rate": (hallucinated_fields / emitted_fields_total
                                    if emitted_fields_total else 0.0),
        "missed_field_rate": (missed_fields / expected_fields_total
                              if expected_fields_total else 0.0),
        "false_positive_tool_rate": (false_positive_tool_calls / n_negative
                                     if n_negative else 0.0),
        "false_negative_tool_rate": (false_negative_no_calls / n_positive
                                     if n_positive else 0.0),
        "latency_ms": {
            "mean": sum(latencies_ms) / len(latencies_ms) if latencies_ms else 0,
            "p50": p50,
            "p95": p95,
        },
        "per_field": per_field,
    }

    return metrics, worst_cases, errors_jsonl


def main():
    ap = argparse.ArgumentParser(description="Calendar-FT model evaluation")
    ap.add_argument("--task", required=True,
                    choices=["calendar_write", "calendar_read", "reminder"])
    ap.add_argument("--dataset", required=True, help="JSONL eval dataset path")
    ap.add_argument("--weights", default=None,
                    help="Optional .cact weights path (omit for base model)")
    ap.add_argument("--out", required=True, help="Output JSON report path")
    args = ap.parse_args()

    dataset = load_jsonl(args.dataset)
    if not dataset:
        raise SystemExit(f"empty dataset: {args.dataset}")

    tool_schema = json.loads((SCHEMAS / TASK_TOOL_FILE[args.task]).read_text())

    metrics, worst_cases, errors_jsonl = evaluate(
        dataset, args.task, args.weights, tool_schema)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as handle:
        json.dump(metrics, handle, indent=2, ensure_ascii=False)

    # Also write worst cases for error analysis
    stem = out_path.stem
    worst_path = out_path.parent / f"{stem}_worst_cases.json"
    with open(worst_path, "w") as handle:
        json.dump(worst_cases, handle, indent=2, ensure_ascii=False)

    errors_path = out_path.parent / f"{stem}_errors.jsonl"
    with open(errors_path, "w") as handle:
        for err in errors_jsonl:
            handle.write(json.dumps(err, ensure_ascii=False) + "\n")

    # Print summary
    print(f"\nEvaluation: task={args.task} weights={args.weights or 'BASE'}")
    print(f"  examples: {metrics['n_examples']} "
          f"(pos={metrics['n_positive']}, neg={metrics['n_negative']})")
    print(f"  tool_call_accuracy:      {metrics['tool_call_accuracy']:.4f}")
    print(f"  full_frame_exact_match:  {metrics['full_frame_exact_match']:.4f}")
    print(f"  field_precision:        {metrics['field_precision']:.4f}")
    print(f"  field_recall:           {metrics['field_recall']:.4f}")
    print(f"  field_f1:               {metrics['field_f1']:.4f}")
    print(f"  hallucinated_field_rate:{metrics['hallucinated_field_rate']:.4f}")
    print(f"  missed_field_rate:      {metrics['missed_field_rate']:.4f}")
    print(f"  false_positive_tool_rate:{metrics['false_positive_tool_rate']:.4f}")
    print(f"  false_negative_tool_rate:{metrics['false_negative_tool_rate']:.4f}")
    print(f"  latency_ms (mean/p50/p95): "
          f"{metrics['latency_ms']['mean']:.0f}/"
          f"{metrics['latency_ms']['p50']:.0f}/"
          f"{metrics['latency_ms']['p95']:.0f}")
    print(f"\nReport written to {args.out}")
    print(f"Worst cases: {worst_path}")
    print(f"Errors JSONL: {errors_path}")


if __name__ == "__main__":
    main()
