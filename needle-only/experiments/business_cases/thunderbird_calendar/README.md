# Thunderbird → Local Calendar — Business-Case Feasibility Spike

Stand-alone spike, **isolated** from production. Nothing under
`needle-only/src/local_calendar/`, `experiments/ft/` or `experiments/command_ir/`
was touched. No Thunderbird install, no real calendar write, no mail send, no
SMTP, no iTIP, no attachments, no OCR, no fine-tuning, no Gemma, no autonomous
execution. Mail text is the only content source.

## Question

> Can a local Base Needle turn a normal email into an editable calendar-event
> proposal that a human can approve with little or no correction — and is the
> later Thunderbird workflow simulatable without Thunderbird?

```
E-Mail → "Termin erkennen" → local Needle → EventCandidate
       → editierbare Vorschau → Human Approval → simulated calendar
       → optional: participants → simulated invitation draft
```

## Isolation / layout

```
needle-only/experiments/business_cases/thunderbird_calendar/
├── README.md            # this report
├── app.py               # Gradio 4-tab workflow/debug app (wiring only)
├── models.py            # MailMessage, EventCandidate, InvitationDraft, ...
├── thunderbird_sim.py   # MailPort + SimulatedThunderbird
├── extractor.py         # LocalNeedleHost (subprocess N2/N3 JSON protocol)
├── temporal.py          # deterministic when-span -> start/end/all_day compiler
├── workflow.py          # candidates, header participants, FakeStore, outbox
├── eval.py              # fixture metrics (whole-mail vs selection)
├── cases.jsonl          # 60 fixtures with authored gold
├── tests/test_spike.py  # 10 deterministic tests (no model)
└── reports/             # n2.json, n3.json, n3_whenfirst.json (evidence)
```

Production `src/local_calendar` is used **read-only** by `temporal.py` for its
frozen date/time parsing helpers; it is never modified.

## Architecture boundaries

- **MailPort** (`thunderbird_sim.py`) is the only mail surface business logic
  sees. A later `ThunderbirdMailExtensionAdapter` can replace
  `SimulatedThunderbird` without touching workflow/temporal/extractor.
- **LocalNeedleHost** (`extractor.py`) keeps the UI away from `needle`. It
  spawns a worker in the backend's own venv and speaks JSON lines — the logical
  protocol a later Native-Messaging host would use. N2 and N3 stay in separate
  venvs (they cannot be co-installed).
- **Temporal compiler** (`temporal.py`) only translates the model's `when` span
  into `(start, end, all_day)`; it does no mail semantics. Relative phrases are
  resolved against `received_at`, not wall-clock now.
- **Participants** come exclusively from structured `From/To/Cc` headers, never
  from the model and never from body regexes. Own addresses are excluded and
  duplicates deduplicated.
- **Human approval is structural**: `extract` only proposes; `commit`,
  `prepare_invitation` and `send_invitation` run only from UI buttons.

## Extraction contract (a key result)

The contract was not taken as given: a small upfront probe compared candidates.
Findings that materially changed the score:

1. **An explicit `no_event` tool is essential.** With only `extract_event`,
   Base Needle called it for **100 % of negative mails** (false-positive rate
   `1.00`) by echoing the subject as the event title. Adding `no_event` brought
   N2 false positives down to `0.14` with reliable refusals.
2. **Argument order and wording matter.** `title`-first with a `when` described
   as a verbatim *date AND clock-time* phrase avoids both ISO computation and
   the failure mode where `when` receives the event title. A `when`-first
   variant scored better on a few explicit mails but catastrophically echoed the
   title for most. Both variants are reproducible via `TB_CONTRACT=K|G`
   (`reports/n3_whenfirst.json` shows N3 under the alternative).

Final contract (`K`):

```python
extract_event(when: str, title: str, location: str = "")
# when = verbatim date AND clock time phrase, e.g. "9.10. um 13 Uhr"
no_event()
```

Needle never emits year/month/day/start_hour/duration/timezone/participants/
organizer/emails. Python compiles the span.

## Temporal compiler

- explicit dates / `DD.MM.[YYYY]` / month names (DE+EN), `7. und 8. Oktober`
- relative days (`heute/morgen/übermorgen/today/tomorrow`), weekdays incl.
  `nächsten Dienstag` / `kommenden Freitag`
- time ranges (`von 09:00 bis 11:00`, `14-16 Uhr`, `um 14 Uhr`) → end time
- no clock time → all-day (optionally a multi-day span)
- clock time without resolvable date → `status=incomplete`, user edits
- model-computed past dates are rolled by one year (create semantics), which
  recovers Base Needle's wrong-year ISO output (see `e01` trace below)

Default duration when only a start time exists is 60 min; it is shown in the
preview and flagged.

## Fixtures (`cases.jsonl`, 60)

20 explicit single · 10 relative · 8 location/online · 5 all-day · 7 negative ·
5 multi-event · 5 selection-from-multi. Ten cases (5 multi + 5 selection) carry
a `selection` and are also scored in selection mode, so whole-mail vs selection
can be compared on the same corpus. Header personas are synthetic; 10 cases
contain a selection; DE with a few EN phrases; no attachments.

