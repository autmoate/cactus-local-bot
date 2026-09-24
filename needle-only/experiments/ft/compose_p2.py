#!/usr/bin/env python3
"""Compose P2 offline: N3-autonomous → N3 result; N3-escalated → P0 result.

Why (user review): N2-FT and N3-E5 are bound to different Needle runtimes
(2.0.13 vs 3.0.4). Instead of forcing both engines into one process, run
P1/P2 with N3 (--pipeline fallback --no-gemma) and P0 with Gemma→N2, then
compose per case-id. This yields the actual logical quality of
"N3 → on escalation Gemma→N2" without engine coupling.

Usage:
  PYTHONPATH=src uv run python experiments/ft/compose_p2.py \
      --p0 reports/arch_n2ft-p0-pi_hybrid.json \
      --n3 reports/arch_n3e5-p2-pi_fallback.json \
      --tag n3e5-p2-pi
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _fix(path: str) -> Path:
    p = Path(path)
    return p if p.exists() else HERE / "reports" / p.name


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--p0", required=True)
    ap.add_argument("--n3", required=True)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    p0 = json.loads(_fix(args.p0).read_text())
    n3 = json.loads(_fix(args.n3).read_text())
    p0_by_id = {r["id"]: r for r in p0["rows"]}

    rows = []
    for r in n3["rows"]:
        if r["escalated"]:
            p = p0_by_id.get(r["id"])
            rows.append({
                "id": r["id"], "family": r["family"], "source": "P0",
                "goal_ok": bool(p and p["goal_ok"]),
                "wrong_mutations": (p or {}).get("wrong_mutations", 0),
                "escalated": True,
                "gemma_used": bool(p and p.get("gemma_used")),
                "ms": r["ms"] + ((p or {}).get("ms", 0)),
                "why": f"n3_escalated({r['esc_reason']}) → p0: "
                       f"{(p or {}).get('why', 'missing')}",
            })
        else:
            rows.append({"id": r["id"], "family": r["family"], "source": "N3",
                         "goal_ok": r["goal_ok"],
                         "wrong_mutations": r["wrong_mutations"],
                         "escalated": False, "gemma_used": False,
                         "ms": r["ms"], "why": r["why"]})
    n = len(rows)

    def pct(sel):
        return round(sum(1 for r in rows if sel(r)) / n, 3)

    lats = sorted(r["ms"] for r in rows)
    fams = {}
    for r in rows:
        f = fams.setdefault(r["family"], {"n": 0, "ok": 0, "esc": 0, "wrong": 0})
        f["n"] += 1
        f["ok"] += r["goal_ok"]
        f["esc"] += r["escalated"]
        f["wrong"] += r["wrong_mutations"]
    rescue = [r for r in rows if r["escalated"]]
    summary = {"tag": args.tag, "pipeline": "p2-composed", "n": n,
               "final_goal_ok": pct(lambda r: r["goal_ok"]),
               "autonomous_ok": pct(lambda r: r["goal_ok"] and not r["escalated"]),
               "escalation_rate": pct(lambda r: r["escalated"]),
               "wrong_mutations": sum(r["wrong_mutations"] for r in rows),
               "escalated_cases": len(rescue),
               "escalated_rescued_by_p0": sum(r["goal_ok"] for r in rescue),
               "p2_ceiling": round(
                   (sum(1 for r in n3["rows"] if r["goal_ok"] and not r["escalated"])
                    + len(rescue)) / n, 3),
               "latency_p50_ms": round(st.median(lats)),
               "latency_p95_ms": round(lats[int(n * 0.95) - 1]),
               "families": fams, "rows": rows}
    out = HERE / "reports" / f"arch_{args.tag}_p2composed.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"[{args.tag}/p2-composed] n={n} · full_goal_ok {summary['final_goal_ok']} "
          f"· autonomous {summary['autonomous_ok']} · escalation {summary['escalation_rate']}"
          f" · wrong_mutations {summary['wrong_mutations']}"
          f" · escalated rescues {summary['escalated_rescued_by_p0']}/{len(rescue)}"
          f" · ceiling {summary['p2_ceiling']}")
    print("  Familien:")
    for fam, f in sorted(fams.items()):
        print(f"    {fam:<14} n={f['n']:<3} ok {f['ok']}/{f['n']} · esc {f['esc']}"
              f" · wrong_mut {f['wrong']}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
