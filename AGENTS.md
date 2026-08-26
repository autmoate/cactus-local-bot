# AGENTS.md

## Repo State
- This is a prototype workspace for local Cactus/Gemma + Needle 2 experiments, not an upstream checkout of `cactus-compute/cactus` or `cactus-compute/needle` unless those directories are later added under `vendor/` or similar.
- Python environment is managed with `uv`; dependencies are in `pyproject.toml` and the app is launched with `uv run python scripts/launch.py`.
- Keep Python modules small; user explicitly asked for `.py` modules around 100 lines max and functionality split under `modules/`.

## Local Commands
- First run: `cp .env.example .env && uv sync`; use `uv sync --extra needle` for real Needle 2 instead of the heuristic fallback.
- Normal launch: `uv run python scripts/launch.py`; this starts Docker Compose Postgres, initializes pgvector tables, and opens `tui-app.py`.
- Manual DB start: `docker compose up -d postgres`.
- Project Postgres binds to host port `5433` by default via `POSTGRES_HOST_PORT` to avoid conflicts with other local Postgres containers.
- If Docker Hub pgvector pulls fail, set `.env` `POSTGRES_AUTO_START=0`; the TUI still starts, but DB tools need a reachable `DATABASE_URL`.
- Cactus auto-start is controlled by `.env` `CACTUS_AUTO_START=1`; otherwise start `cactus serve` manually before launch.
- Cactus was installed from `vendor/cactus/python` via `uv`; use `uv sync --extra cactus` after cloning `vendor/cactus`.
- No automated test suite exists yet; use `docs/manual.md` smoke tests for DB status, todo write, vector search, rejection, and off-topic routing.

## Current Code Map
- `tui-app.py` is the terminal UI and request loop.
- `modules/needle_router.py` calls Needle with `complete()` when available and uses a visible heuristic fallback if Needle cannot initialize.
- `modules/tool_catalog.py` defines Needle tools and maps approved calls to execution.
- `modules/postgres_store.py` owns schema creation and pgvector search across `inventory`, `todos`, `calendar_events`, and `knowledge`.
- `modules/cactus_engine.py` calls local Cactus OpenAI-compatible HTTP at `CACTUS_BASE_URL` for Gemma escalation.
- `scripts/launch.py` creates `.env` from `.env.example` if missing, starts Postgres, optionally starts Cactus, then runs `tui-app.py`.

## Product Direction
- Goal: locally test converted Gemma-4 E2B with Cactus and connect it to Needle 2 for tool calling.
- Desired routing: prefer Needle for structured tool-call selection; require user approval before executing actions; on non-approval, low confidence, off-topic input, or complex requests, escalate to Gemma.
- Target prototype: terminal UI showing model/inference state, Postgres DB/vector-store state, and a Needle tool catalog.
- Data target: Postgres should cover normal relational data plus vector search for inventory, calendar, todos, and knowledge/RAG-style context.

## Upstream Cactus Facts
- Source: `https://github.com/cactus-compute/cactus`; docs from upstream README were the only verified source because this workspace had no local code.
- Upstream setup is `source ./setup`, not `./setup`; it creates `venv`, requires `python3.12`, installs `python/requirements.txt`, and installs the Python CLI from `python`.
- Linux prerequisites listed upstream: `python3.12 python3.12-venv python3-pip cmake build-essential libcurl4-openssl-dev`.
- Local model commands from upstream: `cactus convert <model> [dir]`, `cactus download [model]`, `cactus run [model|path]`, and `cactus serve [model]`.
- Gemma default in upstream Cactus test/benchmark help is `google/gemma-4-E2B-it`; Cactus README examples mention Gemma-4-E2B-CQ4 and `cactus run Cactus-Compute/needle [--tools my_tools.json]`.
- Downloaded local Gemma CQ4 bundle path is `vendor/cactus/weights/gemma-4-e2b-it-cq4`.
- Cactus `serve` exposes an OpenAI-compatible local HTTP server and has cloud handoff controls: `--no-cloud-handoff`, `--confidence-threshold <0..1>`, and `--cloud-timeout-ms <n>`.
- Focused upstream verification command: `cactus test --component <kernels|graph|engine|all> --suite <name> --list` to discover suites before running expensive model tests.
- On this x86_64 WSL machine, Cactus build failed after download because upstream kernels require ARM/NEON headers/types (`arm_neon.h`, `__fp16`); do not assume `cactus run/serve` works here without an upstream x86 fix or ARM64 environment.

## Upstream Needle 2 Facts
- Source: `https://github.com/cactus-compute/needle`; it is a Python package named `cactus-needle` with CLI entrypoint `needle`.
- Install/use upstream package with `pip install cactus-needle`; local upstream setup creates `.venv`, installs editable package, and requires Python `>=3.9`.
- Test extra is `cactus-needle[test]`; upstream pytest config uses `tests` and marker `slow` for end-to-end JAX build/finetune tests.
- Needle 2 is designed for structured tool calls, not free-text chat: unsupported/off-topic requests return an empty call `[]`.
- `needle.Needle(tools=..., system=..., weights=..., tool_index_path=...)` binds one session to one toolset; use `agent.reset()` to rewind while keeping tools loaded.
- Prefer `agent.complete(...)` over `agent.run(...)` when implementing user approval, because `complete()` lets this app inspect the proposed call before executing it.
- Needle response objects include `type`, `function_calls`, `reasoning`, `confidence`, and performance fields; gate execution on `confidence` before asking for approval.
- Tool schemas should be precise: argument descriptions, `Literal` choices, and `needle.Field(...)` constraints are compiled into the decode grammar.
- With more than five tools, Needle retrieves only the top five and rebuilds the grammar for that subset; persist embeddings with `tool_index_path` for large catalogs.
- Needle system text is for facts like `date`, `locale`, `device`, `battery`, `network`, `location`, `user`, and `assistant`; do not rely on it for behavioral instructions.
- Offline Needle inference uses cached engine files under `~/.cache/cactus-needle/<engine version>/`; set `NEEDLE_LIB_PATH` for an explicit engine and `HF_HUB_OFFLINE=1` to fail fast offline.

## Prototype Implementation Bias
- Keep Needle tool execution behind an approval boundary: propose call -> show arguments/reasoning/confidence -> execute only after approval.
- Model routing should be explicit and observable in the TUI: `needle_proposed`, `user_approved`, `needle_executed`, `needle_rejected`, `gemma_escalated` are useful states to expose.
- Keep tool definitions close to the code that executes them; avoid separate natural-language prompts as the source of truth for tool behavior.
- If adding Postgres vectors, prefer `pgvector` in Postgres over a separate vector DB unless a local requirement proves otherwise.
- Current embeddings are deterministic local hash vectors in `modules/embeddings.py` so pgvector is functional before adding a dedicated embedding model.
- Do not add cloud dependencies for the core loop unless explicitly needed; the requested experiment is local-first.
