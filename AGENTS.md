# AGENTS.md

## Security: `.env` & Secrets

**Die `.env` wird NIEMALS gelesen.** Kein `cat .env`, kein Read-Tool, kein `grep` auf die Datei. Sie enthält Secrets (HF-Tokens, API-Keys, DB-Credentials) und ist in `.gitignore` — sie darf niemals committet werden.

Regeln:
- **Lesen verboten:** Wenn ein Agent Zugriff auf Secrets braucht (z.B. HF-Upload), lädt ein Skript die `.env` via `python-dotenv` (`load_dotenv()`) intern. Der Token-Wert wird nie ausgegeben, geloggt oder in Commits aufgenommen.
- **Fragen vor Debugging:** Wenn für Debugging-Zwecke Zugriff auf Secrets nötig wäre, FRAGE VORHER den Nutzer.
- **Keine Secrets in Logs/Commits:** Niemals Token-Werte in Print-Statements, Log-Files oder Commit-Messages ausgeben.
- **`.env.example` ist ok:** Diese Datei enthält nur Keys ohne Werte und kann gelesen werden.

## Repo State
- This is a prototype workspace for local Cactus/Gemma + Needle 2 experiments, NOT an upstream checkout of `cactus-compute/cactus` or `cactus-compute/needle`; those are cloned into gitignored `vendor/` for source reference.
- Target is ARM64 (RPi 5 8GB); Cactus kernels build and run there, unlike the old x86_64 WSL machine.
- Python environment is managed with `uv`; dependencies are in `pyproject.toml` and the app is launched with `uv run python scripts/launch.py`. `launch.py` auto-creates `.env` from `.env.example` if missing, so the initial `cp` is optional.
- Keep Python modules small; user explicitly asked for `.py` modules around 100 lines max and functionality split under `modules/`.
- **Calendar-FT (kanonisch):** `needle-only/experiments/ft/` — FT-Dataset v2/v3, `train_rtx.py`, `modal_train.py` (Cloud-FT), `base_eval.py`/`multi_call_bench.py` (Eval), `RESULTS.md`/`NEEDLE3_FT_RESULTS.md`/`TRAINING_ENV.md`/`BASELINES.md`/`ARCH_BAKEOFF.md` (Doku), `promotion_gate.py` (N3-Gate), `arch_bench.py`+`arch_cases.py` (P0/P1/P2-Bakeoff), `native_agent_bench.py` (Track B). **Legacy/Archiv:** `needle-only/calendar_ft/` (ältere Needle-2-3-Adapter: calendar_write/read/reminder, Jetson) und `needle-only/calendar_ft_all/` (verworfenes Merged-Experiment) — nur Referenz, nicht weiterentwickeln. Das Needle-2-FT auf Dataset v2 bleibt Produktionsreferenz, bis ein Needle-3-Kandidat das Promotion-Gate (`BASELINES.md`) hält.

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
- `needle-only/` **v1 „local-calendar"** (September 2026, ersetzt den alten v5/v6/v3.1-Stack): eigenständiges uv-Projekt (Python 3.12, `uv sync` im Ordner). Minimaler Kalender-Agent: Needle 2 (`cactus-needle==2.0.13`) + optional Gemma 4 E2B über den laufenden `cactus serve` (HTTP, `CACTUS_BASE_URL`, kein zweiter Modell-Load). Domäne in `src/local_calendar/calendar.py` (SQLite `data/calendar.db` direkt, halboffene Intervalle, 2 Kinds: appointment/absence, DST/Monats-/Jahresgrenzen getestet); Modellpfad + Orchestrator + Trace in `agent.py` (`gemma_normalize → needle_complete → resolve → verify → repair(max 3) → execute`, Call-Merge + Dedup, trailing-Perioden-Strip); 4 Gradio-Tabs in `tabs/` (Assistant-Live-Trace, Calendar-Wochenraster+Busy/Free, Debug-Trace-Historie, Needle Lab complete/run/extract). Start `uv run local-calendar [--mode needle|hybrid]`; Tests `uv run pytest` (25 grün: 23 Unit + Needle-Integration suite-level + E2E 20 Cases, Threshold 75 %); Eval-CLI `uv run python tests/test_e2e.py [--repeat n] [--hybrid]`. Gemessen: Confidence unkalibriert (Off-topic-Refusals scoren ~0.99, valide Calls teils ~0.0 → Floor-Default 0), EN „Show my appointments“ routet varianzbehaftet, Mehrtägig-Absences nur teils sauber, Hybrid stabilisiert DE (+95 %) kostet aber ~7 s vs ~1 s. Bug-Fixes Sep 13: Modell rechnet Datumsangaben falsch → explizites Datum im Text gewinnt deterministisch (`extract_date_from_text` + `_fix_text_date`, Kontext durch execute_call), vergangene Daten rollen mit Jahreszahl im Confirm-Text; Repair-Loop feuert jetzt auch bei fehlgeschlagener Ausführung (§24); Gemma-Canonical bewahrt Intent-Wörter („Termin X löschen“ erzeugte sonst CREATE); Kalender-Grid rendert Events nur im belegten Zeitraum (vorher: jeder Folgetag, halboffene Slot-Intervall-Tests); Playwright-UI-Checks in `tests/ui_check.py` (Create/Move/Delete im echten Chromium). Review-Fixes Sep 13 (r2): _merge_calls behält non-persons-Args (nur persons gemergt), Duration-Bug in der Datums-Korrektur gefixt (Dauer vor Start-Shift), collision participant-aware (nur gemeinsame Teilnehmer; Absence blockiert Termine derselben Person — konsistent mit free_slots, Absence-Anlegen kollidiert nie), CalendarStore.get() gefixt (+Test), Needle-Lab: schemas() existiert, use_facts wirkt bei complete/run (Session-Cache mit Facts-Flag), Resolver nutzt date/time/until, E2E-Eval prüft finale Semantik (expected: all_day/span_days/participants in eval_cases.json; ehrlicher Score 70 % vs Tool-Only ~90 %; Suite-Threshold 0.6), Calendar-Tab: Teilnehmer-Choices refreshen bei tab.select/Refresh, Availability folgt dem Wochen-Offset, share=True per --share-Flag (Default aus, LAN zuerst). Agent-v2 (Sep 13, Phasen 1-14 des Entwicklungsplans): calendar_list echte Query (date/until, keine Roll für Reads), calendar_create end_time (end>time-Verifikation), Multi-Call (MAX_TOOL_CALLS_PER_STEP=10), Gemma als iterativer Controller (ControllerDecision continue/ask_user/finish via CONTROLLER_SYSTEM-JSON, Dekomposition vor Needle, Observation-Loop, MAX_AGENT_STEPS=8, Loop-Guards), ask_user mit Pending-State über Turns (Agent.pending), E2E-Cases erweitert (historical/range read, end_time, multi-create, mixed multi-action; hybrid_only-Cases nur via main --hybrid), Assistant-Tab zeigt Agent-Iterationen. Bekannt: resolve_date parst ISO-Datetime; Gemma-Qualitäts-Lücken sichtbar (Titel 'Appointment X', 'TÜV'→'Television', gelegentliche falsche Zeiten) — dokumentiert für Phase 29 (reale Fehler -> richtige Schicht). Telegram-Phase (Sep 13, Plan 16-30): `src/local_calendar/telegram.py` (dünner PTB-22.8-Adapter, Long Polling, python-dotenv lädt Root-.env — Secrets nie geloggt), Allowlist/Owner-Security, /start /week /today /debug (Owner-only), Inline-Navigation Woche/Tag/Teilnehmer deterministisch, Pillow-Renderer `render.py` (Wochen-Snapshot ohne leere Rasterzeilen, Availability-Widget), Widget-Auswahl aus Trace-Steps (kein Regex), ein Agent/Store/Trace-Format für Gradio+Telegram (Telegram-Traces landen automatisch in traces.jsonl), Phase 29/30: reale Regression-Fälle in eval_cases.json (letzter-Wochentag-Delete, Tag-Range 'vom 23. bis 27.', resolve_date 'letzten dienstag'/last-Weekday + day-only-Range mit Uhrzeit-Guard, extract_dates_from_text Pattern-Hierarchie exklusiv). needle-only-Suite: 17/25 ehrlich (Refusal-Varianz mit frozen facts + Modell-Lücken dokumentiert); multi-create/mixed sind hybrid_only (Gemma-Zerlegung ist der Plan-Weg). Hardening-Pass (Sep 13, Review-1-21): move prüft Kollision VOR dem Write (candidate, revert-frei), find_candidates (0/1/>1 Treffer) -> move/delete bei Ambiguität Rückfrage statt stiller Mutation, Delete-Datum = Hard-Filter (12.9. löscht nie 17.9.), find_slot date/until-Zeiträume, Availability-Renderer zeigt exakt das ausgeführte Solver-Result (resolved: persons/first_day/last_day/duration/window/slots — kein Re-Schedule), render_day_png + _fit-Texttruncation, People-Buttons in 3er-Rows, Pending-State pro session_id (Telegram chat_id, Gradio 'local'), inference_lock (threading) serialisiert Needle-Calls, Gemma-unavailable-Guard (kein steps[-1]-IndexError, Fallback dokumentiert im Trace), Controller sieht completed-Liste + completed-Instruction-Guard, toter Code entfernt (Gemma.repair/respond, MAX_REPAIRS; canonicalize+CANON_SYSTEM bleiben fürs Needle-Lab), .env.example korrigiert (Owner-ID explizit), tests: deterministic (test_calendar 37 + telegram stubs) getrennt von Modell-Evals (needle-Marker). Controller-Qualität (Gemma folgt JSON-Protokoll teils unzuverlässig: über-asking, premature finish) = bekanntes Gemma-Planning-Limit, Phase-29-Material. Stabilisierungs-/Bakeoff-Pass (Sep 13 r2, Phase 1-16): Trace-Klassifikation (Session-Pending-Verunreinigung, generischer Delete-Titel, Gemma create-vs-delete) -> Fixes: resolve_event (generische Event-Identifikation: title/date/time optionale Identifier AND-verknüpft, 0/1/ambiguous-Status — ersetzt Delete-Sonderfall), Delete-Zeitfenster-Rückfallebene, Controller-Regeln (failed delete nie create; neue Selbststände verwerfen Pending), /cancel, 5 reale Delete-Regressionen, Widget-Datenpfad-Tests (gleicher Tag/Multi-Event/langer Titel/Urlaub+Termin/Filter/Wochenstart/Mitternacht/Mutation-Refresh), find_by_title-Flake (now()->near) gefixt. Bakeoff v2 (needle_bakeoff_v2.py, kontrollierte Ablation): 21 Cases × raw+canon × 3 Repeats, echte Produktionsschemas, 3 Metriken (tool-role/arg-semantisch/final-DB) + exact-call-stability: PROD 0.905 tool/0.476 args/0.619 final — Naming (create_event) 0.786 verworfen, 10 Orders identisch (Order egal), Split 0.714/0.69 verworfen (auch ohne Retrieval-Konfundierung), args_ok = Flaschenhals -> FT auf eingefrorenes Toolset. run(): strict grounding verweigert berechnete ISO-Args (10/20 ungrounded); extract(): single exakt, Arrays 0, iterativ-solo halluziniert -> Gemma-split + extract(single) wäre nutzbar. Loop-Vergleich: Controller gewinnt multi-create (Dekomposition), needle-raw gewinnt simple Reads (12x schneller); find-slot->move-Kette scheitert in beiden (offen). Harness-Review-Fixes (Sep 13 r3, Bakeoff v2.1): run_orders reihenfolgte NICHT (gefixt: echte Schema-Reihenfolge), Resultat: 10 Orders → 0.69-0.762 (kein robuster Order-Effekt); canon-Form wurde in run_block nie gemessen (form-Parameter), raw/canon jetzt getrennt lauffähig; run(strict) wurde nie übergeben (gefixt: strict=False ändert die ungrounded-Quote NICHT — Engine erwartet verbatim-evidenced Werte); Persons-Metrik subset statt intersection; final_ok = exec_ok AND state_ok (Execution-Fails wurden verschluckt); jeder Case frischer Store; contract_ablation.py: Contract A (date/until/time) 0.90 tool/0.40 args/0.725 final vs Contract B (symbolische date_expression-Namen) 0.65/0.375/0.65 mit 0.275 Refusals — symbolischer Contract VERWORFEN; extract(single) über alle create-Cases 8/17 befüllt (Arrays 0, iterativ-solo halluziniert); resolve_event ersetzt find_candidates (generische Identifikation); loop_compare final-DB korrigiert (result_contains); Modell-Eval-Thresholds als Mindestwerte markiert (deterministische Tests bleiben 100 % in test_calendar.py). ArgEx-Pass (Sep 13 r3, Plan-Phasen 1-13): per-field-Report (date 0.63/until 0.33/time 0.64/horizon 0.00 = 70 % des Problems; title 0.80; end_time/duration 1.00); Two-Call-Pipeline (tool-select via complete -> extract(per-tool Pydantic-Schema)) in allen Varianten SCHLECHTER als complete()-Baseline (args 0.025-0.125 vs 0.40) — Schema-Name/-Docstring sind Verhaltensfaktoren ('to create' -> null, 'described in text' -> exakt); None-Defaults ohne Effekt; Temporal-Expression-Contract schlechter; field-level-repair verworfen (extract generisch <50 %); Mutationen laufen bereits by event_id (resolve_event). FAZIT: complete() bleibt Produktionspfad; args_ok 0.4-0.48 = harte Base-Needle-Grenze -> FT (Plan §14) der nächste Hebel. Contract-Minimization (Sep 13 r4, Plan §1-10): Minimal-Contract (list/find ohne horizon/days, Handler-Defaults deterministisch) vs Produktion — 21 Cases x raw/canon x 3 Repeats: args 0.375 vs 0.400, final 0.60 vs 0.65, Refusals 0.125 vs 0.05 (2.5x) -> VERWORFEN nach Akzeptanzregel (§8: args +0.10 UND final gleich UND Refusals stabil — keiner erfuellt); isoliert hilft Minimal nur bei until (0.5 vs 0.33). Bonus-Erkenntnis: kanonische Sätze NICHT besser als rohe Anfrage (args 0.30 vs 0.40) — Gemma-Instructions sollten knapp/direkt bleiben. Base-Needle-Contract damit eingefroren. FT-Dataset v2 (Sep 13/16, Plan §1-18): `needle-only/experiments/ft/` — deterministischer seedbarer Generator (seed 42) über die FÜNF Produktionsschemas (auto-import via build_tools()+fn._needle_tool, KEINE Schema-Duplikation, schema-hash-Check im Validator); Splits strikt nach Template-Familien (44 Familien, split-exklusiv), eval-only Value-Pools (TÜV/Miriam/Felix) mit Wortgrenzen-Leak-Check; Sprachmix 60% kurzes DE / 25% Gemma-atomic / 15% EN-simple (Finding ac150d1: kurz/direkt schlägt Kanon-Sätze); Gold-Format: alle Schema-String-Felder present, nicht-evidente "" , ints/enums tragen Defaults (duration_min=60, days=7, horizon=week); Grounding: String-Args = Substrings, dokumentierte Ausnahmen (horizon-Enum, delete ohne time-Arg, daypart→bare-day für move/find_slot); Negatives 8.4% (off-topic 2/3 + calendar-adjacent) über deterministische Kombi-Pools mit Split-Slices (garantiert cross-split-disjunkt); 25 frozen Challenge-Items (14 reale Telegram-Traces + 11 reale Regressionen); Validator hart (unbekanntes Tool/Arg, Typen, required, Dup-Keys, Familien-Leak, held-out-Leak, temporelle Klassen) + Coverage-Report; Manifest: commit sha ac150d1, seed 42, schema-hash d4fd45a3, file-sha256. Ist-Stand: train 9940 / val 1000 / test 1800 (create 32%, find 22%, move 19%, list 10%, delete 9%, neg 8.4%). Frozen Base-Baseline (1 Repeat, Pi): TEST n=1800: tool_ok 0.863, args_ok 0.310, exact_args 0.057, refusals 0.081, median 1140ms (date 0.544/until 0.357/time 0.935/persons 0.732/horizon 0.006/duration 0.012/days 0.002); CHALLENGE n=25: tool_ok 0.920, args_ok 0.280, exact 0.000, refusals 0.080. Trainings-Hyperparameter-Matrix (rank 8/16, LR 5e-5/1e-4, epochs 3/5/8, 3 Seeds), Erfolgskriterien und 3-Ebenen-Eval sind im ft/README fixiert; train_rtx.py (OHNE Jetson-Hacks, §20) folgt auf der RTX-Maschine — KEIN Training vor FT-Freigabe. Pre-FT-Review-Pass (Sep 17, Nutzer-Review 4 Punkte): (1) Gold-Konvention SPARSE/evidenced-only statt Full-Defaults — gold_ab.py bewertet dieselben Base-Outputs gegen beide Konventionen: exact_full 0.005 vs exact_sparse 0.077 (Test) / 0.000 vs 0.160 (Challenge), Base lässt Handler-Defaults ~99% weg (missing_default 142/142 horizon, 282/282 duration, 288/288 days); Needle-eigener Finetune-Generator (needle/model/finetune.py _GEN_TEMPLATE) schreibt "only values evidenced in the query" vor → Dataset auf sparse umgestellt (leeres arguments:{} legal, z.B. list_question ohne Args); Base-Baseline neu bewertet (--from-cache, got-Args cache-basiert): args_ok 0.449/0.520 produktionstolerant, exact 0.113/0.120 — Hauptfehler = Span-Kopier-Shift (Base liefert ISO statt Spans). (2) Manifest-Provenienz: provenance.build_dataset_sha256/dataset_spec_sha256/schema_sha256/seed statt falschem Parent-Commit (generated_at_commit nur informativ). (3) README "JSONLs nicht committet" korrigiert (sie sind committet, ~6.8 MB). (4) Generator: exakte Counts garantiert (sequenzielle Negativ-Indices statt 7*i-Wrap, Pool-Guard, Hard-Fail bei count mismatch — kein still 9940 statt 10000). Alter Stack (orga/run/tools/router/tg/world, Postgres, Semantic-Router) ist aus Git wiederherstellbar (vor tg_v6.py etc. nach /tmp/opencode/needle-only-vold-backup/ gesichert).
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

