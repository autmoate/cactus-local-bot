"""Metrics + failure taxonomy for the command_ir spike (§13)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from compiler import norm

TAXONOMY = ("MODEL_TOOL", "MODEL_SPAN", "MODEL_OMISSION", "MODEL_EXTRA",
            "COMPILER_TIME", "COMPILER_TARGET", "AMBIGUOUS", "DOMAIN",
            "EXECUTION")


def span_grounding(calls: list[dict], query: str) -> tuple[int, int, list[str]]:
    """Evidence-only check (§4): every non-empty string arg must be a substring
    of the user text (whitespace/punctuation/case tolerant)."""
    nq = norm(query)
    grounded = total = 0
    bad: list[str] = []
    for call in calls or []:
        for key, val in (call.get("arguments") or {}).items():
            if not isinstance(val, str) or not val.strip():
                continue
            total += 1
            if norm(val) and (norm(val) in nq or nq in norm(val)):
                grounded += 1
            else:
                bad.append(f"{call.get('name')}.{key}={val!r}")
    return grounded, total, bad


def tools_of(calls) -> Counter:
    return Counter(c.get("name") for c in (calls or []))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vs = sorted(values)
    k = max(0, min(len(vs) - 1, int(round((p / 100) * (len(vs) - 1)))))
    return vs[k]


class Tally:
    """Accumulates per-family metrics and a failure taxonomy."""

    def __init__(self):
        self.rows: dict[str, dict] = defaultdict(
            lambda: {"n": 0, "tool_ok": 0, "grounded": 0, "span_total": 0,
                     "compile_ok": 0, "final_ok": 0, "write_cases": 0,
                     "wrong_mutation": 0, "ambiguous_ok": 0,
                     "ambiguous_total": 0, "notfound_ok": 0,
                     "notfound_total": 0, "refusal_ok": 0, "refusal_total": 0,
                     "ops_expected": 0, "ops_pred": 0, "all_intents": 0,
                     "silent_omission": 0, "lat": []})
        self.taxonomy: Counter = Counter()
        self.failures: list[dict] = []

    def add(self, family: str, m: dict):
        r = self.rows[family]
        r["n"] += 1
        for key in ("tool_ok", "grounded", "span_total", "compile_ok", "final_ok",
                    "wrong_mutation", "ambiguous_ok", "ambiguous_total",
                    "notfound_ok", "notfound_total", "refusal_ok",
                    "refusal_total", "ops_expected", "ops_pred", "all_intents",
                    "silent_omission"):
            r[key] += int(m.get(key, 0))
        if "latency_ms" in m:
            r["lat"].append(m["latency_ms"])
        if m.get("taxonomy"):
            self.taxonomy[m["taxonomy"]] += 1
        if m.get("failure"):
            self.failures.append(m["failure"])

    def report(self) -> dict:
        out = {}
        for family, r in sorted(self.rows.items()):
            n = max(r["n"], 1)
            out[family] = {
                "n": r["n"],
                "tool_ok": round(r["tool_ok"] / n, 3),
                "compile_ok": round(r["compile_ok"] / n, 3),
                "final_ok": round(r["final_ok"] / n, 3),
                "span_grounding": round(r["grounded"] / max(r["span_total"], 1), 3),
                "wrong_mutation": r["wrong_mutation"],
                "ambiguous_correct": round(r["ambiguous_ok"] /
                                           max(r["ambiguous_total"], 1), 3),
                "not_found_correct": round(r["notfound_ok"] /
                                           max(r["notfound_total"], 1), 3),
                "correct_refusal_rate": round(r["refusal_ok"] /
                                              max(r["refusal_total"], 1), 3),
                "ops_expected": r["ops_expected"],
                "ops_pred": r["ops_pred"],
                "all_intents_covered": round(r["all_intents"] / n, 3),
                "silent_omission": r["silent_omission"],
                "latency_p50": round(percentile(r["lat"], 50), 1),
                "latency_p95": round(percentile(r["lat"], 95), 1),
            }
        return {"families": out, "taxonomy": dict(self.taxonomy),
                "failures": self.failures[:40]}
