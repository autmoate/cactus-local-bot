#!/usr/bin/env python3
"""Promotion-Gate + vollständiger Metrik-Report für FT-Kandidaten.

KEIN einzelner Gesamtscore: jede Achse wird getrennt ausgewiesen (Atomic,
Generalization/Challenge, Multi-Call, Refusal, E2E/final_db, Latenz).

Ein Needle-3-Kandidat ersetzt das Needle-2-FT (Referenz) erst, wenn ALLE
Gate-Kriterien erfüllt sind — siehe BASELINES.md.

Artefakte pro Tag (nur vorhandene werden gelesen):
  reports/<tag>_test_report.json       (base_eval: Atomic + Challenge + §22)
  reports/<tag>_challenge_report.json
  reports/<tag>_test_rows.jsonl        (per-Beispiel: Refusal-Zerlegung, p95)
  reports/multicall_<tag>.json         (multi_call_bench)
  reports/e2e_<tag>.json               (tests/test_e2e.py --out)

Usage:
  PYTHONPATH=src python experiments/ft/promotion_gate.py [tag ...]
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

FT = Path(__file__).resolve().parent
REPORTS = FT / "reports"
REFERENCE = "sa-r16-lr1e-4-e8-seed44"          # N2-FT auf Dataset v2 (SOTA)
DEFAULT_TAGS = ["base", "needle3-base", REFERENCE, "n3-v2-ft", "n3-v3-ft"]

GATE = {
    "atomic_args_ok": 0.95,
    "atomic_exact": 0.95,
    "tool_ok": 0.98,
    "multi_all_actions": 0.80,
    "neg_refusal_rate": 0.90,
    "false_refusal_rate_max": 0.01,       # Anteil falscher Refusals an Positiven
    "challenge_tolerance_pp": 0.05,       # nicht >5pp schlechter als Referenz
}


def _neg_ids() -> set[str]:
    ids = set()
    for line in open(FT / "data" / "test.jsonl", encoding="utf-8"):
        r = json.loads(line)
        if r["meta"]["tool"] == "none":
            ids.add(r["id"])
    return ids


def load(tag: str, neg: set[str]) -> dict:
    m: dict = {"tag": tag}
    t_p = REPORTS / f"{tag}_test_report.json"
    c_p = REPORTS / f"{tag}_challenge_report.json"
    rows_p = REPORTS / f"{tag}_test_rows.jsonl"
    mc_p = REPORTS / f"multicall_{tag}.json"
    e2e_p = REPORTS / f"e2e_{tag}.json"

    if t_p.exists():
        t = json.loads(t_p.read_text())
        m["tool_ok"] = t.get("tool_ok")
        m["args_ok"] = t.get("args_ok")
        m["exact"] = t.get("exact_args_ok")
    if c_p.exists():
        c = json.loads(c_p.read_text())
        m["ch_args_ok"] = c.get("args_ok")
        m["ch_exact"] = c.get("exact_args_ok")
    if rows_p.exists():
        rows = [json.loads(l) for l in open(rows_p, encoding="utf-8")]
        pos = [r for r in rows if r["id"] not in neg]
        ngs = [r for r in rows if r["id"] in neg]
        m["false_refusal_n"] = sum(bool(r.get("refusal")) for r in pos)
        m["false_refusal_rate"] = round(m["false_refusal_n"] / max(1, len(pos)), 4)
        m["neg_refusal_n"] = sum(bool(r.get("refusal")) for r in ngs)
        m["neg_refusal_rate"] = round(m["neg_refusal_n"] / max(1, len(ngs)), 4)
        ms = [r["ms"] for r in rows if r.get("ms")]
        if ms:
            m["median_ms"] = round(st.median(ms))
            m["p95_ms"] = round(sorted(ms)[int(len(ms) * 0.95) - 1])
    if mc_p.exists():
        mc = json.loads(mc_p.read_text())
        m["multi_call_count"] = mc.get("call_count_ok")
        m["multi_order"] = mc.get("order_ok")
        m["multi_args"] = mc.get("args_ok")
        m["multi_all"] = mc.get("all_actions_correct")
    if e2e_p.exists():
        e = json.loads(e2e_p.read_text())
        m["final_db"] = e.get("final_db_ok")
    return m


def fmt(v, kind="f"):
    if v is None:
        return "  –  "
    return f"{v:.3f}" if kind == "f" else str(v)


def main() -> int:
    tags = sys.argv[1:] or DEFAULT_TAGS
    neg = _neg_ids()
    models = {t: load(t, neg) for t in tags}
    ref = models.get(REFERENCE, {})

    print("\n=== Vollständiger Metrik-Report (je Achse getrennt) ===")
    hdr = (f"{'tag':26s} {'atomic':>18s} {'challenge':>13s} {'multi(all)':>10s} "
           f"{'negRef':>7s} {'falseRef(n/%)':>15s} {'final_db':>9s} {'ms':>6s}")
    print(hdr)
    print(f"{'':26s} {'tool/args/exact':>18s} {'args/exact':>13s}")
    for t, m in models.items():
        print(f"{t:26s} "
              f"{fmt(m.get('tool_ok'))}/{fmt(m.get('args_ok'))}/{fmt(m.get('exact'))} "
              f"{fmt(m.get('ch_args_ok'))}/{fmt(m.get('ch_exact'))} "
              f"{fmt(m.get('multi_all'))} {fmt(m.get('neg_refusal_rate'))} "
              f"{str(m.get('false_refusal_n'))+'/'+format(m['false_refusal_rate']*100, '.2f')+'%' if m.get('false_refusal_rate') is not None else '  –  '} "
              f"{fmt(m.get('final_db'))} {m.get('median_ms','–')}")

    print("\n=== Promotion-Gate (Ziel: ein Needle-3-Kandidat) ===")
    any_pass = False
    for t, m in models.items():
        if t == REFERENCE or t in ("base", "needle3-base"):
            continue
        checks = []
        def chk(name, ok, detail):
            checks.append((name, ok, detail))
        chk("atomic args_ok>=%.2f" % GATE["atomic_args_ok"],
            (m.get("args_ok") or 0) >= GATE["atomic_args_ok"], fmt(m.get("args_ok")))
        chk("atomic exact>=%.2f" % GATE["atomic_exact"],
            (m.get("exact") or 0) >= GATE["atomic_exact"], fmt(m.get("exact")))
        chk("tool_ok>=%.2f" % GATE["tool_ok"],
            (m.get("tool_ok") or 0) >= GATE["tool_ok"], fmt(m.get("tool_ok")))
        chk("multi all_actions>=%.2f" % GATE["multi_all_actions"],
            (m.get("multi_all") or 0) >= GATE["multi_all_actions"], fmt(m.get("multi_all")))
        chk("neg refusal>=%.2f" % GATE["neg_refusal_rate"],
            (m.get("neg_refusal_rate") or 0) >= GATE["neg_refusal_rate"], fmt(m.get("neg_refusal_rate")))
        fr = m.get("false_refusal_rate")
        chk("false refusal<=%.0f%%" % (GATE["false_refusal_rate_max"]*100),
            fr is not None and fr <= GATE["false_refusal_rate_max"],
            f"{(fr or 0)*100:.2f}%")
        for key, label in (("ch_exact", "challenge exact"), ("ch_args_ok", "challenge args")):
            if m.get(key) is not None and ref.get(key) is not None:
                tol = ref[key] - GATE["challenge_tolerance_pp"]
                chk(f"{label}>=ref-5pp", m[key] >= tol, f"{m[key]:.3f} vs {tol:.3f}")
        if m.get("final_db") is not None and ref.get("final_db") is not None:
            chk("final_db>=ref", m["final_db"] >= ref["final_db"],
                f"{m['final_db']:.3f} vs {ref['final_db']:.3f}")
        ok_all = all(c[1] for c in checks) and bool(checks)
        any_pass = any_pass or ok_all
        print(f"\n  {t}: {'PASS' if ok_all else 'FAIL'}")
        for name, ok, detail in checks:
            print(f"    {'✓' if ok else '✗'} {name:28s} {detail}")
    print(f"\nReferenz (N2-FT): {REFERENCE}")
    print(f"Ergebnis: {'mindestens ein Kandidat hält das Gate' if any_pass else 'kein Kandidat hält das Gate'}")
    return 0 if any_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