## Run

From `needle-only/` (N2 venv). N3 defaults to the repo-root `.venv-ft3`.

```sh
# Gradio (N2)
uv run python experiments/business_cases/thunderbird_calendar/app.py --backend n2
# Gradio (N3)
uv run python experiments/business_cases/thunderbird_calendar/app.py --backend n3

# Fixture eval
uv run python experiments/business_cases/thunderbird_calendar/eval.py --backend n2
uv run python experiments/business_cases/thunderbird_calendar/eval.py --backend n3
uv run python experiments/business_cases/thunderbird_calendar/eval.py --backend both
uv run python experiments/business_cases/thunderbird_calendar/eval.py --backend n2 --limit 10

# Deterministic tests (no model)
uv run pytest experiments/business_cases/thunderbird_calendar/tests -q
```

Interpreter overrides: `TB_N2_PYTHON`, `TB_N3_PYTHON`. Missing N3 interpreter
fails fast with a clear message instead of crashing the app.

## Results

`reports/n2.json`, `reports/n3.json` (60 cases; selection = 10 subset).
`final` counts negative correct-refusals as success.

| Model | Mode | n | final | approval | detect | false-pos | title | temporal | location | p50 |
|---|---|---|---|---|---|---|---|---|---|---|
| N2 base | whole mail | 60 | **0.667** | 0.633 | 1.00 | **0.143** | 0.868 | 0.642 | 0.906 | 706 ms |
| N2 base | selection | 10 | 0.400 | 0.400 | 1.00 | 0.00 | 0.800 | 0.500 | 1.00 | 712 ms |
| N3 base | whole mail | 60 | 0.100 | 0.100 | 1.00 | 0.857 | 0.849 | 0.094 | 0.906 | 600 ms |
| N3 base | selection | 10 | 0.000 | 0.000 | 1.00 | 0.00 | 0.300 | 0.000 | 0.900 | 507 ms |

Under the alternative `when`-first contract N3 stays weak
(`reports/n3_whenfirst.json`: whole-mail final 0.12, temporal 0.11, FP 0.86), so
"N3 base is unsuitable here" is not an artifact of the chosen contract.

### Reading the numbers

- **Negatives are solved** (N2 FP 0.14 — one residual, n03 "Bericht
  überarbeiten"). The `no_event` tool was decisive.
- **The bottleneck is temporal extraction, not the compiler.** The compiler
  parses the gold spans correctly; Base Needle drops clock times/end times,
  emits date-only values (→ all-day) or normalizes to ISO. `l01` is typical: the
  model returned `2025-10-12 14:00`, losing `bis 16 Uhr`, so the end defaults to
  15:00.
- **Selection is NOT better for Base Needle** (N2 0.40 vs 0.67 whole mail). The
  model often moves the whole selection into `title` and leaves `when` empty, or
  leaks the (unhelpful) subject. This contradicts the upfront hypothesis and is
  itself a business-relevant finding.
- **N3 base is clearly worse** on this German email task: it maps `12.10.` to
  `2026-09-12` (day kept, current month) and frequently refuses to use
  `no_event`.

## Five example traces (N2)

1. `e01` ✅ `when="2025-10-12 14:00"` → compiler rolls the wrong year to
   `2026-10-12 14:00–15:00`, location `Raum 3.14` → final.
2. `e06` ✅ `when="5.11. 09:00-11:00"` → range parsed →
   `2026-11-05 09:00–11:00` → final.
3. `a01` ✅ `when="7. und 8. Oktober"` → all-day span
   `2026-10-07T00:00 – 2026-10-09T00:00` → final.
4. `n01` ✅ no call (`[]`) → `status=none`, no candidate, no write.
5. `l01` ❌ `when="2025-10-12 14:00"` → start recovered but end defaults to
   15:00 instead of the gold 16:00 (range dropped by the model).
6. `m01` (selection) ❌ selection "Review Mittwoch 14 Uhr" →
   `title="Review Mittwoch 14 Uhr", when=""` → incomplete (weekday+time only).

## Verdict (pre-registered)

**WEAK** for Base models with this spike contract:

- STRONG GO needs whole-mail final ≥ 0.85, selection ≥ 0.95, FP ≤ 0.05 — not met.
- PROMISING needs whole-mail 0.65–0.85 **and** selection ≥ 0.90 — selection
  0.40 fails, so not met.
- Whole-mail `0.667` is inside the PROMISING band, but selection does not
  support a "selection-first product V1".

The spike answers its questions: the workflow is fully simulatable, Human
Approval is the natural shape, negatives are solvable, the deterministic
header/time layers are sound — but Base Needle's date/time span extraction is
the hard limit. Per the pre-registered rule, the next step is a **narrow
fine-tune on date/time span extraction** (not started here), not a Thunderbird
add-on yet.

## What a later phase 2 would touch (NOT now)

Thunderbird toolbar/context menu → current message/selection → Native Messaging
→ `LocalNeedleHost` → preview popup. Only then a real CalendarAdapter, and only
after that real invitations. This order was intentionally not pre-empted.
