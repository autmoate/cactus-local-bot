# Realism Challenge Set (hand-authored)

`realism_challenge.jsonl` — 150 **hand-written** mails, frozen. Not generated
from the dataset templates; `build_realism.py` only resolves each gold temporal
span to concrete start/end via the production compiler and validates that the
gold span (and location) is a verbatim substring of the mail / selection. No
content is invented by tooling.

Purpose: measure Base Needle on realistic mailbox stress before any FT, and
afterwards use it as the pre-registered evaluation set (`FT_PLAN.md` §7/§9).

## Distribution (150)

| category | n | V1 |
|---|---|---|
| informal_confirm | 25 | positive |
| formal_confirm | 20 | positive |
| announcement | 15 | positive |
| short_accept | 15 | positive |
| relative_time | 10 | positive |
| timezone | 10 | positive, `incomplete` (TZ not solved in V1) |
| long_signature | 10 | positive |
| quoted_thread | 15 | positive (current text > quoted history) |
| tentative_slots | 10 | **no event** |
| cancel_reschedule | 10 | **no event** |
| deadline | 5 | **no event** |
| unrelated | 5 | **no event** |

positive 120 · no-event 30 · selection variants 55 (`selection_expected`).

## Gold policy

- `expected` is eval-compatible (`event_count`, `title`, `start`, `end`,
  `location`) plus `span` = the verbatim temporal phrase the model must copy.
- No-event classes have `event_count: 0` → canonical empty call `[]`.
- Title: explicit event name, else the cleaned subject (Python fallback,
  `workflow.clean_subject`). Not required to be a substring of the body.
- `span`/`location` must be verbatim substrings (builder enforces this).
- Timezone spans are marked `incomplete: true` — the compiler does not resolve
  zones yet; V1 shows `needs_review` rather than guessing.

## Rebuild / validate

```sh
cd needle-only
uv run python experiments/business_cases/thunderbird_calendar/ft/build_realism.py
```

## Run Base / FT against it

```sh
cd needle-only
uv run python experiments/business_cases/thunderbird_calendar/eval.py \
    --backend n2 --cases experiments/business_cases/thunderbird_calendar/ft/realism_challenge.jsonl \
    --out experiments/business_cases/thunderbird_calendar/ft/reports/n2_realism.json
```
