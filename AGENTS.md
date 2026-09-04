# AGENTS.md

## Repo State
- This is a prototype workspace for local Cactus/Gemma + Needle 2 experiments, NOT an upstream checkout of `cactus-compute/cactus` or `cactus-compute/needle`; those are cloned into gitignored `vendor/` for source reference.
- Target is ARM64 (RPi 5 8GB); Cactus kernels build and run there, unlike the old x86_64 WSL machine.
- Python environment is managed with `uv`; dependencies are in `pyproject.toml` and the app is launched with `uv run python scripts/launch.py`. `launch.py` auto-creates `.env` from `.env.example` if missing, so the initial `cp` is optional.
- Keep Python modules small; user explicitly asked for `.py` modules around 100 lines max and functionality split under `modules/`.

## Local Commands
- First run: `cp .env.example .env && uv sync`; use `uv sync --extra needle` for real Needle 2 instead of the heuristic fallback.
- Normal launch: `uv run python scripts/launch.py`; this starts Docker Compose Postgres, initializes pgvector tables, and opens `tui-app.py`.
- Manual DB start: `docker compose up -d postgres`.
- Project Postgres binds to host port `5433` by default via `POSTGRES_HOST_PORT` to avoid conflicts with other local Postgres containers.
- If Docker Hub pgvector pulls fail, set `.env` `POSTGRES_AUTO_START=0`; the TUI still starts, but DB tools need a reachable `DATABASE_URL`.
- Cactus auto-start is controlled by `.env` `CACTUS_AUTO_START=1`; otherwise start `cactus serve` manually before launch.
- Cactus installs from the PyPI `cactus-compute` manylinux_aarch64 wheel via `uv sync --extra cactus` (prebuilt native `libcactus_engine.so`). `vendor/cactus` is source/code reference only; do NOT pin `[tool.uv.sources]` back to `vendor/cactus/python` (the git tree ships no native libs).- No automated test suite exists yet; use `scripts/eval.py` (26 goldene Fälle, Baseline 25–26/26, braucht laufendes serve) plus `docs/manual.md` smoke tests für DB status, todo write, vector search, rejection, and off-topic routing.

