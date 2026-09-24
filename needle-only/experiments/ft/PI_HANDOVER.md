# PI-HANDOVER — Stand & Aufgaben für den Raspberry Pi

Kurz-Übergabe für den Agenten auf dem Pi (Repo + Git-History sind die Quelle).
Ziel: die **Gemma/`cactus serve`-Evaluationen** fahren, die auf der RTX-Box nicht
möglich waren. Nichts Bestehendes umdefinieren. **Aktualisiert nach `ea868c1`**
(Eval-Hardening) — frühere Fassungen waren an mehreren Stellen stale.

## 1. Lese-Reihenfolge (alles in `needle-only/experiments/ft/`)

1. `BASELINES.md` — eingefrorene Referenzen, Promotion-Gate, Namensregeln
2. `ARCH_BAKEOFF.md` — P0/P1/P2-Business-Bakeoff (133 Fälle), Pi-Plan
3. `READ_WRITE_SPLIT.md` — Toolset-Ablation (Same-Subset korrigiert),
   Availability-Kernel, Zielarchitektur
4. `DECOMPOSER_CONTRACT.md`, `RESULTS.md`, `NEEDLE3_FT_RESULTS.md`, `TRAINING_ENV.md`
5. Root: `AGENTS.md` (Regeln) — **`.env` niemals lesen** (Secrets)

## 2. Gesicherter Stand (freeze — nicht umdefinieren)

Pyramide: **A1 atomic** (`base_eval.py`) · **A2 one-shot multi** (`multi_call_bench.py`) ·
**B agentic** (`native_agent_bench.py`) · **C production E2E** (`tests/test_e2e.py` → `final_db_ok`).

