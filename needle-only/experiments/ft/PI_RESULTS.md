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
| P0 Gemma→N2 (one-shot canonicalize) | 0.654 | 15 | 100 % | 13 492 ms | 23 566 ms | 46 MB¹ |
| **P0-controller (echter `Agent` hybrid)** | **0.286** | **19** | 100 % | **120 031 ms** | 120 104 ms | 45 MB |
| **P1 N3-only** | 0.662 | **6** | 0 % | **2 122 ms** | 3 638 ms | 47 MB |
| P2 N3 + Eskalation (N3-venv, no-gemma) | 0.662 | 6 | 14.3 % | 2 183 ms | 4 076 ms | 46 MB |
| P2 N3→P0 one-shot Fallback (komponiert) | 0.699 | 6 | 14.3 % | ~4 000 ms | ~9 000 ms | 46 MB |
| **P2 N3→P0-controller Fallback (komponiert)** | **0.737** | **6** | 14.3 % | — ² | — ² | 45 MB |

¹ N2/N3-Prozess. **Gemma-`serve` resident = 2576 MB** (separater Prozess, dauerhaft).
² Fallback-Latenz wäre ~120 s/Turn beim Controller → auf dem Pi nicht deploybar.

**P2-Obergrenze:** (autonom korrekt 88 + eskaliert 19)/133 = **0.805**.
Der one-shot-Fallback rettet 5/19, der **Controller-Fallback rettet 10/19** —
exakt die Ambiguitätsklasse (er fragt nach).

### P0-controller (echter Agent, iterativ) — warum so schlecht

Der echte `Agent(mode="hybrid")` (`_controller_loop`: Gemma decide → N2 →
execute → Observation, wiederholt) erreicht auf dem Pi nur **0.286** bei
**p50/p95 = 120 s/Case** und **19 wrong_mutations**. `gemma_turns_mean = 1.35`
(98× 1 Turn, 27× 2) — die Latenz kommt **nicht** von vielen Turns, sondern von
**~120 s pro Turn**: der Controller-Prompt (System + Goal + Observations +
Kalender-Kontext) ist auf CPU sehr teuer (Prefill + 240 Tokens). Als Default-Pfad
ist die bestehende Hybrid-Architektur auf dem Pi damit **unbrauchbar** —
schlechter und ~60× langsamer als N3-only. Einziger klarer Mehrwert: die
**Rückfrage-Klasse** (ambiguous 10/10), die als Fallback wirkt.

## Familien (one-shot P0 / Controller-P0 / komponiertes P2-controller)

| Familie | n | P0 one-shot | P0-controller | P2ctrl ok | P2ctrl esc |
|---|---|---|---|---|---|
| atomic_read | 20 | 17 | 5 | **20** | 0 |
| atomic_write | 30 | 27 | 11 | 25 | 4 |
| indep_multi | 25 | 14 | 2 | 13 | 0 |
| bullet_list | 15 | 7 | 0 | 11 | 0 |
| mixed | 15 | 11 | 0 | 9 | 0 |
| ambiguous | 10 | 0 | **10** | 10 | 10 |
| dependent | 8 | 3 | 0 | 0 | 5 |
| offtopic | 10 | 8 | 10 | 10 | 0 |

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

1. **Gemma-first ist auf dem Pi endgültig widerlegt** — in beiden Varianten:
   one-shot-canonicalize (0.654) **und** der echte iterative Controller (0.286)
   sind schlechter als N3-only (0.662). Der Controller kostet zusätzlich **120 s
   pro Turn** und 2,6 GB resident RAM. Gemma E2B auf CPU ist weder schnell noch
   als Planner zuverlässig genug.
2. **N3-only (P1) ist der starke Kompromiss:** 0.662 goal bei 2.1 s, 6
   wrong_mutations, 46 MB, kein LLM → **Core/Lite-Profil**. Reads (atomic_read
   20/20) und Off-Topic (10/10) solide; Schwächen bei Multi/Bullet/Mixed/Dependent.
3. **Der Controller hat genau einen Mehrwert: die Rückfrage-Klasse.** Als
   Eskalations-Fallback (P2-controller = 0.737) rettet er **10/19** — alle 10
   ambiguen Fälle (er fragt nach, statt zu mutieren). Als Default-Pfad ist er
   wegen 120 s/Turn aber **nicht deploybar**. Der Wert ist architektonisch
   übersetzbar: **Ambiguität → Rückfrage** (statt Gemma-Controller).
4. **Silent Omission bleibt der Deckel** (44 missed in allen N3-Pipelines;
   157 beim Controller). Python erkennt fehlende Aktionen nicht — der Hebel ist
   **explizite Zerlegung (v5-Decomposer)**, nicht weitere Modelle.
5. **Capability-Split per Toolset ist unsicher** (write-on-read 97 %). Routing
   muss vor der Modellwahl passieren — oder N3↔N2 bestätigen sich gegenseitig.
6. **Gemma resident = 2576 MB**, auch bei nur 14 % Nutzung → ein Full-Profil
   kostet dauerhaft RAM; Core/Lite (N3-only) braucht ~50 MB.

**Fazit:** Kein Gemma-first, kein Write-FT. Atomic sind die Modelle gut genug
(n2-FT 0.978); das Problem ist **Intent-Erkennung, Zerlegung und
Referenzbindung**. Nächster Schritt: **v5-Decomposer** — N3 bekommt keine
mutierenden Tools mehr, liefert nur `read_step`/`write_step`/`dependent_write_step`
(verbatim), N2 macht die atomaren Calls, Python bindet Resultate und erzwingt den
Capability-Konsens (N3 sagt WRITE, N2 sieht READ → keine Mutation).

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
