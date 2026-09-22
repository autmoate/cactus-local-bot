# Calendar-FT Ergebnisse (Needle 2, RTX 3090) — Stage A

> **Kernaussage:** Das *Needle-Argumentproblem* (Spans statt gerechneter ISO-Werte)
> ist praktisch gelöst: L1 `exact_args` 0.113 → **0.974–0.981**, falsche Refusals
> 53 → 0–5. Der **End-to-End-Kalenderagent bleibt aber bei ~68–72 %** finalem
> DB-Zustand (Level 2) — limitiert durch Resolver/Verify/Identifikation, nicht
> durch Needle. Dieses Dokument behauptet **nicht**, der Agent sei insgesamt
> ~98 % korrekt.

## Setup

| | |
|---|---|
| Base | Needle 2 (`Cactus-Compute/needle2`, `checkpoints/needle2.pkl`), cactus-needle 2.0.13 |
| Dataset | FT-Dataset v2 (seed 42, sparse/evidenced-only Gold), 5 Produktionstools |
| Training | LoRA rank 16 / alpha 32 / lr 1e-4 / 8 Epochen / batch 8 / max-len 1024, 3 Seeds |
| Hardware | RTX 3090 (WSL2), ~2.9–3.0 h pro Seed, Adapter 7,96 MB, `.cact` 13,74 MB (W4A8) |
| Wrapper | `train_rtx.py` (injiziert `tools.json` + `SYSTEM_FACTS`; Console-Script `needle`) |

## Ergebnisübersicht

| Modell | L1 exact_args | L1 args_ok | L1 tool_ok | L1 falsche Refusals | L1 korr. Neg-Refusals | L3 exact | L3 args_ok | L2 final_db | ø Median-Latenz |
|---|---|---|---|---|---|---|---|---|---|
| **Base** | 0.113 | 0.449 | 0.863 | 53/1638 | 93/162 (57 %) | 0.120 | 0.520 | 68 % | 1206 ms |
| FT seed 42 | 0.974 | 0.975 | 0.996 | **0** | 156/162 (96 %) | 0.680 | 0.760 | 68 % | 260 ms |
| FT seed 43 | **0.981** | **0.981** | 0.992 | **0** | 149/162 (92 %) | 0.640 | 0.800 | 68 % | 237 ms |
| **FT seed 44 (RC)** | 0.977 | 0.977 | 0.991 | 5 | 158/162 (98 %) | 0.680 | **0.840** | **72 %** | 261 ms |

**3-Seed-Varianz L1 exact: 0.974–0.981** (mean 0.977, ±0.004) — sehr robust.
**Release-Kandidat: seed 44** (bestes L2 72 %, bestes L3 args 0.84, beste Negativ-Refusal-Rate 98 %);
seed 43 ist L1-Sieger mit 0 falschen Refusals.

### §22-Kriterien (Nutzer-Spec)
| Kriterium | Ziel | Ergebnis |
|---|---|---|
| L1 exact_args | ≥ base + 0.20 (≥ 0.313) | **PASS** 0.974–0.981 (≈3×) |
| L1 args_ok | ≥ base + 0.15 (≥ 0.599) | **PASS** 0.975–0.981 |
| Refusals ≤ base | ≤ 0.081 | formal FAIL (0.083–0.091) — **Metrik-Artefakt**, s. u. |
| falsche Refusals auf Positiven ≤ base | ≤ 53 | **PASS** 0–5 |
| keine Read/Delete-Regression | — | **PASS** (per-tool, s. u.) |

Die Gesamt-„Refusals" vermischen korrekte Negativ-Verweigerungen mit falschen auf
gültigen Anfragen. Die FT-Modelle verweigern **korrekt deutlich mehr** Off-Topic
(92–98 % vs. 57 %) und **falsch praktisch nie** (0–5 vs. 53) — die Kennzahl steigt
nur, weil Negativ-Refusals zählen. Separat ausgewiesen in `base_eval.py --weights`.

