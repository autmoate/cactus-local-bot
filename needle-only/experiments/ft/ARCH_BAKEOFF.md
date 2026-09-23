# Architektur-Bakeoff — wie viel Gemma-Compute sparen wir?

Frage (Business, nicht Benchmark): **Wie viele produktionsnahe Kalenderziele
schließt Needle 3 allein korrekt und sicher ab, bevor Gemma überhaupt gebraucht
wird?** Die bestehenden A1/A2/Benchmarks bleiben unverändert; hier kommen nur
neue, danebenliegende Pipelines dazu.

## Aufbau

- **133 produktionsnahe Fälle** (`arch_cases.py`, deterministisch, seed 42):
  atomic_read 20 · atomic_write 30 · indep_multi 25 · bullet_list 15 · mixed 15 ·
  ambiguous 10 · dependent 8 · offtopic 10. Feste Fixtures, prüfbarer Endzustand.
- **Pipelines** (`arch_bench.py`, nichts Bestehendes umgebaut):
  - **P0 hybrid** Gemma → N2-FT → Python (Referenz; braucht laufendes Gemma)
  - **P1 direct** N3 → Python (maximale Rationalisierung)
  - **P2 fallback** N3 → Python → nur bei Bedarf Gemma → N2-FT → Python
- **P2-Eskalation ist deterministisch** (kein semantischer Router): leerer Call =
  Refusal (safe, **kein** Gemma), unbekanntes Tool, leerer Titel bei Write, nicht
  eindeutig auflösbare Entity, Create ohne Datum/Zeit, Ausführungsfehler. Bei
  Eskalation wird **nichts geschrieben**.
- **Metriken:** final_goal_ok · autonomous_ok · gemma_invocation_rate ·
  wrong_writes · missed_actions · Latenz p50/p95.

## Ergebnis (133 Fälle, lokal, kein Gemma verfügbar)

| Pipeline | Modell | goal_ok | autonomous | Gemma-Rate | **wrong_writes** | missed | p50 | p95 |
|---|---|---|---|---|---|---|---|---|
| P1 direct | **N3-preserve e5** | **0.662** | 0.662 | 0 | 11 | 44 | 1086 ms | 1334 ms |
| P1 direct | N2-FT seed44 | 0.496 | 0.496 | 0 | 13 | 87 | 1314 ms | 1496 ms |
| **P2 fallback** | **N3-preserve e5** | **0.662** | **0.662** | **0.143** | **0** | 44 | 1102 ms | 1369 ms |
| P2 fallback | N2-FT seed44 | 0.496 | 0.496 | 0.113 | 0 | 87 | 1351 ms | 1525 ms |
| P0 hybrid | (Gemma→N2) | n/a — **kein lokales Gemma auf dieser Box** | | | | | | |

### Familien-Breakdown (P2, N3-E5)

| Familie | n | goal_ok | eskaliert | wrong | missed |
|---|---|---|---|---|---|
| atomic_read | 20 | 20/20 | 0 | 0 | 0 |
| atomic_write | 30 | 25/30 | 4 | 0 | 5 |
| indep_multi | 25 | 14/25 | 0 | 0 | 16 |
| bullet_list | 15 | 10/15 | 0 | 0 | 9 |
| mixed | 15 | 9/15 | 0 | 0 | 6 |
| ambiguous | 10 | 0/10 | **10** | **0** | 0 |
| dependent | 8 | 0/8 | 5 | 0 | 8 |
| offtopic | 10 | 10/10 | 0 | 0 | 0 |

## Was das heißt

1. **N3-first ist als Einzelmodell klar besser als N2-first** (0.662 vs 0.496):
   N2-FT kollabiert Multi-Call/Bullet-Listen (0/15 bzw. 3/25), N3 nicht.
2. **P2 erzeugt 0 Falsch-Writes bei nur 14.3 % Gemma-Bedarf** — die
   deterministische Eskalation fängt genau die gefährlichen Klassen:
   Ambiguität (10/10), nicht auflösbare Entities, Dependent-Chains (5/8).
   P1 ohne Eskalation schrieb 11-mal falsch (alles Ambiguität).
3. **Residualrisiko „Silent Omission": 26/133 (20 %)** — Fälle, die NICHT eskaliert
   wurden und das Ziel verfehlen (indep_multi 11, mixed 6, bullet_list 5,
   dependent 3, atomic_write 1). Python kann aus validen Calls nicht erkennen,
   dass Aktionen fehlen. Genau das fängt P0 (Gemma zerlegt zuerst) — und genau
   das kann N3-first prinzipbedingt nicht.
   (N2-first hat dieselbe Klasse bei 39 %.)
4. **Refusals kosten kein Gemma:** Off-Topic liefert leere Calls → safe, kein Write,
   keine Eskalation (10/10 korrekt).

## Business-Interpretation (Schätzung, klar als solche markiert)

- **Lite-Profil (N3-only, kein LLM):** ~66 % der Ziele autonom, schnell (p50 ~1.1 s),
  CPU-only, 0 Gemma-Compute. Falsch-Writes nur bei Ambiguität → mit der
  P2-Eskalation (statt Ausführung) auch dort safe.
- **Full-Profil (P2, N3-first + Gemma-Fallback):** 66 % autonom, **14 %** eskalieren.
  Wird die Eskalation von Gemma→N2 gelöst (Erwartung ≥ 90 % Erfolg), landet man
  grob bei **~78–80 % Zielerreichung mit ~14 % Gemma-Compute** statt 100 %.
- **N3-first + Gemma→N2 ist damit der interessante Rationalisierungspfad** —
  nicht Gemma→N3 (dort wäre N3s Multi-Call-Vorteil verspielt).

## Grenzen / nicht messbar hier

- **P0 (Gemma→N2) konnte nicht gemessen werden**: kein lokales Gemma/`cactus serve`
  auf dieser Box. Die Referenzzeile fehlt daher; die Aussage „P2 spart X % Gemma"
  beruht auf der Eskalationsrate, nicht auf einem gemessenen P0-Vergleich.
- Der 20-%-Silent-Omission-Anteil ist die zentrale offene Größe: nur ein
  gemessenes P0 zeigt, wie viel davon Gemma-first tatsächlich rettet.
- Optionales N2-Second-Pass (N3 → N2 → Gemma) ist bewusst noch nicht gebaut.

## Reproduktion

```bash
cd needle-only
PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/arch_bench.py \
    --pipeline direct   --tag n3-v4-e5     # NEEDLE_WEIGHTS=<n3.cact>
PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/arch_bench.py \
    --pipeline fallback --tag n3-v4-e5
```

Reports: `reports/arch_<tag>_<pipeline>.json` (Rohdaten inkl. pro-Fall-Calls).
