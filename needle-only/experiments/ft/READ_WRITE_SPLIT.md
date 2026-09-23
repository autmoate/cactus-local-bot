# Read/Write-Split & Compute-Rationalisierung (Ablation, kein Training)

Ziel: **Gemma-Compute einsparen**, ohne Zuverlässigkeit zu verlieren. Dazu wurden
bestehende Modelle mit **eingeschränkten Toolsets** gemessen (frozen Subsets),
ein **Availability-Kernel** prototypisiert und ein **Decomposer-Contract** entworfen.
Kein Training, keine Migration, nichts Bestehendes umdefiniert.

## A — Toolset-Ablation (frozen: 558 read · 1080 write · 162 neg)

**Wichtig — Same-Subset-Vergleich (Review-Korrektur):** die frühere Aussage
„write-only 0.984 > all-5 0.977" verglich ein Subset mit dem *globalen* Score
über alle 1800 Fälle. `subset_baseline.py` zerlegt die frozen All-5-Resultate
nach denselben Read/Write-Subsets — erst das ist apples-to-apples:

| Modell / Toolset | Klasse | n | tool_ok | args_ok | exact | Refusals (Neg) |
|---|---|---|---|---|---|---|
| N2-FT **all-5** (gleicher Subset) | read | 558 | 0.977 | 0.948 | **0.948** | — |
| N2-FT **read-only** | read | 558 | 0.977 | 0.959 | **0.957** | 0.914 |
| N2-FT **all-5** (gleicher Subset) | write | 1080 | **1.000** | **0.993** | **0.993** | — |
| N2-FT **write-only** | write | 1080 | 1.000 | 0.987 | 0.984 | 0.506 |
| N3-E5 **all-5** (gleicher Subset) | read | 558 | 1.000 | 0.855 | **0.849** | — |
| N3-E5 **read-only** | read | 558 | 0.896 | 0.659 | 0.600 | 0.852 |
| N3-E5 **all-5** (gleicher Subset) | write | 1080 | 1.000 | 0.890 | **0.888** | — |
| N3-E5 **write-only** | write | 1080 | 0.959 | 0.818 | 0.816 | 0.944 |

**Befunde (korrigiert)**
1. **N2-FT ist auf atomaren Writes bereits bei 0.993 exact, wenn es seine
   bekannten fünf Tools sieht.** Die Verengung auf write-only wird minimal
   *schlechter* (0.993 → 0.984) — **kein Write-FT nötig.** Das eigentliche
   Problem liegt davor: Zerlegung, Vollständigkeit, Routing, dependent reasoning.
2. Read profitiert leicht von der Verengung (0.948 → 0.957).
3. **N3 degradiert bei beiden Verengungen** (Read 0.849 → 0.600, Write 0.888 →
   0.816): sein FT hängt am 5-Tool-Kontext; die Multi-Call-Stärke wird in diesen
   atomaren Subsets gar nicht gemessen.
4. **Die 0.506 sind NICHT „das Write-Modell verweigert echte Reads zu 50 %".**
   Im write-Modus laufen nur write-cases + none-case-negatives — 0.506 ist die
   Refusal-Rate auf den *bestehenden none-/Off-topic-Negativen*. Das echte
   Cross-Routing (write-toolset ← echte Read-Cases) ist damit **noch nicht
   gemessen**: `cross_routing.py --toolset write --cases read` (ready-to-run,
   braucht Modell). Sicherheitsmetrik dort: `mutating_call_rate`.

**Konsequenz:** ein einziges N2-FT mit zwei Tool-Views ist der interessante Weg
(Read-View: list/find_slot → 0.957; Write-View: alle 5, Python akzeptiert nur
create/move/delete → 0.993) — ein Modellfile, Python erzwingt die Capability.
Dedizierte Read/Write-FTs erst, wenn Cross-Routing das rechtfertigt.

## B — Availability-Kernel (Bitset/Slot-Matrix)

`availability_kernel.py`: projiziert den Event-Store auf Tage × 15-min-Slots ×
Personen (int-Bitmasken). Überlappungen werden `OR`, gemeinsame Freiheit `AND` —
Python bleibt Wahrheit (Events halboffen), die Matrix ist nur Projektion.

- **Identität auf dem Raster:** 60/60 randomisierte Fixtures mit 15-min-Zeiten
  liefern **exakt dieselben** freien Slots wie `find_free_slots` ✓
