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
