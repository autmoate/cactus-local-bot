# PI_RESULTS — echte ARM/CPU-Messungen (Raspberry Pi 5, 8 GB)

Erster echter Pi-Lauf nach dem Eval-Hardening (`ea868c1`). Alle Zahlen auf dieser
Hardware gemessen: aarch64, Gemma 4 E2B über `cactus serve` (CPU-Backend),
N2-FT in `.venv` (needle 2.0.13), N3-E5 in `.venv-ft3` (needle 3.0.4).

Modelle (ethereum-verifiziert via HF etag): n2-FT `ba3212ab…` (HF
`autmoate/cactus-needle2-calendar`), N3-E5 `0547c56d…` = HF-manifest
`artifact.sha256` (`autmoate/cactus-needle3-calendar`).

## Entscheidungstabelle (133 Fälle, gehärtete Metrik)

| Pipeline | Full goal | Wrong mut | Escalation/Gemma | p50 | p95 | Peak RAM |
|---|---|---|---|---|---|---|
| **P0 Gemma→N2** | 0.654 | **15** | 100 % | **13 492 ms** | 23 566 ms | 46 MB¹ |
| **P1 N3-only** | 0.662 | **6** | 0 % | **2 122 ms** | 3 638 ms | 47 MB |
| P2 N3 + Eskalation (N3-venv, no-gemma) | 0.662 | 6 | 14.3 % | 2 183 ms | 4 076 ms | 46 MB |
| **P2 N3→P0 Fallback (komponiert)** | **0.699** | **6** | 14.3 % | ~4 000 ms² | ~9 000 ms² | 46 MB |

¹ N2/N3-Prozess (Gemma läuft separat im `serve`: resident ≈ Bundle-Größe, s. u.).
² komponiert (N3-Zeit bzw. N3 + P0-Zeit bei Eskalation); P0 allein s. o.

**P2-Obergrenze neu:** (autonom korrekt 88 + eskaliert 19)/133 = **0.805** — die
alte 80.5 % bleibt zufällig gleich, aber jetzt auf gehärteter Read-/Mutations-
Metrik. Real löst P0 nur **5 von 19** eskalierten Fällen → P2 = 0.699.

## Familien (P0 / komponiertes P2)

| Familie | n | P0 ok | P2 ok | P2 esc | P2 wrong_mut |
|---|---|---|---|---|---|
| atomic_read | 20 | 17 | **20** | 0 | 0 |
| atomic_write | 30 | 27 | 29 | 4 | 1 |
| indep_multi | 25 | 14 | 13 | 0 | 5 |
| bullet_list | 15 | 7 | 11 | 0 | 0 |
| mixed | 15 | 11 | 9 | 0 | 0 |
| ambiguous | 10 | 0 | 0 | **10** | 0 |
| dependent | 8 | 3 | 1 | 5 | 0 |
| offtopic | 10 | 8 | 10 | 0 | 0 |

## A1 atomic — n2-FT auf dem Pi (`base_eval.py --tag n2ft-pi`)

| Set | tool_ok | args_ok | exact | refusals | median |
|---|---|---|---|---|---|
| Test n=1800 | 0.992 | **0.978** | **0.978** | 0.091 | 1031 ms |
| Challenge n=25 | 1.000 | 0.880 | 0.720 | 0.000 | 831 ms |

**Praktisch identisch zur RTX** (exact 0.977 / challenge 0.68). §22 „refusals ≤
base" **FAIL** (0.091 vs 0.081), aber zerlegt: **falsche Refusals auf Positiven
nur 5** (Base 53), korrekte Negativ-Refusals 159 (Base 93) — das Modell verweigert
die richtigen Dinge und wenigen richtigen Inputs.

## Cross-Routing (N2-FT, Sicherheitsfrage)

| Richtung | n | Refusal | Misroute | mutating_call |
|---|---|---|---|---|
| **write-Toolset ← echte Read-Cases** | 558 | 0.027 | **0.973** | **0.973** |
| read-Toolset ← echte Write-Cases | 1080 | 0.266 | 0.734 | 0.000 |

**Kritisch:** Landet ein Read-Request beim Write-Modell, entsteht in **97.3 %**
ein Write-Call — die frühere „50.6 % verweigert Reads"-Lesart war grundfalsch
(das waren none-Negative). Eine Capability-Trennung nur über Toolsets ist **nicht
sicher**; der Dispatcher muss die Klasse vorher bestimmen. Read-Richtung ist
harmlos (mutating 0), verliert aber Writes.