## Current Code Map
- `tui-app.py` ist die Terminal UI/request loop; Hintergrund-Aktivität als Spinner (`modules/anim.py`), danach max. 4 kompakte Logzeilen; `/status`, `/logs`, `/graph` via `modules/shell_cmds.py`.
- `modules/pipeline.py` Turn-Pipeline: Toolwahl 100 % Gemma-FC (`modules/interpreter.py`, Few-Shots, Output-Normalisierung für Cactus-Quirks); deterministische Zeit-/Struktur-Fixes (`resolve_calls`) NACH der Toolwahl, nie als Tool-Ersatz; Needle nur bei Writes als advisory Zweitmeinung (`modules/needle_verifier.py`); Injection-Guard (kein Write nach `web_search` in derselben Runde); Follow-up-Loop abschaltbar (`Runtime.followup`).
- `modules/cactus_engine.py` calls local Cactus OpenAI-compatible HTTP (`chat/completions` mit `tools`/`tool_choice`, `temperature`, `reasoning_effort`), resolves served model id from `GET /models`.
- `modules/tool_catalog.py` OpenAI-Schemas (`schemas()`) + Needle-Funktionen + Ausführung; `search_records(table=calendar_events)` durchsucht die `events`-Tabelle; Termin-Duplikatschutz (±1 Tag); `WRITE_TOOLS` = Approval-Pflicht.
- `modules/postgres_store.py` relational (inventory/todos/knowledge + `events`/`event_changes`), pgvector (dim kanonisch via `EMBED_DIM`), Memory (messages/facts, `space`), Graph (`graph_nodes`/`graph_edges`), `purge_old_messages()` (Retention).
- `modules/facts.py` Mem0-Gedächtnis: ADD-only Fakten (`valid_from/valid_to`, `supersede`-Kante), Decay-gewichtetes Retrieval, `active_facts`.
- `modules/needle_router.py` hält die Needle-Instanz bereit; `modules/needle_verifier.py` macht den Cross-Check (semantische Argumente, advisory, nur Writes).
- `modules/timesync.py` lokal (`Europe/Berlin`), `resolve_dt` + `parse_calendar` (generalistisch, versteht „kommende woche sa. ab 12:30Uhr", „14 uhr").
- `modules/websearch.py` SearXNG; `modules/memory.py`/`reflect.py` Boot-Kontext bzw. Lernen (Graph + facts.record_fact) nach Writes; `modules/scheduler.py` Erinnerungen + Wartung (Fakten-Prune + Chat-Retention); `modules/shell_cmds.py` `/`-Befehle.
- `scripts/eval.py` + `scripts/eval_cases.py` Eval-Suite: 28 goldene Fälle (Routing, Argumente, NOWRITE/GATEDWRITE/SILENT/ANSWER), Baseline 28/28; Lauf: `uv run python scripts/eval.py [--filter x]` (braucht laufendes serve).
- `needle-only/` unabhängiger Mini-Stack **v3.1 „Plan-Werkstatt"**: Sprache = Diff-Spec für den Kalender-Zustand. `orga.py` = eigenes Schema (`n_events`/`n_reminders`/`n_notes`, space/owner, psycopg direkt, kein serve) + Planner: upsert-Semantik (add⟷move unmöglich zu verwechseln), pg_trgm-Fuzzy-Titel (Tippfehler), Endzustands-Kollisionsprüfung, atomare Ausführung, `free_slots`-Berechnung, gerenderte Ausgaben (LOCAL-Zeit); `tools.py` = 9 Tools; `run.py` = EIN Plan-Approval je Turn (y/n/e/q, `e` = Freitext-Korrektur → neue Needle-Runde), Multi-Op-Retry+Merge; `eval.py`+`eval_cases.py` = 22 Fälle + `--repeat`. Stand: **14/22, ø ~2,3 s, deterministisch**; Rest = Needle-Varianz (Transkription, Erinnerung/Termin-Verwechslung, fehlende Ops) — sicher abgefangen. Gelernt: EN-Docstrings+DE-Keywords, getrennte Tools (gemergtes action-Tool scheitert an Grammar), confidence unkalibriert, opus-mt-Übersetzung schadet. Gemma-Stack parallel: 28/28.
- `scripts/launch.py` creates `.env`, starts Postgres, optionally Cactus; `uv run python scripts/launch.py` zum Start.

