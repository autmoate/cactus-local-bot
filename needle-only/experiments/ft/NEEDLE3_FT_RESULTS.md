# Needle-3-FT Ergebnisse (Modal, A100-40GB)

Gleiche Schemas, System-Facts und split-identische Test-/Challenge-Sets wie in
`RESULTS.md`. Zwei Läufe: **v2** (atomar, 10 000) und **v3** (atomar + ~25 %
Multi-Call, 12 282). Details zur Umgebung: `TRAINING_ENV.md`.

## Setup

| | |
|---|---|
| Plattform | Modal, `A100-40GB`, 2 Container **parallel** |
| Paket | `cactus-needle[train,gpu]==3.0.4` |
| LoRA | rank 16 / alpha 32 / lr 1e-4 / **1 Epoche** / batch 16 / max-len 1024 / seed 42 / val-split 0 |
| Daten | `modal_train.py` lädt Rohdaten (11,5 MB), injiziert `tools`+`system` im Container |
| Artefakte | `.cact` 63,4 MB (20 Layer), Adapter 7,9 MB, im Volume `cactus-ft-artifacts` |

**Zeiten/Kosten (gemessen):** v2 train 603 s + build 21 s · v3 train 826 s + build 27 s.
Wanduhr inkl. Image-Build, Base-Download (242 MB) und Parallelität: **~27 min**.
Grob **< 1 $** (2 × ~0,25 GPU-h × 2,10 $/h), also weit innerhalb der $30 Gratis-Compute.
Vergleich lokal (3090): v2-Subset-Epoche 14 min und Abbruchrisiko — Modal ist ~10×
schneller bei dieser Modellgröße.

## Ergebnisse

| Modell | L1 exact | L1 args | L1 tool | L3 exact | L3 args | L2 final_db | Multi-Call | Median |
|---|---|---|---|---|---|---|---|---|
| Base Needle 2 | 0.113 | 0.449 | 0.863 | 0.120 | 0.520 | 68 % | 0.80 | 1206 ms |
| Needle 3 Base | 0.063 | 0.230 | 0.746 | 0.040 | 0.360 | 52 % | 0.80 | 184 ms |
| **n3-FT v2 (atomar)** | **0.739** | **0.769** | 0.839 | **0.520** | 0.760 | 68 % | 0.40 | 425 ms* |
| **n3-FT v3 (+Multi)** | **0.741** | 0.758 | 0.848 | 0.440 | 0.640 | 68 % | **0.70** | 417 ms* |
| **n2-FT seed44** | **0.977** | **0.977** | **0.991** | **0.680** | **0.840** | 68–72 % | 0.30 | 261 ms |

\* Während der Messung liefen zwei CPU-Evals parallel → Latenz überschätzt.
§22 (Level 1): **beide n3-FTs PASS** — exact_args ≥ base+0.20, args_ok ≥ base+0.15,
`refusals` 0.024/0.026 ≤ 0.081, falsche Refusals 1 bzw. 7 von 1638 (Base 53).

**Kernaussagen**
1. **Needle-2-FT bleibt der atomare Champion** (0.977 vs 0.739): das FT v2 auf
   Needle 2 schlägt Needle 3 FT bei Argumenten klar — trotz nur 1 Epoche auf N3 ist
   der Abstand groß; mehr Epochen sind der naheliegende Hebel (s. u.).
2. **Needle 3 FT ist der Multi-Call-Champion unter den FTs**: v3-FT 0.70 vs n2-FT 0.30
   (Trainingsanteil Multi-Call wirkt: v2-FT 0.40 → v3-FT 0.70). Die Bases liegen bei 0.80.
3. **Level 2 ist für alle FTs gleich (68 %)** → der Endzustand hängt am
   Python-Resolver/Verify, nicht am Modell. (N3-Base nur 52 %.)
4. **N3-FTs verweigern zu selten Off-Topic** (korrekte Negativ-Refusals ~25 % vs
   n2-FT ~92–98 %) — typisch für 1 Epoche + wenige Negatives; Sicherheitsnetz bleibt
   die deterministische Validierung.
5. **Confidence:** Fine-Tunes (N2/N3) tragen **keinen** trainierten Head →
   `confidence: None`. Ein Confidence-Gate (< 0.3 → Gemma) ist nur mit **Base** oder
   mit auf der Cactus Platform trainierten Modellen möglich.

## Nächste Hebel (billig auf Modal)
- **Epochen 2–3** (je ~10–14 min, < 1 $): dürfte exact_args deutlich heben.
- **v2+v3 gemischt** trainieren → atomare Stärke **und** Multi-Call in einem Modell.
- **Ladder-Export** (`needle build --layers 8|12|16`) für den Pi-Benchmark
  (Accuracy/Latenz/RAM), gleiches `.cact`-Format.

