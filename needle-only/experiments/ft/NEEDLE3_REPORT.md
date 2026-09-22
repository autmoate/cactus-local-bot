# Needle 3 Benchmark (Phase D)

Umgebung: `cactus-needle==3.0.4` in separater `.venv-ft3` (Needle-2-Kette `.venv-ft`
bleibt unangetastet). Gleiche Schemas (`tools.json`), System-Facts und split-identische
Test-/Challenge-Sets wie in `RESULTS.md`. `auto_date=False`, damit die System-Facts
den FT2-Trainings entsprechen.

## D1 — API-/Schema-Kompatibilität

- Konstruktor rückwärtskompatibel: `Needle(tools, system, weights, tool_index_path, ...)`,
  neu: `auto_date=True` (injiziert ein Datums-Fact) und `generation`.
- `complete()`/`reset()`/`extract()` unverändert; `run()` neu (führt Tool-Callables aus).
- Antwort-Dict gleich geformt; **`confidence` jetzt kalibriert** (Beispiel 0.5521) —
  Needle-2-FT meldete `None`.
- **`.cact` ist engine-versionsgebunden** → das Needle-2-FT-`.cact` lädt in Needle 3
  nicht; für den FT-Vergleich ist ein Needle-3-Adapter nötig.
- Modelldatei 8–29 MB, `needle build --layers 2..20` vorhanden (Ladder-Export bestätigt).

## D2 — Frozen atomic Benchmark (Test n=1800 / Challenge n=25)

| Modell | test tool_ok | test args_ok | **test exact_args** | challenge exact | Median |
|---|---|---|---|---|---|
| Needle-2 Base | 0.863 | 0.449 | 0.113 | 0.120 | 1206 ms |
| **Needle-2 FT (seed 44)** | 0.991 | 0.977 | **0.977** | 0.680 | 261 ms |
| Needle-3 Base (20 Layer) | 0.746 | 0.230 | **0.063** | 0.040 | **184 ms** |

**Antwort auf die Leitfrage:** Needle 3 Base schlägt unser spezialisiertes Needle-2-FT
**nicht** — es liegt sogar unter Needle-2 Base. Erwartbar: Needle 3 Base ist nicht auf
unsere 5 Schemas, die sparse-Span-Konvention oder Deutsch trainiert. Latenz-Vorteil: ~30 %.

## D3 — Multi-Call-Benchmark (`multi_call_bench.py`, 10 Fälle, 2–4 Aktionen)

| Modell | call_count_ok | order_ok | args_ok | **all_actions_correct** | Median |
|---|---|---|---|---|---|
| Needle-2 Base | 0.9 | 0.9 | 0.8 | **0.80** | 486 ms |
| Needle-2 FT (seed 44) | 0.5 | 0.5 | 0.3 | **0.30** | 456 ms |
| Needle-3 Base `complete()` | 0.9 | 0.9 | 0.8 | **0.80** | 320 ms |
| Needle-3 Base `run()` | 0.2 | 0.2 | 0.2 | **0.20** | 1592 ms |

- **Regression bestätigt:** Das Needle-2-FT kollabiert Mehrfach-Aktionen (Dataset v2 ist
  rein atomar) — Base (Needle 2 *und* 3) schafft 0.80.
- **`run()` ist mit reinen JSON-Schemas unbrauchbar** (führt Callables aus; es bleibt beim
  ersten Call hängen) → für den Produktivpfad bräuchte es echte Tool-Funktionen + Executor
  (wie im `Agent`), nicht Schema-only.

## Level 2 (finaler DB-Zustand, 25 Cases, needle-Mode, ab Werk)
Needle-2 Base 68 % · Needle-2 FT 68–72 % · **Needle-3 Base 52 %** (ø 174 ms).
Needle 3 Base ist auch E2E schwächer — konsistent mit D2. (auto_date=Default, also
„as deployed"; mit `auto_date=False` wäre es minimal anders.)

## Fazit & empfohlene Reihenfolge

1. **Needle 3 Base ist für unsere Domäne zu schwach** → ohne FT nicht einsetzen;
   sein Mehrwert sind Multi-Call (0.80), kalibrierte Confidence und Latenz.
2. **Dataset v3** = v2 (atomic) **+ ~20–30 % Multi-Call** (unabhängig, Reihenfolge wie im
   Text) **+ getrennte dependent Chains** (find_slot→create, resolve→move/delete).
3. **LoRA auf dem 20-Layer-Needle-3-Basismodell**, dann Ladder-Export 20/16/12/8/4
   (`needle build --layers N`), Pi-Benchmark Accuracy/Latenz/RAM pro Layer.
4. Danach Architektur-Bakeoff: `Gemma→Needle2-FT` vs `Needle3-FT/run` vs
   `Needle3-FT + Gemma-Fallback`.

**Kein FT-v3-Training gestartet** (braucht deine Freigabe; Datenaufbau + Training mehrstündig).
Die `.cact`-Inkompatibilität zwischen Engine-Generationen ist dabei der Hauptgrund, den
Needle-2-FT weiterhin als stabilen Champion zu behalten, bis Needle-3-FT steht.