- **Non-grid (Review):** der 15-min-Bitset rundet konservativ. Selftest mit
  non-grid-Zeiten (05/10/17/23/41/50 min), Dauern 20/35/45/70/110, Tagesgrenzen
  (08:50/17:10), all-day, multi-day, Wochenende, Überlappungen:
  **0 unsichere Freiräume** (die Matrix erfindet nie Freiheit), aber in 58/60
  Fällen *konservativer* als die exakte Intervall-Logik (unterschlägt echte
  Freiheit). Ein 5-min-Raster reduziert das auf 49/60, ist bei 17/23/41 min aber
  nicht exakt. → **15 min = bewusste Produktentscheidung; für beliebige Minuten
  bleibt die Intervall-Logik oder ein feineres Raster.**
- **CPU-Zeit (Median, 15-min-Raster):** 2,8–4,0× schneller, Vorsprung wächst mit
  Personen/Horizont:

| Fall | Intervall | Matrix | Speedup |
|---|---|---|---|
| 1 Person / 7 Tage | 0.93 ms | 0.33 ms | 2.8× |
| 4 Personen / 30 Tage | 3.04 ms | 0.85 ms | 3.6× |
| 8 Personen / 30 Tage | 5.96 ms | 1.49 ms | **4.0×** |

→ Für CPU-only ist das der richtige Weg: billige deterministische Arithmetik,
Modelle nur für Sprache/Intent.

## C — Decomposer-Contract (Hypothese, Design only)

`DECOMPOSER_CONTRACT.md`: N3 wird zum **Zerleger** statt Arg-Extraktor
(`read_step(text)` / `write_step(text)`, verbatim, Reihum, kein Arg-Wissen).
Danach: `write_step → N2-FT` (0.977 atomar), `read_step → Python/Gemma`.
Begründung: N3 ist stark bei Multi/Toolwahl (0.90/1.000), schwach bei
`date`/`until`; N2 ist im Atomaren exzellent. Dataset v5 muss dafür die
atomaren **Teil-Queries** mit ausgeben (heute nicht der Fall) — noch nicht trainiert.

## D — Gemma als Read-Handler (Design + ready-to-run)

`gemma_read_probe.py`: **Gemma mutiert nie**. Python liefert einen strukturierten
14-Tage-Snapshot (Termine inkl. Teilnehmer) plus die gemeinsamen 60-min-Freiräume
für die in der Frage genannten Personen; Gemma formuliert die Antwort.
Review-Fix: der Facts-Builder interpretiert die Frage **nicht** mehr semantisch —
er erkennt „ich"/Gruppen korrekt (`_mentioned_persons`, Owner-aware) und lässt
Gemma aus dem Snapshot wählen. 20 Fragen: simple · semantic · group · control.
Lokal **nicht messbar** (kein `cactus serve`) — Skript überspringt sauber.

Rollen für Gemma (Mehrwert statt Overhead): Ambiguität klären · dependent Goals
planen · fehlerhafte/inkomplette Small-Model-Ausgaben reparieren · komplexe
Results zusammenfassen. **Nicht** als Reviewer nach jedem Needle-Call.

## Zielarchitektur (Cascade, Gemma als Ausnahme)

```
User → Needle-Dispatcher/Decomposer
        ├─ READ  → Python-Fakten (Kernel) [+ Gemma für semantische Reads]
        └─ WRITE → N2-FT (Specialist) → Python verify/execute
                    └─ unklar/abhängig → Gemma → atomare Schritte → N2-FT → Python
```

Einfache Reads brauchen kein Gemma (`"Was habe ich morgen?" → N2 read-view →
Python render`); Gemma erst bei semantischen/aggregierenden Fragen.

**Harte Architekturregel:** Gemma darf Features verbessern, aber das Kernsystem
darf ohne Gemma nicht unbrauchbar werden. Zwei Betriebsprofile ohne
Architekturänderung:

- **Core/Lite:** Dispatcher + N2-Write + Python-Reads (kein Gemma) — CPU-only.
- **Full Local:** zusätzlich Gemma für semantische Reads / Ambiguität / Planning.

## Offene nächste Schritte (kein Training ohne Freigabe)

1. **Pi-Messung zuerst** (siehe `BASELINES.md`): P0 Gemma→N2, P1, P2 +
   Cross-Routing + Gemma-Read auf echter Hardware.
2. **Cross-Routing** (`cross_routing.py`): write-toolset ← echte Read-Cases →
   `mutating_call_rate`; read-toolset ← echte Write-Cases. Erst das entscheidet,
   ob ein dedizierter Write-FT überhaupt nötig ist.
3. **v5-Decomposer-Dataset** (verbatim Teil-Queries) + kleines N3-Decomposer-FT —
   zielt auf den echten Bottleneck (Completeness/Silent Omission), primäre Metrik
   `all_steps_covered`/Intent-Recall.
4. **P2/P3-Bakeoff** der Cascade gegen P1/P0, sobald Gemma lokal messbar ist;
   Kernmetrik bleibt `escalation_rate` bei `wrong_mutations = 0`.