### Level 1 — per-Tool `tool_ok` (n=1800)
| Tool | Base | FT seed 42/43/44 |
|---|---|---|
| calendar_create | 0.996 | 0.998 / 1.000 / 1.000 |
| calendar_move | 0.878 | 1.000 / 1.000 / 1.000 |
| calendar_delete | 0.805 | 1.000 / 1.000 / 1.000 |
| calendar_list | 0.484 | 1.000 / 0.994 / 0.929 |
| calendar_find_slot | 0.945 | 1.000 / 1.000 / 0.995 |

### Level 1 — per-Field (Base → FT-Range)
| Feld | Base | FT |
|---|---|---|
| date | 0.551 | 0.995–0.997 |
| until | 0.306 | 0.987–0.990 |
| time | 0.935 | 0.997 |
| title | 0.999 | 0.994–0.998 |
| persons | 0.732 | 0.943–0.983 |
| person | 0.650 | 1.000 |
| participants | 0.887 | 0.991–1.000 |

### Level 3 — Challenge (25 reale Traces, bewusst unfair)
Base: tool 0.92 / args 0.52 / exact 0.12 · FT: tool 0.96–1.00 / args 0.76–0.84 / exact 0.64–0.68.
Verbleibende L3-Lücken: `until` 0.25–0.50, `end_time`/`time` 0.5–1.0, `title` 0.88
(reale, teils kolloquiale Fail-Fälle + Resolver-Grenzen).

### Level 2 — finaler DB-Zustand (`tests/test_e2e.py`, 25 Cases, needle-Mode)
Base 68 % (ø 348 ms) · FT 42/43/44: 68 / 68 / **72 %** (ø 216–242 ms).
→ Needle liefert korrekte Spans; der Endzustand hängt an Python-Resolvern,
Event-Identifikation und Verifikation. **Ehrlicher Gesamtstand des Agenten: ~70 %.**
Nicht in dieser Suite: hybrid_only-Fälle (brauchen `cactus serve`).

### Gold-Konvention A/B (seed 42)
`exact_sparse` 0.887 vs. `exact_full` 0.186 (`gold_ab.py --reconstruct-full`)
→ evidenced-only sparse Gold ist empirisch bestätigt.

## Bekannte verbleibende Fehler
- **Resolver-Gaps** (README „dokumentierte Gaps", bestimmen Level 2): „diesen Freitag",
  „am Wochenende", ausgeschriebene Offsets, „am Freitag" vs. „Freitag"; mehrteilige
  Absences; Absence ohne Uhrzeit.
- **L3-Challenge** hält bewusst reale Fail-/Kapazitätsgrenzen: `until`, `end_time`.
- **Nicht Teil des Trainings** (per Design): Kollisionen, Multi-Intent, En-Datumsangaben
  wie „August 3", Multi-Step-Planning — Python bzw. eine höhere Schicht bleibt zuständig.
- **Kein Confidence-Gating:** FT-Modelle melden `confidence: None` (Head wird beim
  Finetuning nicht mittrainiert) → deterministische Validierung nötig, kein Schwellenwert.

## Artefakte & Reproduktion
- Modelle: `models/sa-r16-lr1e-4-e8-seed{42,43,44}.cact` (+ `_lora.pkl`), Hashes/Configs in
  `models/manifest.json` (Binärdateien gitignored, Manifest committet).
- Reports: `reports/<run>_{test,challenge}_report.json` + `_rows.jsonl`, `reports/runs/<run>.{json,log}`.
- Lauf: `uv run python experiments/ft/train_rtx.py --run-name <n> --rank 16 --lr 1e-4 --epochs 8 --seed <s>`
  (Batchnote: WSL-VRAM-Cap ~12 GiB/GPU → batch 8; 16@1024 OOMt).
- Eval: `base_eval.py --weights models/<run>.cact --tag <run>` (Level 1+3, §22-Check) ·
  `NEEDLE_WEIGHTS=models/<run>.cact uv run python tests/test_e2e.py` (Level 2).