## Key Decisions (v3, geschärft mit Nutzer)
- **Toolwahl 100 % Gemma** — keine Regex-Tool-Fallbacks (Nutzer: „wozu ein SLM, wenn ich Regeln hardcode?"). Deterministische Fixes nur für Zeit/Struktur nach der Wahl.
- **Needle nur bei Writes** (advisory); In-Chat-Approval bleibt; später Messenger-Status + Interventionsmöglichkeit.
- **Mem0-Gedächtnis in Postgres** (`modules/facts.py`): ADD-only + valid_to/supersede + Decay — Lernen ohne Finetuning.
- **Injection-Guard** für `web_search` (Ergebnisse als Daten, kein Write in derselben Runde).
- **Roh-Chat-Retention** `MSG_RETENTION_HOURS` (Default 24 h, nächtliche Wartung).
- Endpoint austauschbar: alles über OpenAI-kompatible Base-URL (llama.cpp/ollama/anderer SoC = Config-Swap).

## Product Direction
- Goal: locally test converted Gemma-4 E2B with Cactus and connect it to Needle 2 for tool calling.
- Desired routing: prefer Needle for structured tool-call selection; require user approval before executing actions; on non-approval, low confidence, off-topic input, or complex requests, escalate to Gemma.
- Target prototype: terminal UI showing model/inference state, Postgres DB/vector-store state, and a Needle tool catalog.
- Data target: Postgres sollte normal relational data plus vector search für inventory, calendar, todos, und knowledge/RAG-style context abdecken; zusätzlich ist eine minimale Graph-Schicht (`graph_nodes`/`graph_edges`, `add_node`/`add_edge`/`neighbors`) vorbereitet (Ziel: db + Vektoren + Graph für den silent Agent).
- TUI ist bewusst ein ruhiger, farbiger Chat-Teststand: Konversation nur; Status-Block nur via `/status`, Befehle via `/help`.

## Phase 3 Roadmap (mit Nutzer abgestimmt, Hardening + Messenger)
- **Messenger-Layer (Telegram zuerst, dann Matrix E2EE)**: Reminder/Status als Push (Kanal-Muster steht in `scheduler`+`_reminder_line`), Approval als Inline-Button/Reply (Interventionsmöglichkeit statt Chat-y/n), Paaring + „inbound = untrusted" (OpenClaw-Lektion), Trigger konfigurierbar (`&botname`).
- **report/availability**: `report` (tag/woche) als deterministische DB-Aggregation + Gemma-Formulierung; `availability` über `events` (start/end, participants) + Graph.
- **Nacht-Konsolidierung 2–4 Uhr**: Roh-Chat → sanitisierte Fakten/Graph, danach Roh+Embeddings löschen (heute: Retention 24 h via `MSG_RETENTION_HOURS`); pgcrypto opt-in prüfen.
- **Robustness**: serve-Watchdog (hängt nach abgebrochenen Läufen → Neustart-Hinweis/auto), Retry/Backoff auf Gemma-Calls, Arg-Validierung (Mengen ≥ 0, Timer-Bounds 1..1440 min), Misroute-Log für spätere Few-Shot-Erweiterung (kein Regex-Routing).
- **Eval-Erweiterung**: Mehrsprachig (EN), Multi-Intent (2 Calls), Reminder-Trigger-Fälle; Baseline-Ziel ≥ 27/28 stabil.

## Upstream Cactus Facts
- Source: `https://github.com/cactus-compute/cactus`; docs from upstream README were the only verified source because this workspace had no local code.
- Upstream setup is `source ./setup`, not `./setup`; it creates `venv`, requires `python3.12`, installs `python/requirements.txt`, and installs the Python CLI from `python`.
- Linux prerequisites listed upstream: `python3.12 python3.12-venv python3-pip cmake build-essential libcurl4-openssl-dev`.
- Local model commands from upstream: `cactus convert <model> [dir]`, `cactus download [model]`, `cactus run [model|path]`, and `cactus serve [model]`.
- Gemma default in upstream Cactus test/benchmark help is `google/gemma-4-E2B-it`; Cactus README examples mention Gemma-4-E2B-CQ4 and `cactus run Cactus-Compute/needle [--tools my_tools.json]`.
- Downloaded local Gemma CQ4 bundle path is `~/.cache/cactus/weights/gemma-4-e2b-it-cq4` (NOT `vendor/cactus/weights`). Set `.env` `CACTUS_MODEL` to that path (or HF id `Cactus-Compute/gemma-4-E2B-it`); `cactus download <id>` writes there.
- The `serve` OpenAI-compatible API 404s unless `model` matches the served id (e.g. `gemma-4-e2b-it-cq4`); `modules/cactus_engine.py` resolves it from `GET /models` — do not hardcode `"local"`.
- Cactus `serve` exposes an OpenAI-compatible local HTTP server and has cloud handoff controls: `--no-cloud-handoff`, `--confidence-threshold <0..1>`, and `--cloud-timeout-ms <n>`.
- Focused upstream verification command: `cactus test --component <kernels|graph|engine|all> --suite <name> --list` to discover suites before running expensive model tests.
- This target is ARM64 (RPi 5 8GB), where Cactus kernels build and run. Historical: kernels need ARM/NEON types (`arm_neon.h`, `__fp16`); the old x86_64 WSL Cactus build failed because of that — do not assume that failure applies here.

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
- Embeddings: real local vectors via Cactus `POST /v1/embeddings` (same Gemma bundle, dim auto-probed, ~1536); deterministic hash vectors only as offline fallback in `modules/embeddings.py`. Recreates tables if dim changes.
- Do not add cloud dependencies for the core loop unless explicitly needed; the requested experiment is local-first.
