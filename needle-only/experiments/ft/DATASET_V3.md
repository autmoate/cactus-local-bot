# Dataset v3 — v2 (atomar) + Multi-Termin (Needle-3-Vertrag)

Ziel: Ein Needle-3-FT, das **mehrere Termine in einem Request** in Reihenfolge als
mehrere Calls ausgibt (Needle-3-Kernfähigkeit) — ohne die atomare Genauigkeit von
v2 zu verlieren. Motivation: das Needle-2-FT auf v2 kollabiert strukturierte Listen
zu einem Call (`experiments/MULTI_INTENT_REPORT.md`), Base-Needle 2/3 schaffen 0.80.

## Aufbau

| Split | Datei | Zeilen | davon Multi-Call |
|---|---|---|---|
| train | `data/train_v3.jsonl` | 12 282 | 2 282 (~25 % der Positives) |
| validation | `data/validation_v3.jsonl` | 1 227 | 227 |

- **Basis = v2 unverändert** (10 000 train / 1 000 val: create/move/delete/list/find,
  sparse-Gold, Helden-/Wert-Pools, ~9 % Negatives) → atomare Qualität bleibt messbar.
- **Zusätzlich Multi-Call** (10 Template-Familien, `build_v3.py`), 2–4 unabhängige
  Aktionen in Text-Reihenfolge: `und`-Verkettung, Komma-Listen, Bullet-Listen,
  `move+delete`, `delete+create`, `list+create`, `mit <Person>`-Kombinationen.
- **Listen-Negatives** (8, off-topic): Einkaufs-/Pack-/Merklisten → `answers: []`,
  damit das Modell „Liste" nicht pauschal als Kalender liest.
- **Grounding wie v2**: jeder Argumentwert ist literaler Substring der Query
  (maschinell geprüft: 0 echte Verstöße; die einzigen „Abweichungen" sind die
  dokumentierten Enum/Int-Ausnahmen `horizon`/`days`).
- **Kein Leak**: Held-out-Werte des frozen v2-Tests (TÜV, Miriam, Felix, Nora …)
  werden im v3-Multi-Teil gemieden; Familien je Split exklusiv (`-train`/`-val`).

## Bewusst NICHT trainiert (eval-only)

**Dependent chains** (`find_slot` → `create`, `resolve` → `move`/`delete`): der zweite
Call braucht das Ergebnis des ersten. Needles `finetune`-Format ist single-turn
(`query → answers`) und kann Tool-Resultate nicht abbilden — solche Ketten werden
daher separat mit `run()`/manueller Ergebnis-Schleife evaluiert, nicht trainiert.
Ebenfalls außen vor: Kollisionen, mehrteilige Absences, Multi-Step-Planning.

## Reproduktion

```bash
cd needle-only
PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/build_v3.py   # seed 42
# -> data/train_v3.jsonl, data/validation_v3.jsonl, data/v3_manifest.json
```

Training (Needle 3, 20 Layer, batch 2 wegen WSL-VRAM):

```bash
PYTHONPATH=src ../.venv-ft3/bin/python experiments/ft/train_rtx.py \
    --run-name n3-v3-r16-lr1e-4-e5-seed42 --data experiments/ft/data/train_v3.jsonl \
    --epochs 5 --rank 16 --lr 1e-4 --seed 42 --batch-size 2
```

`v3_manifest.json` hält seed, Mix und Datei-Hashes. Die Multi-Call-Zeilen tragen
noch kein `tools`/`system` — die Injektion macht `train_rtx.py` (wie bei v2).

## Eskalations-Idee (Confidence-Gate, noch nicht verdrahtet)

Needle 3 liefert **kalibrierte** Confidence pro Turn. Vorgesehene Regel: liegt die
Confidence unter einem Schwellwert (Vorschlag **0.3**) — bei mehreren Calls: mindestens
einer darunter — wird der Turn an Gemma eskaliert (Formulierung/Ambiguität), statt
auszuführen. Die Evaluations-Reports weisen dafür jetzt `confidence`
(mean/median/`share_below_0.3`) aus, damit der Schwellwert datenbasiert gewählt wird.