## Gemma als Read-Handler (`gemma_read_probe.py`)

- p50 **15 348 ms**, p95 **42 800 ms** (CPU, lange Prompts). Kontrollfragen korrekt
  abgewiesen (Wetter/Witz/e-Bike → „nur für den Kalender"); Owner-aware Gruppen
  („Lisa und ich") funktionieren ✓. Aber **semantische Reads schwach**: viele
  eigentlich-kalenderbezogene Fragen („Wie voll wird meine Woche?", „Welcher Tag
  am vollsten?") werden fälschlich als Off-Topic abgewiesen.
- **Cold-Start:** `cactus serve` + Modell-Load **~40–46 s**; erster Token 3–5 s
  warm, reale Prompts p50 ~13–15 s.

## Interpretation (Business)

1. **Gemma-first lohnt auf dem Pi nicht.** P0 (Gemma→N2) ist bei 0.654 **schlechter**
   als N3-only (0.662), erzeugt **mehr** Falsch-Mutationen (15 vs 6) und kostet
   **6×** Latenz (13.5 s vs 2.1 s). Gemma E2B auf CPU ist als vorgeschalteter
   Canonicalizer/Planner zu schwach und zu teuer.
2. **N3-only (P1) ist der starke Kompromiss:** 0.662 goal bei 2.1 s, 6 wrong_mutations,
   46 MB, kein LLM. Kandidat für ein **Core/Lite-Profil**. Reads (atomic_read 20/20)
   und Off-Topic (10/10) sind solide; Schwächen bei Multi/Bullet/Mixed/Dependent.
3. **P2 (N3→Gemma→N2) bringt nur +0.037** (0.699) bei 14.3 % Eskalation und rettet
   5/19. Der zusätzliche Gemma-Pfad ist teuer (Latenz) für wenig Gewinn.
4. **Silent Omission bleibt der Deckel** (44 missed in allen Pipelines): Python
   erkennt fehlende Aktionen nicht. Das ist der Hebel — nicht weitere Modelle.
5. **Capability-Split per Toolset ist unsicher** (write-on-read 97 %). Routing
   muss vor der Modellwahl passieren.

**Fazit:** Kein Gemma-first, kein Write-FT. Der nächste Forschungshebel ist
**Completeness/Zerlegung** (v5-Decomposer: Intent-Recall statt exact-output),
mit N3-only als Core/Lite-Fallback. Der Pi zeigt klar: Modelle sind gut genug im
Atomaren (n2-FT 0.978), die Orchestrierung ist das Problem.

## Reproduktion (Pi)

```bash
# Gemma
cactus serve ~/.cache/cactus/weights/gemma-4-e2b-it-cq4 --host 127.0.0.1 \
    --port 8080 --no-cloud-handoff --backend cpu
# P0 (N2-venv)
PYTHONPATH=src NEEDLE_WEIGHTS=experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact \
  .venv/bin/python experiments/ft/arch_bench.py --pipeline hybrid --tag n2ft-p0-pi
# P1/P2 (N3-venv)
PYTHONPATH=src NEEDLE_WEIGHTS=experiments/ft/models/modal/n3-v4-r32-e5-s42.cact \
  ../.venv-ft3/bin/python experiments/ft/arch_bench.py --pipeline direct --tag n3e5-p1-pi
PYTHONPATH=src NEEDLE_WEIGHTS=experiments/ft/models/modal/n3-v4-r32-e5-s42.cact \
  ../.venv-ft3/bin/python experiments/ft/arch_bench.py --pipeline fallback --no-gemma --tag n3e5-p2-pi
# Komposition + Cross-Routing + Read-Probe
PYTHONPATH=src .venv/bin/python experiments/ft/compose_p2.py \
  --p0 arch_n2ft-p0-pi_hybrid.json --n3 arch_n3e5-p2-pi_fallback.json --tag n3e5-p2-pi
PYTHONPATH=src NEEDLE_WEIGHTS=<n2.cact> .venv/bin/python experiments/ft/cross_routing.py \
  --toolset write --cases read --tag n2ft-write-on-read
PYTHONPATH=src .venv/bin/python experiments/ft/gemma_read_probe.py
```
