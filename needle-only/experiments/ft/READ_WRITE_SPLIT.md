# Read/Write-Split & Compute-Rationalisierung (Ablation, kein Training)

Ziel: **Gemma-Compute einsparen**, ohne Zuverlässigkeit zu verlieren. Dazu wurden
bestehende Modelle mit **eingeschränkten Toolsets** gemessen (frozen Subsets),
ein **Availability-Kernel** prototypisiert und ein **Decomposer-Contract** entworfen.
Kein Training, keine Migration, nichts Bestehendes umdefiniert.

## A — Toolset-Ablation (frozen: 558 read · 1080 write · 162 neg)

| Modell / Toolset | Klasse | n | tool_ok | args_ok | exact | Refusals (Neg) |
|---|---|---|---|---|---|---|
| **N2-FT write-only** | write | 1080 | **1.000** | **0.987** | **0.984** | 0.506 |
| N2-FT read-only | read | 558 | 0.977 | 0.959 | 0.957 | 0.914 |
| N3-E5 write-only | write | 1080 | 0.959 | 0.818 | 0.816 | 0.944 |
| N3-E5 read-only | read | 558 | 0.896 | 0.659 | 0.600 | 0.852 |
| _(Referenz, alle 5 Tools)_ N2-FT | both | 1800 | 0.991 | 0.977 | 0.977 | 0.975 |
| _(Referenz, alle 5 Tools)_ N3-E5 | both | 1800 | 1.000 | 0.889 | 0.886 | 1.000 |

**Befunde**
1. **N2-FT ist auf beiden Ebenen der stärkere Spezialist** — read-only 0.957 exact,
   **write-only 0.984 exact** (tool_ok 1.000). Die Verengung auf die 3 Write-Tools
   *verbessert* N2 leicht (0.977 → 0.984) und macht die Toolwahl perfekt.
2. **N3 degradiert beim Verengen** (0.886 → 0.816 write / 0.600 read): sein FT
   hängt am 5-Tool-Kontext; die Multi-Call-Stärke wird in diesen atomaren Subsets
   gar nicht gemessen.
3. **Sicherheitsrelevant:** write-only verweigert Read-Formulierungen nur zu ~50 %
   (die frozen Negatives enthalten Read-Queries). Ein Write-Spezialist braucht
   daher **Read-/Off-Topic-Negatives im Training** — oder der Dispatcher
   garantiert, dass Reads nie beim Write-Modell landen.
4. **Konsequenz:** Für einen Read/Write-Split wären **dedizierte FTs** sinnvoll
   (Write: create/move/delete + starke Read-Negatives; Read: list/find_slot +
   höherwertige Read-Primitives), statt bestehende 5-Tool-Modelle zu verengen.

## B — Availability-Kernel (Bitset/Slot-Matrix)

`availability_kernel.py`: projiziert den Event-Store auf Tage × 15-min-Slots ×
Personen (int-Bitmasken). Überlappungen werden `OR`, gemeinsame Freiheit `AND` —
Python bleibt Wahrheit (Events halboffen), die Matrix ist nur Projektion.

- **Identität:** 60/60 randomisierte Fixtures liefern **exakt dieselben** freien
  Slots wie die bestehende `find_free_slots` (Intervall-Logik) ✓
- **CPU-Zeit (Median):** 2,8–4,0× schneller, Vorsprung wächst mit Personen/Horizont:

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

`gemma_read_probe.py`: **Gemma mutiert nie**. Python liefert kompakte Fakten
(Snapshot / Kernel-Slots), Gemma formuliert die natürliche Antwort (20 Fragen:
simple · semantic · group · control). Lokal **nicht messbar** (kein `cactus serve`
auf dieser Box) — Skript überspringt sauber und nennt das Setup.

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

**Harte Architekturregel:** Gemma darf Features verbessern, aber das Kernsystem
darf ohne Gemma nicht unbrauchbar werden. Zwei Betriebsprofile ohne
Architekturänderung:

- **Core/Lite:** Dispatcher + N2-Write + Python-Reads (kein Gemma) — CPU-only.
- **Full Local:** zusätzlich Gemma für semantische Reads / Ambiguität / Planning.

## Offene nächste Schritte (kein Training ohne Freigabe)
1. **Write-only-N2-FT mit Read-Negatives** trainieren (Ziel: tool 1.000 **und**
   Refusal auf Reads ~1.0) — kleiner Modal-Run, klarer Nutzen.
2. **Read-only-N2-FT** mit höherwertigen Read-Primitives (snapshot/availability).
3. **v5-Decomposer-Dataset** (Teil-Queries) + kleines N3-Decomposer-FT.
4. **P2/P3-Bakeoff** der Cascade gegen P1/P0, sobald Gemma lokal messbar ist;
   Kernmetrik bleibt `gemma_invocation_rate` bei `wrong_writes = 0`.
