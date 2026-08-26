# Cactus Compute Prototype

Local-first prototype for testing Cactus/Gemma-4 E2B with Needle 2 tool routing and a Postgres/pgvector data store.

## At A Glance
- `tui-app.py`: terminal UI loop for requests, routing state, tool approval, and DB status.
- `modules/needle_router.py`: asks Needle 2 for structured tool proposals via `complete()`.
- `modules/cactus_engine.py`: talks to local Cactus `serve` through OpenAI-compatible HTTP.
- `modules/postgres_store.py`: initializes Postgres tables and pgvector search.
- `modules/tool_catalog.py`: Needle tool definitions and approved execution mapping.
- `scripts/launch.py`: one-command launcher for Postgres and the app; optionally starts Cactus.

## Quick Start
```bash
cp .env.example .env
uv sync
uv run python scripts/launch.py
```

The app starts Postgres via Docker Compose, initializes `pgvector`, and opens the terminal UI. Tool calls are shown for approval before execution.

If Docker Hub cannot pull the pgvector image, set `POSTGRES_AUTO_START=0` in `.env` to test the TUI/routing without DB execution.

For real Needle 2 routing instead of the visible heuristic fallback, install the optional Needle/JAX stack:
```bash
uv sync --extra needle
```

## Docs
- [Setup](docs/setup.md): prerequisites, `uv`, Postgres, Cactus, Gemma model setup.
- [Manual](docs/manual.md): operating the TUI and step-by-step smoke tests.
- [Model Conversion](docs/model-conversion.md): Cactus download/convert/run/serve workflow.

## Local-First Contract
- Needle proposes tool calls; this app executes only after user approval.
- Rejected, unsupported, low-confidence, or complex requests escalate to Gemma through Cactus.
- Postgres is the relational DB and vector store; no separate vector DB is used.
- Embeddings are deterministic local hash vectors for the prototype so DB/vector behavior works before adding a dedicated embedding model.