---

## Calendar-FT: Task-spezifische Needle-Feintuning-Experimente

> **LEGACY/ARCHIV (überholt).** Dieser Abschnitt beschreibt den früheren
> `needle-only/calendar_ft/`-Strang (Needle-2/3-Adapter auf Jetson). Der
> **kanonische FT-Pfad ist `needle-only/experiments/ft/`** — siehe
> `experiments/ft/BASELINES.md` (eingefrorene Baselines + Promotion-Gate),
> `RESULTS.md` (N2-FT), `NEEDLE3_FT_RESULTS.md` (N3-FT), `TRAINING_ENV.md`
> (Umgebung/Modal). `calendar_ft_all/` war ein verworfenes Merged-Experiment.
> Hier nur noch Referenz — nicht weiterentwickeln.

**Ziel:** Verbessert task-spezifisches Needle-FT die Kalenderbedienung signifikant?
Drei separate LoRA-Adapter (calendar_write, calendar_read, reminder) wurden auf
dem Jetson AGX Orin trainiert.

### Ergebnisse (Base vs FT, Eval-Datasets)

| Task | Base Exact | FT Exact | Base F1 | FT F1 | FT Halluc. |
|---|---|---|---|---|---|
| calendar_write | 0.127 | **0.287** | 0.402 | **0.752** | 3.1% |
| calendar_read | 0.117 | **0.175** | 0.184 | **0.494** | 5.9% |
| reminder | 0.092 | **0.233** | 0.493 | **0.565** | 20.1% |

