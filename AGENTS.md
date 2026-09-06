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

## Weights-Loading-Verhalten (wichtig für Telegram-App!)
- **Embedding-Modell wird LAZY geladen**: Der Sentence-Transformers-Encoder (`paraphrase-multilingual-MiniLM-L12-v2`, ~470MB) wird erst beim ERSTEN `router.route()`-Aufruf geladen. Das bedeutet: Der erste User-Request nach dem Start dauert ~10-30s (Modell-Loading), alle weiteren sind schnell (~100ms).
- **Fix: Router-Warmup beim Start** (`needle-only/tg.py`, `main()`): Direkt nach `build()` wird `_get_router()` initialisiert und `router.route("warmup")` aufgerufen. Das lädt das Modell VOR dem ersten User-Request. Die Telegram-App ist erst "ready", wenn die Weights geladen sind.
- **Needle 45M**: Wird beim `needle.Needle()`-Konstruktor geladen (~2-5s). Per-Tool-Sessions werden lazy erstellt (`_get_extract_session`), aber das Modell wird nur einmal in den Speicher geladen (JAX cached die Weights).

## v5.5: Notizen entfernt, Absence-Kind, Kollisions-Fix

### Was hat sich geändert:
- **Notizen ENTFERNT** (Nutzer-Wunsch: verschlanken, Erinnerungen gehen bereits)
- **`absence` Kind hinzugefügt**: Urlaub/Reise/krank — mehrtägig, kollidiert NICHT mit Terminen
- **Kollisions-Logik gefixt**: NUR appointment+appointment (±30 min)
- **Wochentag-Korrektur**: Needle 45M berechnet 'dienstag' oft falsch (Mo statt Di) — wir berechnen Wochentage selbst
- **Multi-Day-Support**: 'von 07.09. bis 11.09.' wird geparst → start_at + end_at
- **`calendar_filter` Tool**: Gruppen-Abfragen ('wann hat lisa diese woche termine')

### Kollisions-Design (klassischer Kalender-Kram):
```
appointment + appointment → ⚠️ Kollision (±30 min)
appointment + absence     → ✅ Koexistiert (Urlaub + Termine gleichzeitig)
absence + anything        → ✅ Koexistiert
reminder + anything       → ✅ Koexistiert
```

### Weights-Loading-Verhalten (wichtig für Telegram-App):
- **Embedding-Modell wird LAZY geladen**: Der Sentence-Transformers-Encoder wird erst beim ersten `router.route()`-Aufruf geladen. Erster User-Request dauert 5-10s (Modell-Loading), alle weiteren sind schnell (~100ms).
- **Fix: Router-Warmup beim Start** (`tg.py` main(), Zeile ~361): `_router.route("warmup")` lädt das Embedding-Modell VOR dem ersten User-Request.
- **Needle 45M**: Wird beim `needle.Needle()`-Konstruktor geladen (~2-5s auf RPi 5). Nur EIN Modell-Load pro Prozess — JAX cached die Weights.

### Aktuelle Tool-Liste (5 Tools):
1. `calendar_create` (appointment/reminder/task/absence)
2. `calendar_edit`
3. `calendar_read` (mit person-Parameter)
4. `calendar_delete`
5. `calendar_filter` (Gruppen-Abfragen, Markdown-Output)

### Eval-Suite: 25/25 Fälle (ø 3.3s/Fall)
- 5x calendar_create (Termine: absolut/relativ/mit Ort/ISO/Dienstag)
- 1x Kollision (verschiedene Titel, gleiche Zeit → Kollision erkannt)
- 3x Erinnerungen (2min/in 10min/medikamente morgen früh)
- 2x Aufgaben (aufgabe/aufgabe-deadline)
- 2x Urlaub (urlaub ohne termin, urlaub mit terminen → KEINE kollision)
- 2x calendar_edit (verschieben/uhrzeit)
- 4x calendar_read (all/erinnerungen/termine/tasks)
- 3x calendar_delete (titel/explicit/datum)
- 1x calendar_filter (person: lisa)
- 2x NOWRITE (allgemeinwissen/chitchat)

## v5.6: Gruppen-Support (29/29 Eval)

### Was wurde hinzugefügt:
- **owner-Parameter**: Termine FÜR andere Personen ("erstelle einen termin zahnarzt für lisa")
- **participants-Parameter**: Termine MIT Personen ("termin meeting mit lisa")
- **calendar_read mit person-Filter**: "wann hat lisa diese woche termine"
- **free_slots-Tool**: gemeinsame freie Zeitslots für Gruppen ("wann haben lisa und max gemeinsam zeit")

### Neue Tools (6 Tools):
1. `calendar_create` (appointment/reminder/task/absence, mit owner/participants)
2. `calendar_edit`
3. `calendar_read` (mit person-Parameter)
4. `calendar_delete`
5. `calendar_filter` (Gruppen-Abfragen, Markdown-Output)
6. `free_slots` (gemeinsame freie Zeitslots)

### Person-Extraktion (fix_args):
- `"für lisa"` → `owner='Lisa'`
- `"mit lisa"` → `participants=['Lisa']`
- Router entscheidet Tool, Needle extrahiert Argumente, fix_args macht Post-Korrektur

### Zeitzonen-Fix (orga.py):
- `_localize()`: DB-UTC-Datetimes nach Europe/Berlin konvertieren
- `_fmt_day()/_fmt_time()`: zeigen jetzt lokale Zeit statt UTC (07:00→09:00 Bug behoben)
- `by_day` Gruppierung lokalisiert (0-2 Uhr CEST-Einträge korrekt zugeordnet)

### Weights-Loading-Verhalten (wichtig für Telegram-App):
- **Embedding-Modell wird LAZY geladen**: Der Sentence-Transformers-Encoder wird erst beim ersten `router.route()`-Aufruf geladen. Erster User-Request dauert 5-10s (Modell-Loading), alle weiteren sind schnell (~100ms).
- **Fix: Router-Warmup beim Start** (`needle-only/run.py`, `_get_router()`): `_router.route("warmup")` lädt das Embedding-Modell VOR dem ersten User-Request. Die Telegram-App ist erst "ready", wenn die Weights geladen sind.
- **Needle 45M**: Wird beim `needle.Needle()`-Konstruktor geladen (~2-5s auf RPi 5). Nur EIN Modell-Load pro Prozess — JAX cached die Weights.

### Eval-Suite: 29/29 Fälle (ø 2.6s/Fall)
- 5x calendar_create (absolut/relativ/mit Ort/ISO/Dienstag)
- 1x calendar_create-kollision (gleiche Zeit → Kollision erkannt)
- 3x Erinnerungen (2min/in 10min/wasser trinken)
- 2x Aufgaben (aufgabe/aufgabe-deadline)
- 2x Urlaub (urlaub ohne termin, urlaub mit terminen → KEINE kollision)
- 2x calendar_edit (verschieben/uhrzeit)
- 4x calendar_read (all/erinnerungen/termine/tasks)
- 3x calendar_delete (titel/explicit/datum)
- 1x calendar_filter (person: lisa)
- 1x group-create-fuer-lisa (owner-Parameter)
- 1x group-create-mit-person (participants-Parameter)
- 1x group-free-slots (free_slots mit Lisa+Max)
- 1x group-free-slots-verfuegbar (free_slots mit Lisa allein)
- 2x NOWRITE (allgemeinwissen/chitchat)
