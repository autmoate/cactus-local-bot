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
- **Metriken:** final_goal_ok · autonomous_ok · escalation_rate ·
  wrong_mutations (DB-Diff) · successful_mutations · missed_actions ·
  write_attempts/failed_write_attempts · Read-Korrektheit · Latenz p50/p95.

## Ergebnis (133 Fälle, lokal, kein Gemma verfügbar)

| Pipeline | Modell | goal_ok | autonomous | escalation_rate | **wrong_mutations** | missed | p50 | p95 |
|---|---|---|---|---|---|---|---|---|
| P1 direct | **N3-preserve e5** | **0.662** | 0.662 | 0 | 11* | 44 | 1086 ms | 1334 ms |
| P1 direct | N2-FT seed44 | 0.496 | 0.496 | 0 | 13* | 87 | 1314 ms | 1496 ms |
| **P2 fallback** | **N3-preserve e5** | **0.662** | **0.662** | **0.143** | **0*** | 44 | 1102 ms | 1369 ms |
| P2 fallback | N2-FT seed44 | 0.496 | 0.496 | 0.113 | **0*** | 87 | 1351 ms | 1525 ms |
| P0 hybrid | (Gemma→N2) | n/a — **kein lokales Gemma auf dieser Box** | | | | | | |

\* Call-basiert (Pre-Hardening). `arch_bench.py` wurde auf die Business-Metrik
umgestellt: `wrong_mutations` kommt jetzt aus einem DB-Diff + Read-Korrektheit
(kein „kein Write = Erfolg" mehr); `write_attempts`/`failed_write_attempts`
werden separat ausgewiesen. Neue Läufe mit eigenen Tags, alte Reports bleiben.

### Obergrenze der P2-Architektur (wichtig für die Pi-Entscheidung)

Auf den 133 Fällen (N3-e5, P2): **88 autonom korrekt · 19 eskalieren · 26 Silent
Omission**. Selbst wenn Gemma **alle** 19 Eskalationen perfekt löst:

```
Obergrenze = (88 + 19) / 133 ≈ 80.5 %
```

Damit ist klar: P2 kann nicht über ~80 % kommen, solange der Completeness-
Mechanismus Silent Omission nicht erkennt. Der Pi-Lauf für P0 muss zeigen, wie
viel davon Gemma-first (das die Vollständigkeit kennt) tatsächlich rettet.

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
2. **P2 erzeugt 0 Falsch-Mutationen bei nur 14.3 % Eskalationsrate** — die
   deterministische Eskalation fängt genau die gefährlichen Klassen:
   Ambiguität (10/10), nicht auflösbare Entities, Dependent-Chains (5/8).
   P1 ohne Eskalation schrieb 11-mal falsch (call-basiert; alles Ambiguität).
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
  gemessenes P0 zeigt, wie viel davon Gemma-first tatsächlich rettet. Selbst mit
  perfektem Gemma auf den 19 Eskalationen liegt die P2-Obergrenze bei ~80.5 %.
- Optionales N2-Second-Pass (N3 → N2 → Gemma) ist bewusst noch nicht gebaut.
- `wrong_mutations`/Read-Korrektheit sind seit dem Hardening **DB-Diff-basiert**;
  der Pre-Hardening-Lauf ist als `*`-Wert markiert. Der Pi-Lauf erzeugt die erste
  saubere Messung (neue Tags).

## Pi-Lauf (Reihenfolge, nach dem Eval-Hardening)

1. Infrastruktur: `cactus serve` + Gemma, N2-FT, N3-E5, SQLite verifizieren;
   Gemma warm halten.
2. **P0 erstmals wirklich messen** (Gemma→N2→Python) auf denselben 133 Fällen.
3. P1 (N3 direct) und P2 (N3-first + Eskalation) auf derselben Hardware.
4. `gemma_read_probe.py` (Gemma als Read-Handler) fahren.
5. `cross_routing.py` (write-toolset ← Read-Cases; read-toolset ← Write-Cases).
6. Ressourcen festhalten: p50/p95, Peak-RAM (N3-only / Gemma resident /
   Gemma warm / Gemma cold-start), N2/N3-Latenz.

**P2 braucht dafür keinen N2/N3-Sidecar:** N3-autonome Ergebnisse per Case-ID mit
den P0-Ergebnissen desselben Cases komponieren (N3 autonom → N3, N3 eskaliert →
P0) simuliert die logische Qualität von „N3 → bei Eskalation Gemma→N2".

## Reproduktion

```bash
cd needle-only
PYTHONPATH=src uv run python experiments/ft/subset_baseline.py \
    --rows sa-r16-lr1e-4-e8-seed44_test_rows.jsonl --tag n2ft-all5
PYTHONPATH=src uv run python experiments/ft/rescore_native.py
PYTHONPATH=src uv run python experiments/ft/availability_kernel.py --selftest

PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/arch_bench.py \
    --pipeline direct   --tag n3-v4-e5-h1     # NEEDLE_WEIGHTS=<n3.cact>
PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/arch_bench.py \
    --pipeline fallback --tag n3-v4-e5-h1
PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/cross_routing.py \
    --toolset write --cases read --tag n2ft-write-on-read
```

Reports: `reports/arch_<tag>_<pipeline>.json`, `reports/cross_<tag>.json`,
`reports/subset_baseline_<tag>.json` (Rohdaten inkl. pro-Fall-Calls).