E2E (10 Cases): **90% Pass Rate, 100% Route Accuracy**

### Training auf dem Orin (GPU)
- **Gesamt: 5.4h** (calendar_write 3.9h, calendar_read 1.0h, reminder 0.5h)
- LoRA rank=16, alpha=32, lr=1e-4, 10 Epochs, QAT W4A8

### Dataset-Details
- **Nur Deutsch** — alle Templates, Value-Pools und Negatives sind deutsch
- Deterministisch, seedbar, mit Train/Eval-Split (0 Duplikate)
- 12% Negatives (Cross-Task + Off-Topic)
- Grounding-Prinzip: Argument-Werte sind Substrings des Queries

### Jetson-spezifische Workarounds (siehe `reports/environment.md`)
1. `unset LD_LIBRARY_PATH` — vermeidet Konflikt zwischen System-CUDA 12.6 und pip-CUDA 12.9
2. `XLA_FLAGS="--xla_gpu_autotune_level=0"` — umgeht sm_87 Autotuner-Crash
3. `XLA_PYTHON_CLIENT_PREALLOCATE=false` — verhindert 46 GiB Preallocation auf Unified Memory

### Modelle (für HuggingFace-Upload bereit)
| Modell | Größe | Task |
|---|---|---|
| `calendar_write.cact` | 23.2 MB | Termin erstellen/verschieben/absagen |
| `calendar_read.cact` | 23.2 MB | Kalender abfragen/anzeigen |
| `reminder.cact` | 23.2 MB | Erinnerungen setzen |

### E2E-Pipeline (Calendar-vNext)
```
User Query → Router (deterministisch) → Task-FT-Modell (.cact) → Planner → DB → Response
```

### Integration
- `needle-only/router_calendar.py` — deterministischer Router (Regex-Trigger)
- `needle-only/calendar_service.py` — Calendar-vNext-Service mit Base-Model-Fallback
- `needle-only/calendar_ft/e2e_eval.py` — E2E-Evaluation (10 Cases)

### Reproduktion
```bash
uv run python needle-only/calendar_ft/run_experiment.py --task calendar_write
# oder alle Tasks:
uv run python needle-only/calendar_ft/run_experiment.py --all
```

### Bekannte Probleme
1. FT-Modell verweigert bei fehlendem Date ("Missing required parameter: date") — Base-Fallback fängt das ab
2. Generalisierungslücke bei unbekannten Phrasings — Lösung: mehr diverse Phrasings im Training
3. Reminder-Modell schwächer (75% Tool-Accuracy) — komplexere Zeit-Auflösung
4. Dataset ist rein deutsch — für bilingualen Support erweitern + neu trainieren
