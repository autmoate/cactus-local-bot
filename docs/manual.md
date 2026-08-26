# Manual

## Start
```bash
uv run python scripts/launch.py
```

You stay in one terminal. The launcher starts Postgres, optionally starts Cactus, then opens the TUI.

If Docker Hub cannot pull the pgvector image, set `POSTGRES_AUTO_START=0` in `.env` and launch anyway. The TUI will start, but DB tools will fail until `DATABASE_URL` points to a running Postgres with pgvector.

## TUI Flow
1. Enter a natural-language request.
2. Needle proposes a structured tool call when possible.
3. The app shows tool name, arguments, reasoning, and confidence.
4. Approve with `y` to execute against Postgres.
5. Reject with `n` to escalate to Gemma/Cactus.
6. Type `quit` to exit.

## Smoke Tests
Run these after `uv run python scripts/launch.py`.

### DB Status
Input:
```text
db stats
```

Expected: proposed `db_stats`; after approval, counts for `inventory`, `todos`, `calendar_events`, and `knowledge`.

### Todo Write
Input:
```text
create todo Milch kaufen morgen
```

Expected: proposed `create_todo`; nothing is written until approval.

### Vector Search
First store knowledge:
```text
speichere wissen: Cactus serve stellt eine OpenAI-kompatible lokale API bereit
```

If Needle does not infer `add_knowledge`, use a clearer wording:
```text
add knowledge Cactus serve exposes a local OpenAI compatible API
```

Then search:
```text
search knowledge for OpenAI API
```

Expected: `search_records` returns similar knowledge rows with a score.

### Rejection Path
Input:
```text
create todo test rejection
```

Reject the proposal. Expected: no DB write; app escalates to Gemma. If Cactus is offline, the Gemma panel says so.

### Off-Topic Path
Input:
```text
erklär mir quantenphysik kurz
```

Expected: Needle returns no useful call or low confidence; Gemma handles the response.

## Notes
- When `cactus-needle` cannot initialize, the app uses a visible heuristic fallback so DB and approval flow remain testable.
- Install real Needle routing with `uv sync --extra needle`; the base `uv sync` is intentionally lightweight.
- Real Needle routing is active when the status row says `Needle ready`.
- The prototype uses hash embeddings for pgvector; replace `modules/embeddings.py` later if Cactus embeddings are available locally.
