#!/usr/bin/env python3
"""Re-score frozen Track-B reports with the corrected mutation metrics.

The old reports counted wrong_writes = max(0, write_calls - expected_write_calls),
which mislabeled rejected attempts and expected deletes. The frozen reports carry
db_before/db_after, so the corrected metric is recomputable without a model.
Writes NEW *_rescored.json files; never overwrites the originals.

Usage:
  PYTHONPATH=src uv run python experiments/ft/rescore_native.py
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import eval_mutations as mut  # noqa: E402
from native_agent_bench import CASES  # noqa: E402

CHECK = {c["id"]: (c["check"], c["want_title"]) for c in CASES}


def _events(title_iso: dict, titles: list[str]) -> list[dict]:
    return [{"title": t, "start": title_iso[t], "end": title_iso[t],
             "all_day": False, "participants": []} for t in titles]


def rescore(path: Path) -> dict:
    d = json.loads(path.read_text())
    rows = []
    for r in d["rows"]:
        check, want = CHECK.get(r["id"], ("", ""))
        before, after = r.get("db_before", {}), r.get("db_after", {})
        created = [t for t in after if t not in before]
        deleted = [t for t in before if t not in after]
        modified = [t for t in after if t in before and before[t] != after[t]]
        diff = {"created": _events(after, created), "deleted": _events(before, deleted),
                "modified": _events(after, modified)}
        if check == "absent":
            present, absent = [], [want]
        else:
            present, absent = [(want, "", "", "")], []
        correct, wrong = mut.classify(diff, present, absent)
        rows.append({"id": r["id"], "goal_completed": r["goal_completed"],
                     "write_attempts": r.get("writes", 0),
                     "failed_write_attempts": r.get("failed_writes", 0),
                     "successful_mutations": mut.mutation_count(diff),
                     "correct_mutations": correct, "wrong_mutations": wrong})
    summary = {"tag": d["tag"], "mode": d["mode"], "n": len(rows),
               "goal_completed": d["goal_completed"],
               "write_attempts": sum(r["write_attempts"] for r in rows),
               "failed_write_attempts":
                   sum(r["failed_write_attempts"] for r in rows),
               "successful_mutations":
                   sum(r["successful_mutations"] for r in rows),
               "wrong_mutations": sum(r["wrong_mutations"] for r in rows),
               "old_wrong_writes": d.get("wrong_writes"),
               "old_extra_writes": d.get("extra_writes"),
               "rows": rows}
    return summary


def main() -> int:
    for path in sorted(HERE.glob("reports/native_agent_*r2_*.json")):
        s = rescore(path)
        if "rescored" in path.name:
            continue
        out = path.with_name(path.stem + "_rescored.json")
        out.write_text(json.dumps(s, ensure_ascii=False, indent=1))
        print(f"{path.name:<44} old_wrong_writes={s['old_wrong_writes']} "
              f"-> wrong_mutations={s['wrong_mutations']} "
              f"(attempts {s['write_attempts']}, failed {s['failed_write_attempts']}, "
              f"succ_mut {s['successful_mutations']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
