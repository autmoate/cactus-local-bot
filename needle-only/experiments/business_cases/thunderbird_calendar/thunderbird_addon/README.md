# Thunderbird Integration Spike — Phase 1

First real Thunderbird integration for the calendar spike. **Preview only:**

```
Thunderbird  [📌 Termin vorbereiten]
   → current message (Subject/From/To/Cc/body)
   → Native Messaging
   → native/native_host.py  (thin adapter)
   → CalendarSpike + LocalNeedleHost (same code as the Gradio app)
   → EventCandidate JSON
   → small Thunderbird popup
```

**Not in this phase:** calendar write, SMTP, iTIP, attachments, HTML parsing,
further FT, product re-architecture. No mail is persisted; no network calls.

## Files

| file | role |
|---|---|
| `manifest.json` | MailExtension (MV2), `message_display_action`, permissions `messagesRead`, `nativeMessaging` |
| `background.js` | reads displayed message, calls the host, opens the preview |
| `popup.html` / `popup.js` | read-only preview (title, date, start/end, place, status, when-span, model) |
| `native/native_host.py` | Native Messaging stdio host; maps payload ↔ `MailMessage`/`EventCandidate` |
| `native/run_host.sh` | launcher registered as the host (resolves paths, picks weights) |
| `native/install_native_host.sh` | writes the host manifest to `~/.mozilla/native-messaging-hosts/` |
| `tests/test_native_host.py` | deterministic framing / mapping / roundtrip tests |

## Install (Linux, temporary add-on)

```sh
cd needle-only/experiments/business_cases/thunderbird_calendar/thunderbird_addon

# 1. Register the native host (no sudo)
bash native/install_native_host.sh
#    -> ~/.mozilla/native-messaging-hosts/de.example.tb_calendar_extract.json

# 2. Load the add-on in Thunderbird:
#    Tools → Developer Tools → Debug Add-ons → Load Temporary Add-on…
#    select thunderbird_addon/manifest.json
```

The host runs out of `needle-only/.venv` and, by default, loads the FT candidate
`ft/models/n2-train-r16-e8-s42.cact` if present. Override with env vars before
installing/starting:

```sh
export TB_NATIVE_WEIGHTS=/abs/path/to/n2-train-r16-e8-s42.cact
export TB_NATIVE_BACKEND=n2          # optional
export TB_NATIVE_PYTHON=/abs/needle-only/.venv/bin/python  # optional
```

If no weights are found the host runs **Base** and says so in the preview
(`model: n2-base`) — no silent mix-up.

## Manual test path

1. Start Thunderbird, open a mail, click **📌 Termin vorbereiten**.
2. A popup shows the candidate; the first click is slow (model cold start).
3. Suggested cases:
   - `Dienstag 14 Uhr passt mir. Bis dann!` → event
   - `Passt dir Dienstag 14 Uhr?` → no event
   - `Unser Termin Dienstag 14 Uhr fällt leider aus.` → no event
   - a `Re:` thread with an old date in the quote → only the current line counts

## Tests

```sh
cd needle-only
uv run pytest experiments/business_cases/thunderbird_calendar/thunderbird_addon/tests -q
```

## Known limits

- Body extraction flattens the MIME tree and does **not** parse HTML (Phase 1).
- `received_at` comes from the message `Date` header; relative spans ("morgen")
  resolve against it.
- Message-toolbar button only; the optional context-menu/selection path is not
  included (kept for a follow-up).
- Native host cold start loads the model per Thunderbird session.