## Reproduktion

```bash
cd needle-only
uv sync --extra modal --no-dev && uv run --extra modal modal setup   # einmalig
uv run --extra modal modal run experiments/ft/modal_train.py \
    --runs v2,v3 --epochs 1 --batch-size 16 --gpu A100-40GB
uv run --extra modal modal volume get cactus-ft-artifacts ./experiments/ft/models/modal
```

Eval lokal (CPU, wie in `RESULTS.md`):
```bash
CUDA_VISIBLE_DEVICES="" .venv-ft3/bin/python experiments/ft/base_eval.py \
    --weights experiments/ft/models/modal/n3-v3-r16-e1-s42.cact --tag n3-v3-ft --no-auto-date
```

---

# Runde 2 — Epochen & Preservation-Mix (Plan-Phasen 1–9)

Nach Runde 1 (1 Epoche, 0.74 atomic) wurde der Evaluator-Gate gehärtet
(`promotion_gate.py`, alle Achsen getrennt — Details in `BASELINES.md`) und ein
**Preservation-Dataset v4** gebaut (`build_v4.py`: ~79 % atomic aus drei
Generator-Seeds, 20 % Multi-Call, 12 % Negatives + Near-Negatives).
Dependent chains bleiben bewusst eval-only.

## Drei begründete Modal-Runs (A100-40GB, Gesamt-Walltime ~3 h ≪ 8 h Budget)

| Run | Dataset | rank | Epochen | Beispiele | Training | Datei |
|---|---|---|---|---|---|---|
| A `n3-v2-r16-e3` | v2 (atomic) | 16 | 3 | 10 000 | 1 636 s | `.cact` 63,4 MB |
| B `n3-v4-r32-e3` | v4 (preserve) | 32 | 3 | 25 072 | 4 074 s | `.cact` 63,4 MB |
| C `n3-v4-r32-e5` | v4 (preserve) | 32 | 5 | 25 072 | 6 630 s | `.cact` 63,4 MB |

A+B liefen parallel (68,6 min Wanduhr), C danach (110,9 min).

## Ergebnisse (Vergleich zur Referenz n2-FT)

