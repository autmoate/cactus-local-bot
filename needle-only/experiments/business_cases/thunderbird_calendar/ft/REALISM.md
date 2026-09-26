# Realism / Dev / Hard sets (hand-authored)

Three hand-written, frozen files. Not generated from dataset templates;
`build_realism.py` only resolves each gold temporal span via the production
compiler and validates verbatim grounding. No content is invented by tooling.

| file | n | purpose | may tune contract? |
|---|---|---|---|
| `contract_dev.jsonl` | 35 | contract/order/wording probe | **yes** |
| `realism_challenge.jsonl` | 115 | frozen final Base/FT gate | **never** |
| `hard_challenge.jsonl` | 20 | messy-inbox stress | no (reported) |

The split prevents the methodological leak where a set used to choose the
contract cannot later certify generalization (`FT_PLAN.md` §7/§9). IDs r001–r150
are partitioned: `DEV_IDS` → dev, the rest → frozen.

## Frozen challenge (`realism_challenge.jsonl`, 115)
positive 93 · no-event 22 · selection variants 43 · review-only 7 (timezone).
Categories: informal_confirm, formal_confirm, announcement, short_accept,
relative_time, timezone, long_signature, quoted_thread, tentative_slots,
cancel_reschedule, deadline, unrelated.

## Hard inbox (`hard_challenge.jsonl`, 20)
Deliberately ugly: multi-date reply chains, auto-footer with opening hours,
invoice date + appointment, several phone numbers + times, old date in quote,
misleading subject, "morgen" in a disclaimer, meeting link without "online",
EN/DE mixed, `ab 14 Uhr`, and fuzzy (`gegen`/`ca.`/`zwischen`/`nach dem
Mittagessen`/`Vormittag`) → review. mixed supported / needs_review / no_event.

## Gold policy
- `expected` (eval-compatible): `event_count`, `title`, `start`, `end`,
  `location`, plus `span` = verbatim temporal phrase.
- No-event classes: `event_count: 0` → canonical empty call `[]`.
- Title: explicit name, else cleaned subject (Python fallback). Not required to
  be a substring of the body.
- `span`/`location` must be verbatim substrings (builder enforces).
- **Review-only** spans (timezone / fuzzy) are marked `incomplete: true`;
  `start/end` are null. `temporal.compile_when` detects them at runtime and the
  workflow sets `status=incomplete` + `needs_review` — never a guessed datetime.

## Semantics kept strict
Question/confirmation-seeking or slot-offering text is **no create**, even when
it contains a date: e.g. "Passt dir morgen 11 Uhr?", "Bleibt es bei Donnerstag
11 Uhr?", "Dienstag 13 Uhr ist frei." → `event_count: 0`. Question marks inside
quoted history do not count.

## Metrics (eval.py)
- `supported_final_event_ok` / `supported_approval_ready`: only hard-resolvable
  positives (timezone/fuzzy excluded).
- `review_routing_ok`: incomplete positives correctly routed to review.
- `false_positive_by_category`: near-miss FP split per class (tentative_slots,
  cancel_reschedule, deadline, unrelated, hard_tentative).

## Rebuild / run

```sh
cd needle-only
uv run python experiments/business_cases/thunderbird_calendar/ft/build_realism.py

uv run python experiments/business_cases/thunderbird_calendar/eval.py \
    --backend n2 --cases experiments/business_cases/thunderbird_calendar/ft/contract_dev.jsonl \
    --out experiments/business_cases/thunderbird_calendar/ft/reports/n2_dev.json
uv run python experiments/business_cases/thunderbird_calendar/eval.py \
    --backend n2 --cases experiments/business_cases/thunderbird_calendar/ft/realism_challenge.jsonl \
    --out experiments/business_cases/thunderbird_calendar/ft/reports/n2_realism.json
uv run python experiments/business_cases/thunderbird_calendar/eval.py \
    --backend n2 --cases experiments/business_cases/thunderbird_calendar/ft/hard_challenge.jsonl \
    --out experiments/business_cases/thunderbird_calendar/ft/reports/n2_hard.json
```
