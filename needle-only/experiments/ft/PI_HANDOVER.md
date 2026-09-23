# PI-HANDOVER — Stand & Aufgaben für den Raspberry Pi

Kurz-Übergabe für den Agenten auf dem Pi (Repo + Git-History sind die Quelle).
Ziel dort: die **Gemma/`cactus serve`-Evaluationen** fahren, die auf der RTX-Box
nicht möglich waren (kein lokales Gemma). Nichts Bestehendes umdefinieren.

## 1. Lese-Reihenfolge (alles in `needle-only/experiments/ft/`)

1. `BASELINES.md` — eingefrorene Referenzen, Promotion-Gate, Namensregeln, Turn-Semantik
2. `ARCH_BAKEOFF.md` — P0/P1/P2-Business-Bakeoff (133 Fälle) inkl. Lücken
3. `READ_WRITE_SPLIT.md` — Toolset-Ablation, Availability-Kernel, Zielarchitektur
4. `DECOMPOSER_CONTRACT.md`, `RESULTS.md`, `NEEDLE3_FT_RESULTS.md`, `TRAINING_ENV.md`
5. Root: `AGENTS.md` (Regeln) — **`.env` niemals lesen** (Secrets)

## 2. Gesicherter Stand (freeze — nicht umdefinieren)

Pyramide: **A1 atomic** (`base_eval.py`) · **A2 one-shot multi** (`multi_call_bench.py`,
Legacy-Score + `multi_independent`) · **B agentic** (`native_agent_bench.py`) ·
**C production E2E** (`tests/test_e2e.py` → `final_db_ok`).

| Modell | A1 atomic tool/args/exact | A2 legacy/indep | C final_db | Track B (dep. chains) |
|---|---|---|---|---|
| **n2-FT seed44** (Referenz) | 0.991/0.977/**0.977** | 0.30/0.30 | 72 % | 0.0 |
| **n3-preserve e5** (bester N3) | **1.000/0.889/0.886** | **0.90/0.90** | 80 % | 0.0 |
| n3-base | 0.746/0.230/0.063 | 0.80/0.80 | 52 % | 0.0 |

**Ablation (frozen):** N2-FT **write-only 0.984 exact / tool 1.000** (stärkste
Write-Konfiguration), N2-FT read-only 0.957; N3 verengt schwächer (0.816/0.600).
**Kernel:** `availability_kernel.py` identisch zu `find_free_slots` (60/60),
**2,8–4,0× schneller**. **Track B:** echt abhängige Ketten löst kein Modell (0/10),
`run()` == manual; reset() ist Pflicht (Kontext-Poisoning belegt).

**Entscheidung bisher:** Kein weiteres N3-FT, keine Migration. n2-FT + deterministische
Prüfung ist Produktionsreferenz; N3 ist Multi-Call-stark (0.90) → Kandidat als
Decomposer.

## 3. Was der Pi liefern soll (die offenen Messungen)

| # | Aufgabe | Warum nur auf dem Pi |
|---|---|---|
| P-a | **P0-Referenz**: bestehender Hybrid `uv run python tests/test_e2e.py --hybrid` | braucht `cactus serve` |
| P-b | **P0 in `arch_bench.py`**: `--pipeline hybrid` implementieren (Gemma→N2 über den vorhandenen `Agent`, mode=hybrid) und fahren | dito |
| P-c | **P2/P3 mit echtem Fallback**: in `arch_bench.py --pipeline fallback` die Eskalation statt „gemma n/a" an den `Agent(mode=hybrid)` geben | dito |
| P-d | **Gemma als Read-Handler**: `gemma_read_probe.py` (Python-Fakten → Gemma formuliert; 20 Fragen) | dito |
| P-e | **Pi-Metriken**: Latenz p50/p95 + Peak-RAM für P0/P1/P2 | Zielhardware |

Kernmetriken: `final_goal_ok`, `autonomous_ok`, **`gemma_invocation_rate`**,
`wrong_writes` (muss 0 bleiben), `missed_actions`, p50/p95, RAM.

## 4. Setup auf dem Pi

```bash
cd needle-only
uv sync                     # Projekt-venv (py>=3.12), Pin cactus-needle==2.0.13 (Needle 2!)
# Für Needle-3-Modelle (.cact aus 3.0.4) ein zweites venv:
python -m venv ../.venv-ft3 && ../.venv-ft3/bin/pip install "cactus-needle[train,gpu]==3.0.4"
# cactus serve (Gemma) bereitstellen, dann:  CACTUS_BASE_URL=http://127.0.0.1:8080/v1
cactus serve ~/.cache/cactus/weights/gemma-4-e2b-it-cq4 --host 127.0.0.1 --port 8080
```
Hinweis: `needle-only/pyproject.toml` pinnt Needle **2** (App/hybrid). N3-Evals
brauchen **3.0.4** (eigenes venv, wie oben) — `.cact` ist engine-versionsgebunden.

## 5. Artefakte, die der Pi braucht (gitignored → manuell/HF)

| Datei | Größe | Beschaffung |
|---|---|---|
| `experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact` (n2-FT) | 13,7 MB | HF `autmoate/cactus-needle2-calendar` → `calendar-needle2-seed44.cact`, oder kopieren |
| **n3-preserve e5** (`models/modal/n3-v4-r32-e5-s42.cact`) | 63,4 MB | **HF `autmoate/cactus-needle3-calendar`** → `calendar-needle3-v4-e5.cact` (oder scp) |
| Needle-3-Base | 8–29 MB | auto-download via `needle download Cactus-Compute/needle3` (oder erster N3-Aufruf) |
| Datasets | — | `data/test.jsonl` ist committet; v4 JSONLs sind gitignored → `build_v4.py` regeneriert |

Eval-Aufrufe (CPU, auf dem Pi):
```bash
# A1 atomic + Challenge (n2-FT):
PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/base_eval.py \
  --weights experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact --tag n2ft-pi
# N3:
NEEDLE_WEIGHTS=experiments/ft/models/modal/n3-v4-r32-e5-s42.cact PYTHONPATH=src \
  ../.venv-ft3/bin/python experiments/ft/arch_bench.py --pipeline fallback --tag n3-v4-e5-pi
```

## 6. Regeln (aus AGENTS.md — gelten auch auf dem Pi)

- **`.env` nie lesen**; Secrets nur über Skripte (`python-dotenv`), nie ausgeben/committen.
- **Alte Reports nie überschreiben** — neue Tags/Dateien verwenden.
- **A1/A2 bleiben eingefroren**; `run()` gehört nur in Track B.
- **`reset()` genau 1× pro logischem User-Ziel**; kein Needle-Kontext zwischen Chats.
- **Gemma darf Features verbessern, aber nicht Pflicht sein** (Core/Lite-Profil ohne
  Gemma muss funktionieren; Zielarchitektur-Diagramm in `READ_WRITE_SPLIT.md`).
- Kein Gramm-Training ohne ausdrückliche Freigabe.

## 7. Danach ableitbare Entscheidungen

- **P-b/P-c** zeigen, ob P2/P3 die Zielerreichung auf ~80 % hebt und wie hoch
  `gemma_invocation_rate` real ist (Ziel: deutlich < 50 %, ideal ~5–14 %).
- **P-d** zeigt, ob Gemma als **Read-Handler** Mehrwert liefert (statt Overhead).
- Nächste (RTX-)Schritte, freigabepflichtig: **Write-only-N2-FT mit Read-Negatives**,
  Read-only-FT, **v5-Decomposer-Dataset/-FT**.
