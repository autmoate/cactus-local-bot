# command_ir — Kalender Command-IR Spike

## Warum dieser Spike existiert

Der bisherige Kalender-NLP-Strang hat wiederholt gezeigt, dass Needle auf freien
Kalenderformulierungen schwach ist — aber die bisherigen Messungen beweisen
**nicht**:

> „Needle kann Kalender nicht."

Sie beweisen:

> „Needle + der alte **datenmodell-nahe 5-Tool-Contract** (date/until/time/
> end_time/horizon + mehrere Python-Korrekturpfade) war für freie
> Kalenderinteraktion nicht ausreichend."

Dieser Spike testet genau **eine** offene Hypothese:

> Ist Needle geeignet, wenn es **nicht** unser internes Datenmodell erzeugen
> muss, sondern lediglich einen kleinen, **sprach-nahen Command-IR** aus
> evidenzbasierten Textspans kompiliert?

## Aufbau (zweistufig)

```
User
  → Needle            → SURFACE COMMAND  (nur Textspans, evidence-only)
  → compiler.py       → CANONICAL COMMAND (absolute Zeiten, IDs, busy, shares)
  → neutraler Event Store (production calendar.py, read-only)
```

Needle darf: Operation erkennen, unabhängige Operationen separieren, Textspans
kopieren. Needle darf **nicht**: Daten berechnen, ISO erzeugen, Dauer/all_day/
busy/visibility bestimmen, IDs kennen, Availability/Kollisionen berechnen.

## Was hier NICHT passiert (bewusst)

- **Production `d046a97` bleibt unangetastet.** Dies ist ein separater,
  endlicher Spike.
- **Kein Fine-Tuning, kein Gemma, kein v5, kein dependent Agent, keine PWA,
  keine Telegram-/DB-Integration.**
- **Kein neuer Production-Parser-Sonderfall.** Der Compiler enthält nur generische
  Regeln (Datums-/Zeitparser, bekannte Personennamen, literales Titel-Matching,
  deterministische Patch-Semantik). Wenn eine Form nur über `if "zug"` /
  `if "abwesenheit"` / `if "verschiebe bitte"` funktionieren würde, wird sie
  als **Failure dokumentiert**, nicht als Sonderfall hinzugefügt.
- **Multi** umfasst ausschließlich **unabhängige** Intents. Result-dependent
  chains sind ausdrücklich out of scope.

## Dateien

| Datei | Aufgabe |
|---|---|
| `contracts.py` | zwei Kandidaten-Contracts A (phrase-first) / B (span-slotted), nur String-Args |
| `compiler.py` | generischer Calendar-Compiler (parse_when, TimePatch, resolve_target, add/show/availability) |
| `cases.py` / `cases.jsonl` | 81 eingefrorene Fälle + Gold A/B + absolute Gold-Endzustände |
| `fixtures.py` | synthetische Fixtures (Ada/Ben/Cleo), Referenz-Transform, echte Ausführung, Snapshots |
| `metrics.py` | Metriken + Failure-Taxonomie |
| `bench.py` | Compiler-Orakel + Base-Modell-Eval (N2/N3 × A/B) + alte Production-Baseline |

Keine echten privaten Daten — nur synthetische/anonymisierte Fixtures.

## Ausführen

```bash
# Compiler-Orakel (Gold Surface Commands -> Compiler -> finaler DB-Zustand)
PYTHONPATH=src .venv/bin/python experiments/command_ir/bench.py --oracle

# Base-Modelle
PYTHONPATH=src .venv/bin/python     experiments/command_ir/bench.py --model N2 --contract A
PYTHONPATH=src .venv-ft3/bin/python experiments/command_ir/bench.py --model N3 --contract B

# alte Production-Baseline (N2-FT, alte 5 Tools, dieselben Goals)
PYTHONPATH=src .venv/bin/python experiments/command_ir/bench.py --old-baseline \
    --weights experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact
```

Ergebnisse: `reports/` + `REPORT.md`.
