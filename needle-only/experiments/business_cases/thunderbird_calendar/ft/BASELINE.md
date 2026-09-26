# Base Baseline — Contract Freeze (N2 Base, new v2 contract)

Frozen Base Needle 2 run on the hand-authored sets, **before any FT**. Contract
order was chosen on `contract_dev.jsonl` only; the frozen challenge and hard set
were then measured once with the frozen order and must not be used to tune the
contract.

## Contract order probe (contract_dev, n=35; 9 selection)

Same requiredness (all args optional) in both variants — isolates order.

| order | mode | supported_final | final | selection? | title | temporal | p50 |
|---|---|---|---|---|---|---|---|
| title-first | message | 0.24 | 0.14 | – | 0.88 | 0.29 | 1206 ms |
| title-first | selection | 0.00 | 0.00 | – | 0.11 | 0.11 | 973 ms |
| **when-first** | message | **0.43** | 0.26 | – | 0.79 | 0.38 | 2119 ms |
| **when-first** | selection | **0.56** | 0.56 | – | 0.67 | 0.56 | 1331 ms |

**Decision: freeze `when-first`** (`TB_CONTRACT_ORDER=when` is the default).

## Frozen evaluations (N2 Base, when-first, no FT)

| set | mode | n | supported_final | final | FP | review_routing | title | temporal | p50 |
|---|---|---|---|---|---|---|---|---|---|
| realism_challenge | message | 115 | 0.44 | 0.35 | 0.91 | 0.86 | 0.95 | 0.47 | 1301 ms |
| realism_challenge | selection | 43 | 0.42 | 0.42 | 0.00 | – | 0.51 | 0.60 | 935 ms |
| hard_challenge | message | 20 | 0.14 | 0.10 | 1.00 | 1.00 | 0.79 | 0.26 | 2575 ms |

Near-miss false positives (frozen): tentative_slots 1.00, cancel_reschedule
1.00, deadline 1.00, unrelated 0.33.

## Reading

- **Negatives are now the dominant failure.** Removing `no_event` (negatives =
  canonical empty call `[]`, `FT_PLAN.md` §4) costs Base all refusals: FP 0.91 /
  1.00. This is the deliberate risk the FT must close by learning `[]`.
- **Temporal span extraction is the other bottleneck** (`temporal` 0.47 / 0.26),
  consistent with the earlier spike.
- **Selection is genuinely better than whole-mail on the dev set** once the
  subject leak is gone (0.56 vs 0.43), unlike the old contract.
- Title is largely carried by the subject fallback (title_ok 0.95 frozen).
- Review routing works (0.86 / 1.00) — timezone/fuzzy spans are not guessed.

## Verdict

**Dataset-design freeze reached.** The FT target is now unambiguous: teach the
`[]` refusal class plus verbatim temporal-span copying. Base is far from the
gate (needs supported_final ≥ 0.85, FP ≤ 0.05), so the N2 FT is justified.

Next (only after this freeze): `dataset_spec_tb.py`, `build_dataset_tb.py` +
validator, `modal_train_tb.py`, `gate_tb.py`, then Modal smoke and ≤3 runs.

Reports: `reports/n2_dev_title.json`, `n2_dev_when.json`, `n2_realism.json`,
`n2_hard.json`.
