#!/usr/bin/env python3
"""Toolset-Ablation: Bringt eine Read/Write-Trennung den Spezialisten Stabilität?

Kein Training. Bestehende Modelle (N2-FT, N3-E5, N3-Base) werden mit EINEM
eingeschränkten Toolset laufen gelassen und auf den passenden frozen Subsets
gemessen:

  --toolset read   → nur calendar_list + calendar_find_slot  (auf Read-Cases + Neg)
  --toolset write  → nur create/move/delete                  (auf Write-Cases + Neg)
  --toolset all    → alle 5 (Referenz; sonst aus base_eval bekannt)

Metriken identisch zu base_eval: tool_ok, args_ok (semantisch), exact_args,
refusals (auf Negativen), per-field, median ms.

Usage:
  NEEDLE_WEIGHTS=<cact> PYTHONPATH=src ../.venv-ft3/bin/python \
      experiments/ft/toolset_ablation.py --toolset read --tag n3-v4-e5-read
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import needle  # noqa: E402
import dataset_spec as spec  # noqa: E402
from base_eval import _sem_equal  # noqa: E402

READ_TOOLS = ("calendar_list", "calendar_find_slot")
WRITE_TOOLS = ("calendar_create", "calendar_move", "calendar_delete")
FIELDS = ("date", "until", "time", "end_time", "title", "persons",
          "person", "horizon", "duration_min", "days", "participants")


def _schemas(toolset: str) -> list:
    alls = json.loads((HERE / "tools.json").read_text())
    if toolset == "read":
        want = READ_TOOLS
    elif toolset == "write":
        want = WRITE_TOOLS
    else:
        want = READ_TOOLS + WRITE_TOOLS
    return [s for s in alls if s["name"] in want]


def _class_of(tool: str) -> str:
    return "read" if tool in READ_TOOLS else ("write" if tool in WRITE_TOOLS else "none")


def load_cases(toolset: str, challenge: bool = False) -> list[dict]:
    path = (HERE / "challenge" / "challenge_traces.jsonl") if challenge \
        else (HERE / "data" / "test.jsonl")
    out = []
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if challenge:
            tool = r["tool"]
            args = r.get("args", {})
            query = r["input"]
        else:
            tool = r["meta"]["tool"]
            args = (r["answers"][0]["arguments"] if r.get("answers") else {})
            query = r["query"]
        cls = _class_of(tool)
        if toolset == "all":
            keep = True
        elif toolset == "read":
            keep = cls in ("read", "none")
        else:
            keep = cls in ("write", "none")
        if keep:
            out.append({"id": r.get("id", query[:20]), "input": query,
                        "tool": tool, "args": args, "cls": cls})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--toolset", choices=["read", "write", "all"], required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--challenge", action="store_true")
    args = ap.parse_args()
    import os
    weights = os.environ.get("NEEDLE_WEIGHTS") or None

    tools = _schemas(args.toolset)
    agent = needle.Needle(tools=tools, system=spec.SYSTEM_FACTS, weights=weights)
    cases = load_cases(args.toolset, args.challenge)
    rows = []
    for c in cases:
        agent.reset()
        t0 = time.perf_counter()
        try:
            resp = agent.complete(c["input"])
        except Exception:  # noqa: BLE001
            resp = {"function_calls": []}
        ms = round((time.perf_counter() - t0) * 1000)
        calls = resp.get("function_calls") or []
        got = calls[0] if calls else {}
        name, gotargs = got.get("name"), dict(got.get("arguments") or {})
        tool_ok = (not name) if c["tool"] == "none" else (name == c["tool"])
        want = c["args"]
        missing = [k for k, v in want.items() if v and not gotargs.get(k)]
        wrong = [k for k, v in gotargs.items() if v and k in want and want[k]
                 and not _sem_equal(k, v, want[k])]
        args_ok = tool_ok and not missing and not wrong
        rows.append({"id": c["id"], "cls": c["cls"], "tool_ok": tool_ok,
                     "args_ok": args_ok, "exact": tool_ok and gotargs == want,
                     "refusal": not name, "ms": ms, "want": want, "got": gotargs})

    def sub(sel):
        r2 = [r for r in rows if sel(r)]
        n = len(r2) or 1
        return {"n": len(r2),
                "tool_ok": round(sum(r["tool_ok"] for r in r2) / n, 3),
                "args_ok": round(sum(r["args_ok"] for r in r2) / n, 3),
                "exact": round(sum(r["exact"] for r in r2) / n, 3),
                "refusals": round(sum(r["refusal"] for r in r2) / n, 3)}

    per = {}
    for cls in ("read", "write", "none"):
        if any(r["cls"] == cls for r in rows):
            per[cls] = sub(lambda r, c=cls: r["cls"] == c)
    field = {f: [0, 0] for f in FIELDS}
    for r in rows:
        for f, w in r["want"].items():
            if w:
                field[f][1] += 1
                if _sem_equal(f, r["got"].get(f), w):
                    field[f][0] += 1
    summary = {"tag": args.tag, "toolset": args.toolset, "weights": weights,
               "challenge": args.challenge, "n": len(rows),
               "median_ms": round(st.median(r["ms"] for r in rows)),
               "by_class": per,
               "per_field": {f: (round(c / e, 3) if e else None)
                             for f, (c, e) in field.items()}}
    out = HERE / "reports" / f"ablation_{args.tag}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"[{args.tag}] n={summary['n']} median={summary['median_ms']}ms")
    for cls, m in per.items():
        print(f"   {cls:<6} n={m['n']:<4} tool {m['tool_ok']} args {m['args_ok']} "
              f"exact {m['exact']} refusals {m['refusals']}")
    print(f"   per_field: {json.dumps({k: v for k, v in summary['per_field'].items() if v is not None})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