| Modell | A1 atomic tool/args/exact | A2 legacy/indep | C final_db | Track B (dep. chains) |
|---|---|---|---|---|
| **n2-FT seed44** (Referenz) | 0.991/0.977/**0.977** | 0.30/0.30 | 72 % | 0.0 |
| **n3-preserve e5** (bester N3) | **1.000/0.889/0.886** | **0.90/0.90** | 80 % | 0.0 |
| n3-base | 0.746/0.230/0.063 | 0.80/0.80 | 52 % | 0.0 |

**Toolset-Ablation (korrigiert, Same-Subset):** Die frühere Aussage „write-only
0.984 = stärkste Write-Konfiguration" ist **widerlegt**. Apples-to-apples
(`subset_baseline.py`, all-5-Resultate auf dieselben Subsets zerlegt):

| Modell | Read exact | Write exact |
|---|---|---|
| N2-FT all-5 (gleicher Subset) | 0.948 | **0.993** |
| N2-FT eingeschränkt (read-/write-only) | 0.957 | 0.984 |
| N3-E5 all-5 (gleicher Subset) | 0.849 | 0.888 |
| N3-E5 eingeschränkt | 0.600 | 0.816 |

→ **N2 ist auf atomaren Writes bereits bei 0.993 mit allen fünf Tools.** Die
Verengung hilft nicht (Write minimal schlechter, Read +0.009). **Kein Write-FT.**
Der Bottleneck ist Completeness/Orchestrierung (Silent Omission), nicht die
Arg-Extraction. Die 0.506-Refusal im write-Toolset sind Refusals auf den
none-/Off-topic-Negativen — **nicht** „Reads werden zu 50 % verweigert"
(`cross_routing.py` misst das echte Cross-Routing).

**Track B (Metrik korrigiert):** `wrong_mutations` (DB-Snapshot-Diff) statt
`wrong_writes` (Call-Anzahl). `rescore_native.py`: N3-E5 run „11 wrong_writes"
→ **0 echte wrong_mutations** (14 Versuche, alle gescheitert, DB unverändert).
Echt abhängige Ketten löst kein Modell (0/10); `run()` == manual; `reset()` Pflicht.

**Kernel (`availability_kernel.py`):** on-grid **60/60 identisch** zu
`find_free_slots`; off-grid (05/10/17/23/41/50 min, Tagesgrenzen, all-day,
multi-day, Wochenende, Overlap) **0 unsichere Freiräume** (erfindet nie Freiheit),
aber konservativer (15-min 58/60, 5-min 49/60 — der Vergleich hängt an der
Slot-Repräsentation, nicht an „schlechter"). 2,8–4,0× schneller. **Prototyp —
keine Migration.**

**Entscheidung bisher:** Kein weiteres N3-FT, kein Write-FT, keine Migration.
n2-FT + deterministische Prüfung ist Produktionsreferenz; N3 ist Multi-Call-stark
(0.90) → Kandidat als Decomposer. **P2-Obergrenze ~80.5 %** stammte aus dem alten
(Read-/Mutations-)Metrik-Lauf → **auf dem Pi neu berechnen**, nicht als Deckel
behandeln.

## 3. Was der Pi liefern soll (Reihenfolge)

1. Repo aktualisieren + Tests/Smokes (`uv run pytest -q -m "not needle"`).
2. n2-FT + n3-E5 aus den HF-Releases laden, SHA/Smoke prüfen.
3. `cactus serve` mit Gemma **warm** verifizieren (ein Chat-Call).
4. **P0 `arch_bench.py --pipeline hybrid`** (Gemma→N2→Python) im **N2-venv**.
5. **P1 `--pipeline direct`** und **P2 `--pipeline fallback --no-gemma`** im
   **N3-venv** neu fahren (gehärtete Metrik — alte 0.662/0.143 nicht übernehmen).
6. **P2 offline komponieren** (`compose_p2.py`): N3-autonom → N3-Ergebnis;
   N3-eskaliert → P0-Ergebnis desselben Cases. So messen wir die logische
   P2-Qualität, **ohne zwei Engine-Versionen in einen Prozess zu zwingen**.
7. `cross_routing.py`: N2 write-view ← echte Read-Cases (`mutating_call_rate`),
   read-view ← echte Write-Cases.
8. `gemma_read_probe.py` (Python-Fakten → Gemma formuliert; Owner-aware Snapshot).
9. Ressourcen: Peak-RAM, warm p50/p95, Gemma-Cold-Start.

**Venv-Trennung (wichtig):** N2-FT hängt an Needle **2.0.13** (`.venv`),
N3-E5 an Needle **3.0.4** (`.venv-ft3`). `.cact` ist engine-versionsgebunden →
N2 und N3 **nicht** in einen Prozess. P0 im N2-venv, P1/P2 im N3-venv, P2 per
Case-ID komponiert.

Kernmetriken: `final_goal_ok`, `autonomous_ok`, **`escalation_rate`**,
**`wrong_mutations`** (muss 0 bleiben), `missed_actions`, p50/p95, Peak-RAM.
Ziel-Tabelle am Ende: P0 / P1 / P2 × Full goal · Wrong mutations · Escalation ·
p50 · p95 · Peak RAM (+ Familienwerte atomic/multi/bullet/mixed/ambiguous/dependent).

## 4. Setup auf dem Pi

```bash
cd needle-only
uv sync --extra cactus        # Projekt-venv (py>=3.12), Pin cactus-needle==2.0.13,
                              # cactus-compute==2.1.0 (Gemma serve / Engine)
# Für Needle-3-Modelle (.cact aus 3.0.4) ein zweites venv:
python3 -m venv ../.venv-ft3 && ../.venv-ft3/bin/pip install "cactus-needle==3.0.4"
cactus serve ~/.cache/cactus/weights/gemma-4-e2b-it-cq4 \
    --host 127.0.0.1 --port 8080 --no-cloud-handoff --backend cpu
# Agent nutzt CACTUS_BASE_URL (Default http://127.0.0.1:8080/v1) automatisch.
```

## 5. Artefakte (gitignored → HF)

| Datei | Größe | Quelle |
|---|---|---|
| `experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact` (n2-FT) | 13,4 MB | HF `autmoate/cactus-needle2-calendar` → `calendar-needle2-seed44.cact` |
| `experiments/ft/models/modal/n3-v4-r32-e5-s42.cact` (n3-preserve e5) | 61,9 MB | HF `autmoate/cactus-needle3-calendar` → `calendar-needle3-v4-e5.cact` |
| Needle-3-Base | — | auto-download via `needle download Cactus-Compute/needle3` |
| Datasets | — | `data/test.jsonl` committet; v4 via `build_v4.py` regenerierbar |

```bash
# P0 (N2-venv):
PYTHONPATH=src NEEDLE_WEIGHTS=experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact \
  uv run python experiments/ft/arch_bench.py --pipeline hybrid --tag n2ft-p0-pi
# P1/P2 (N3-venv):
PYTHONPATH=src NEEDLE_WEIGHTS=experiments/ft/models/modal/n3-v4-r32-e5-s42.cact \
  ../.venv-ft3/bin/python experiments/ft/arch_bench.py --pipeline direct  --tag n3e5-p1-pi
PYTHONPATH=src NEEDLE_WEIGHTS=experiments/ft/models/modal/n3-v4-r32-e5-s42.cact \
  ../.venv-ft3/bin/python experiments/ft/arch_bench.py --pipeline fallback --no-gemma --tag n3e5-p2-pi
# A1 atomic + Challenge (n2-FT):
PYTHONPATH=src uv run python experiments/ft/base_eval.py \
  --weights experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact --tag n2ft-pi
```

## 6. Regeln (aus AGENTS.md)

- **`.env` nie lesen**; Secrets nur über Skripte (`python-dotenv`), nie ausgeben.
- **Alte Reports nie überschreiben** — neue Tags/Dateien (`*-pi`).
- **A1/A2 bleiben eingefroren**; `run()` gehört nur in Track B.
- **`reset()` genau 1× pro logischem User-Ziel.**
- **Gemma darf nie Pflicht werden** (Core/Lite-Profil ohne Gemma muss laufen).
- Kein Training ohne ausdrückliche Freigabe.

## 7. Danach ableitbare Entscheidungen

- **P0 vs P2**: liegt P0 bei 95–98 % und P2 nur 78–82 %, ist Silent Omission zu
  teuer → v5-Decomposer erforschen. Zieht P2 mit 10–20 % Gemma praktisch gleich,
  ist die Cascade der Rationalisierungspfad.
- **P1 N3-only**: liegt sie überraschend hoch und safe, entsteht ein Core/Lite-
  Profil ohne Gemma.
- **cross_routing**: entscheidet, ob eine Capability-Trennung sicher genug ist.
- **P-d**: zeigt, ob Gemma als Read-Handler Mehrwert liefert.
- Nächste (freigabepflichtige) Schritte: v5-Decomposer-Dataset/-FT; ggf. dedizierte
  Read-/Write-FTs **nur wenn** Cross-Routing es rechtfertigt.
