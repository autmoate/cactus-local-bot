# N2 FT Results — Thunderbird Calendar (execution run)

Execution of the pre-registered FT (`FT_PLAN.md` v2). Strictly serial Modal
jobs, hard aggregate budget. **No N3, no Gemma, no 4th run.**

## 1. Starting baseline (N2 Base, frozen when-first)

| set | mode | supported_final | final | FP | review | evidence |
|---|---|---|---|---|---|---|
| realism_challenge | message | 0.442 | 0.348 | 0.909 | 0.857 | 0.613 |
| realism_challenge | selection | 0.419 | 0.419 | – | – | – |
| hard_challenge | message | 0.143 | 0.100 | 1.000 | 1.000 | – |

## 2. Dataset / schema

- schema `3b2af8acaa0bd5ee` (frozen when-first `extract_event`), seed 42
- counts: train 7000 / validation 800 / test 1400 / smoke 200
- train.jsonl `2413cdcdc8a5a1b6` · validation `1c3db87ad09f616b` ·
  test `283a509337d1ac16` · smoke `59b8bdc544b4cb04`
- negatives = empty call `[]`; validator green before training.

## 3. Smoke

PASS: image built, `cactus-needle[train,gpu]==2.0.13` installed, `needle
finetune` ran (50 s, 25 steps), LoRA saved, `needle build` produced a 13.7 MB
`.cact`, volume + download worked. Smoke used for function only.

> Harness bug found and fixed during evaluation: `LocalNeedleHost.start()` did
> not pass `--weights` to the worker, so the first evaluation pass silently ran
> **Base**. Fixed (`253aa96`) and all evaluations re-run. Verified: the FT output
> differs from Base (dev supported_final 0.90 vs 0.43, FP 0.27 vs 1.00).

## 4. Modal budget (aggregate GPU time)

| job | elapsed |
|---|---|
| smoke | 69.1 s |
| Run A r16/e5 | 606.8 s |
| Run B r16/e8 | 930.3 s |
| Run C r8/e8 | 930.7 s |
| **total** | **2467.8 s = 0.686 h** |

Budget ≤ 6.0 h: **YES** (0.69 h used).

## 5. Runs

| run | rank | lr | epochs | seed | batch | train_s | .cact SHA256 |
|---|---|---|---|---|---|---|---|
| A n2-train-r16-e5-s42 | 16 | 1e-4 | 5 | 42 | 8 | 591.8 | `50b5209f5f4f1e45` |
| B n2-train-r16-e8-s42 | 16 | 1e-4 | 8 | 42 | 8 | 917.8 | `ccb63964b991e5b1` |
| C n2-train-r8-e8-s42 | 8 | 1e-4 | 8 | 42 | 8 | 917.8 | `6f5b189a64afbcdd` |

`.cact` weights are gitignored; manifests/logs in `reports/runs/`.

## 6. Realism whole-mail

| run | supported_final | approval | final | FP | review | evidence | temporal | title | loc | p50 | p95 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 0.767 | 0.767 | 0.722 | 0.227 | 0.857 | 0.817 | 0.731 | 0.850 | 0.828 | 968 | 2866 |
| B | **0.826** | 0.826 | 0.739 | 0.364 | 0.857 | 0.860 | 0.785 | 0.892 | 0.871 | 1062 | 4371 |
| C | 0.721 | 0.721 | 0.687 | 0.227 | 0.857 | 0.785 | 0.688 | 0.796 | 0.796 | 911 | 2919 |

## 7. Realism selection

| run | final | evidence | temporal | p50 |
|---|---|---|---|---|
| A | 0.907 | 0.907 | 0.954 | 863 |
| B | **0.930** | 0.884 | 0.977 | 1056 |
| C | 0.930 | 0.907 | 0.977 | 983 |

## 8. Hard challenge (message)

| run | supported_final | review | FP | evidence | temporal | p50 |
|---|---|---|---|---|---|---|
| A | 0.571 | 0.600 | 0.000 | 0.632 | 0.474 | 1011 |
| B | 0.571 | 0.600 | 0.000 | 0.579 | 0.474 | 1094 |
| C | 0.571 | 0.600 | 0.000 | 0.579 | 0.474 | 658 |
| Base | 0.143 | 1.000 | 1.000 | – | 0.259 | – |

## 9. Near-miss FP per category (realism message)

| run | tentative | cancel/resched | deadline | unrelated |
|---|---|---|---|---|
| A | 0.333 | 0.143 | 0.000 | 0.333 |
| B | 0.333 | 0.286 | 0.333 | 0.667 |
| C | 0.333 | 0.143 | 0.000 | 0.333 |
| Base | 1.000 | 1.000 | 1.000 | 0.333 |

## 10–11. Evidence grounding & latency

Evidence grounded: realism 0.785–0.860; selection 0.884–0.907; hard 0.579–0.632.
Message p50 911–1062 ms (Base 1301 ms), p95 up to 4.4 s (B).

## 12. Gate per candidate (`gate_tb.py`)

All three: **NO-GO**. Passing: selection ≥ 0.90 (all), hard ≥ base + 0.10 (all),
deadline FP (A, C). Failing: message `supported_final` (< 0.85; B closest 0.826),
overall FP (0.23–0.36 ≫ 0.05), per-category near-miss FP, `review_routing` 0.857
(< 0.90), `evidence_grounded` 0.79–0.86 (< 0.95).

## 13. Best candidate

**B `n2-train-r16-e8-s42`** — best message `supported_final` (0.826) and
selection (0.930). Caveat: highest overall FP (0.364). Lower-FP alternative for a
refusal-first product read: **A** (FP 0.227, supported 0.767, selection 0.907).

## 14. Remaining errors (concrete)

- **Half-refusals:** some negatives yield `extract_event(title=…, when="")`
  instead of `[]` (e.g. r147 "Witz", r148 "Wetter", r136 cancel, r144 deadline) →
  counted as false positives. `[]` is learned but not exclusively.
- **False refusals on positives:** 22 message misses; many are valid events the
  model dropped entirely (r006, r020, r030, r042, r043 → no candidate).
- **Temporal spans** remain the second gap (temporal 0.69–0.79); end-time /
  timezone evidence partially lost.
- **Review routing** 0.857: 1–2 timezone/fuzzy cases not flagged for review.
- Hard set stuck at 0.571 — the deliberately messy cases (reply chains, fuzzy
  times) are not mastered.

## 15. Verdict (pre-registered)

`gate_tb.py`: **NO-GO** for all three (FP, evidence, review fail).

Per `FT_PLAN.md` §9's PROMISING band (whole-mail 0.65–0.85 **and** selection
≥ 0.90): candidate **B qualifies → PROMISING (selection-first)**; A and C also
fall in the band with lower FP.

**No 4th run, no new matrix, no dataset tuning in this task.** The FT clearly
learned the `[]` refusal class (FP 1.00 → 0.23–0.36, hard 0.00) and lifted
selection to ≥ 0.90, but the full gate is not met — the open gap is false
positives on near-miss negatives plus evidence grounding.