| Modell | atomic tool/args/exact | challenge args/exact | multi(all) | negRefusal | falseRef | final_db | median |
|---|---|---|---|---|---|---|---|
| **n2-FT seed44 (Referenz)** | 0.991/0.977/**0.977** | 0.840/**0.680** | 0.30 | 97.5 % | 0.31 % | 72 % | 261 ms |
| n3-atomic e1 | 0.839/0.769/0.739 | 0.760/0.520 | 0.40 | 26 % | 0.06 % | 68 % | 425 ms |
| n3-preserve e3 | 0.998/0.884/0.877 | 0.600/0.480 | 0.70 | 100 % | 0.18 % | 80 % | 438 ms |
| **n3-preserve e5 (bester N3)** | **1.000/0.889/0.886** | 0.720/0.600 | **0.90** | **100 %** | **0.00 %** | **80 %** | 188 ms |

## Was die Runde gezeigt hat

1. **Epochen sind der dominante Hebel (bis zum Plateau):** 1 → 3 Epochen hoben
   atomic von 0.739 auf ~0.877 (+14 pp) und die Negativ-Refusals von ~25 % auf
   100 %. 3 → 5 Epochen brachten nur noch +0.9 pp (0.886) → **Plateau ~0.89**.
2. **Das Preservation-Dataset wirkt:** Multi-Call steigt mit den Trainingsdaten
   (0.40 atomar → 0.70 bei v3/v4 mit 20–25 % Multi) und erreicht mit 5 Epochen
   **0.90 — über Base (0.80) und weit über n2-FT (0.30)**. Off-topic-Refusals
   sind mit v4 perfekt (162/162, 0 falsche) statt ~25 %.
3. **Der Multi-Call-„Verlust" ist steuerbar**, kein Strukturproblem: Daten + Epochen.
4. **Aber: die atomare Lücke schließt sich nicht.** Bestes N3 0.886/0.889 gegen
   n2-FT 0.977 — bei ~5 pp Abstand zur Gate-Schwelle 0.95 und 9 pp zur Referenz.
   Lokales N3-4-bit-LoRA (nur Attention, kein Head) erreicht das N2-FT atomar nicht.
5. **Level 2 (finaler DB-Zustand):** n3-preserve 80 % vs. n2-FT 72 % (Base 56–68 %).

## Turn-Semantik (Phasen 8/9) — bewiesen, keine Migration

`turn_semantics_probe.py` → `reports/turn_semantics_n2ft-seed44.txt`:

- **`reset()` ist Pflicht:** ohne Reset leakt der Titel aus Turn A in Turn C und
  löscht den falschen Eintrag (`foreign_leak = ["zahnarzt"]`, DB leer). Mit Reset
  vor jedem unabhängigen Turn: kein Fremd-Leak, korrektes Verhalten.
- **Dependent chains** lösen weder manueller `complete → execute → complete(result)`-
  Loop noch `run()` (es bleibt beim ersten Call) → weiterhin Aufgabe einer höheren
  Schicht (Gemma), nicht des FT.
- **Confidence ist kein FT-Signal** (`None`): Eskalation nur über deterministische
  Signale (Liste in `BASELINES.md`, Phase 7).

## Entscheidung (Plan-Entscheidungsbaum)

**N3 bleibt bei ~0.89 atomic → lokales N3-Tuning gestoppt.** Produktionsarchitektur
bleibt: **n2-FT → Python-Validierung → Gemma nur bei Ambiguität/Multi-Step/Repair.**
Needle 3 bleibt Forschungsstrang; sein klarer Gewinn (Multi-Call 0.90) ist notiert.
Keine Ladder-, Telegram- oder Produktionsmigration, solange das Gate nicht hält.
Optionaler nächster Challenger: **1 Platform-FT-Run** (kalibrierte Confidence +
Replay der Needle-Daten + 2-Bit) — nur mit Cactus-Plan/API-Key.


---

# Nachtrag — Track B (Agentic) + Korrekturen (eval/doc-hardening, kein Training)

## Korrekturen
- **v4-Mix** war im Manifest falsch ausgewiesen (Zähler vor, Nenner nach dem
  Val-Split → 111 %). `build_v4.py` zählt jetzt die tatsächlichen Tags nach dem
  Split: **71,5 % atomic / 17,8 % multi / 10,7 % negatives** (= geplanter Mix).
- **A2 zusätzlich differenziert:** der historische 10-Fälle-Score bleibt
  (`all_actions_correct`), neu daneben `multi_independent` (ohne den dependent
  Fall `find+create`). Alte Zahlen wurden nicht überschrieben.
- **Reset-Beweis präzisiert:** nicht mehr ein bestimmter Titel-Leak, sondern der
  robuste Divergenz-Test (frischer vs. verschmutzter Kontext, gleiche DB):
  **n2-FT 3/3 divergent, N3-e5 1/3** → `reset()` ist Pflicht.
- **`--mode run` im `multi_call_bench.py`** ist als **legacy/invalid** markiert
  (Schema-only-Tools, keine ausführbaren Callables) — native run()-Fähigkeit
  misst ausschließlich `native_agent_bench.py`.

## Track B — native Agent Capability (10 dependent-chain-Fälle, echte Tools)

| Modell | manual goal_ok | run() goal_ok | wrong_writes | median |
|---|---|---|---|---|
| n2-FT seed44 | 0.2 | 0.2 | 0 | ~0.4–0.6 s |
| N3 Base | 0.1 | 0.1 | **1** (manual) | 0.5–1.5 s |
| N3-preserve e5 | **0.4** | **0.4** | 0 | ~0.39 s |

**Erkenntnisse**
1. **`run()` = manual loop** — identische Ergebnisse bei allen drei Modellen.
   Gate 3: kein messbarer Zusatznutzen der native-Orchestrierung für unsere
   dependent chains; der Engpass ist das **Resultat-Grounding**, nicht der Loop.
2. Hauptfehler: richtige Sequenz (`find_slot → create`), aber die **Slot-Zeit aus
   dem Tool-Resultat wird nicht in den `create`-Call übernommen** → inkorrekt.
3. **Sicherheit:** N3-Base erzeugte im manuellen Loop **einen falschen Write**;
   n2-FT und N3-e5: 0. `run()` exponiert nur `results` (kein Argument-Transkript)
   → für Audits weniger geeignet als der eigene Loop.
4. dependent chains bleiben damit klar Aufgabe einer höheren Schicht
   (Gemma-Zerlegung/Planner) — nicht des FT-Modells.

## Entscheidungslage (unverändert)

Kein N3-Kandidat erreicht das Atomic-Gate (bestes 0.886/0.889 vs. 0.95/0.977);
Track B ergibt keinen run()-Vorteil. **Lokales N3-Tuning bleibt gestoppt**,
n2-FT + Gemma-Controller + Python bleibt Produktion. Kein Platform-FT,
keine Telegram-/Ladder-Migration (Budget-Entscheidung des Nutzers).
