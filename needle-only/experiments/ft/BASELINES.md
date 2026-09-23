# BASELINES — eingefrorene Referenzen & Promotion-Gate

Diese Datei ist der **unveränderliche Maßstab** für FT-Kandidaten. Zahlen kommen
aus dem Harness (`promotion_gate.py`), nicht aus Einzelscores. Nach jedem Run
sofort auswerten; die nächste GPU-Zeit muss durch das vorherige Ergebnis
begründet sein.

## Begriffe

| Kürzel | Bedeutung |
|---|---|
| Dataset **v2** | `data/train.jsonl` — 10 000 atomare Beispiele (5 Produktionstools, sparse Gold) |
| Dataset **v3** | `data/train_v3.jsonl` — v2 + ~25 % unabhängige Multi-Calls |
| Dataset **v4** | `data/train_v4.jsonl` — Preservation-Mix: 71.5 % atomic / 17.8 % multi / 10.7 % negatives+near-neg (25 072; nach Split korrekt gezählt) |
| Modell **n2-FT** | Needle 2, LoRA auf v2, 8 Epochen (SOTA/Produktionsreferenz) |
| Modell **n3-atomic** | Needle 3 auf v2 (`n3-v2-*`) |
| Modell **n3-mixed** | Needle 3 auf v3 (`n3-v3-*`) |
| Modell **n3-preserve** | Needle 3 auf v4 (`n3-v4-*`) |
| dependent chains | find_slot→create, resolve→move/delete — **nie trainiert**, nur als Agent-Loop evaluiert |

## Eingefrorene Baselines (Stand: Needle-3-Runde)

| Modell (Tag) | atomic tool/args/exact | challenge args/exact | multi(all) | negRefusal | falseRef | final_db | median |
|---|---|---|---|---|---|---|---|
| Base Needle 2 (`base`) | 0.863/0.449/0.113 | 0.520/0.120 | 0.80¹ | 57 % | 53 | 56–68 %² | 1206 ms |
| Needle 3 Base (`needle3-base`) | 0.746/0.230/0.063 | 0.360/0.040 | 0.80¹ | 4 % | 58 | 52 % | 184 ms |
| **n2-FT seed44 (`sa-r16-lr1e-4-e8-seed44`) — REFERENZ** | **0.991/0.977/0.977** | **0.840/0.680** | 0.30 | **97.5 %** | 5 | 72 % | 261 ms |
| n3-atomic e1 (`n3-v2-ft`) | 0.839/0.769/0.739 | 0.760/0.520 | 0.40 | 26 % | 1 | 68 % | 425 ms |
| n3-mixed e1 (`n3-v3-ft`) | 0.848/0.758/0.741 | 0.640/0.440 | 0.70 | 24 % | 7 | 72 % | 417 ms |
| n3-atomic e3 (`n3-v2-e3`) | 0.982/0.880/0.876 | 0.760/0.600 | 0.40 | 99 % | 30 | 80 % | 439 ms |
| n3-preserve e3 (`n3-v4-e3`) | 0.998/0.884/0.877 | 0.600/0.480 | 0.70 | **100 %** | 3 | 80 % | 438 ms |
| n3-preserve e5 (`n3-v4-e5`) | 1.000/0.889/0.886 | 0.720/0.600 | **0.90** | **100 %** | 0 | 80 % | 188 ms |

¹ Multi-Call der Bases (10 Fälle, hohe Varianz: ±1 Fall = 10 pp).
² `base` schwankt zwischen Läufen (25 Cases): 56 % vs. 68 % — L2 ist **verrauscht**.

**Fazit-Stand (Runde abgeschlossen):** Kein N3-Kandidat hält das Gate.
Der beste Kandidat **`n3-v4-e5`** besteht 5 von 7 Achsen — tool_ok 1.000,
**Multi-Call 0.90** (über Base 0.80 und weit über n2-FT 0.30), Negativ-Refusals
100 %, falsche Refusals 0 %, final_db 80 % (> Referenz). Er scheitert an der
**atomaren Genauigkeit**: exact 0.886 / args 0.889 gegen 0.95-Schwelle bzw.
n2-FT 0.977 — und 3 → 5 Epochen hoben sie nur von 0.877 auf 0.886 (+0.9 pp),
also **Plateau ~0.89**. Der lokale N3-LoRA-Pfad erreicht das N2-FT atomar nicht.
Entscheidung (Plan-Entscheidungsbaum): lokales N3-Tuning stoppen, **n2-FT bleibt
Produktionsreferenz**; n3 bleibt Forschungsstrang (Multi-Call-Stärke notiert).
Optionaler nächster Challenger: **1 Platform-FT-Run** (Phase 11) — nur mit
Cactus-Plan/API-Key und erst nach diesem Ergebnis.

## Promotion-Gate (Needle 3 ersetzt n2-FT nur, wenn ALLE Punkte erfüllt)

```
atomic args_ok  >= 0.95
atomic exact    >= 0.95
tool_ok         >= 0.98
multi all_acts  >= 0.80
neg refusal     >= 0.90
false refusal   <= 1 %  (der Positiven)
challenge args/exact  nicht > 5 pp schlechter als n2-FT
final_db        >= n2-FT
```

Ausführen: `PYTHONPATH=src .venv-ft3/bin/python experiments/ft/promotion_gate.py <tags…>`
(Exit 0, sobald ein Kandidat das Gate hält.)

## Evaluationspyramide (drei getrennte Ebenen, unveränderliche Vergleichslinie)

```
C  Production E2E            final_db_ok (echter Agent → Resolver → SQLite)
B  Agentic capability        manual loop  vs  run()   (echte Tools, dependent chains)
A  Model capability          A1 atomic complete()  ·  A2 independent multi complete()
```

- **A1/A2 sind eingefroren:** immer `reset()` → `complete(...)`; `base_eval.py`
  (1800 Test + Challenge) und `multi_call_bench.py` bleiben die Messlatte. `run()`
  kommt hier **nie** hinein.
- **A2-Nachtrag (ohne alte Zahlen zu ändern):** `all_actions_correct` bleibt der
  historische 10-Fälle-Score („legacy"); zusätzlich wird `multi_independent`
  ausgewiesen (ohne den dependent Fall `find+create`).
- **B ist neu und ersetzt A nicht:** `native_agent_bench.py` mit echten
  `build_tools(store)`-Callables, nur dependent chains, frischer Fixture.
- **C entscheidet über Produktion:** `tests/test_e2e.py` → `final_db_ok`.

### Track B — Ergebnis (10 dependent-chain-Fälle)

| Modell | manual goal_ok | run() goal_ok | wrong_writes | Bemerkung |
|---|---|---|---|---|
| n2-FT seed44 | 0.2 | 0.2 | 0 | Sequenz teils ja, Resultat-Grounding nein |
| N3 Base | 0.1 | 0.1 | **1 (manual)** | Sicherheitshinweis: falscher Write möglich |
| N3-preserve e5 | **0.4** | **0.4** | 0 | bester N3; scheitert v. a. `find_slot → create` |

**Gate 3 beantwortet:** `run()` bringt bei diesen dependent chains **keinen**
messbaren Vorteil gegenüber dem kontrollierten `complete → execute → complete`-Loop
(identische Scores). Häufigster Fehler: das Modell führt die richtige **Sequenz**
aus, überträgt aber das Tool-Resultat (Slot-Zeit) nicht in den Folge-Call.
Zusätzlich: `run()` exponiert **kein** per-Call-Argument-Transkript (nur `results` +
`suppressed_calls`) → schlechter auditierbar als der eigene Loop.

## Turn-Semantik (Plan Phase 8/9) — bewiesen

`experiments/ft/turn_semantics_probe.py` (Ergebnis: `reports/turn_semantics_n2ft-seed44.txt`):

- **Kontext-Isolation (robuster Contract-Test, keine Erwartung eines bestimmten
  Leaks):** derselbe Turn einmal mit **frischem** Agent und einmal mit
  **verschmutztem** Kontext (vorheriger Turn ohne `reset`, DB identisch).
  Ergebnis: **n2-FT divergiert in 3/3 Turns**, N3-preserve e5 in 1/3 → die
  Needle-Historie beeinflusst unabhängige Turns messbar → **`reset()` ist Pflicht**.
  (Frühere Einzelläufe zeigten die Wirkung exemplarisch, z. B. ein verfälschtes
  Title-Argument wie „wiederdas" ohne Reset — die konkrete Fehlform variiert,
  deshalb jetzt der Divergenz-Test statt eines Titel-Leak-Kriteriums.)
- **Dependent chains:** weder manueller `complete → execute → complete(result)`-Loop
  noch `run()` lösen `find_slot → create` (n2-FT): es bleibt beim ersten Call.
  Solche Ketten brauchen weiterhin eine höhere Schicht (Gemma) — nicht ins FT.
- Regel: **reset() genau einmal pro logischem User-Ziel; Kontext nur innerhalb
  einer explizit kontrollierten Iteration.**

## Eskalationssignale (Plan Phase 7 — kein Confidence-Gate für lokale FTs)

Fine-Tunes (N2/N3) tragen **keinen** Confidence-Head → `confidence = None`
(Quelle: `needle/model/finetune.py`, Docs „What a fine-tune does not change").
Produktions-Eskalation (zu Gemma/zur Rückfrage) entscheidet daher über
**beobachtbare, deterministische** Signale:

```
no calls · unknown tool · invalid arg type · required semantic info missing ·
resolver failure · ambiguous event resolution · verification failure ·
execution failure · dependent goal detected
```

Base-Needle-3 darf Confidence im Benchmark reporten; die Produktionsarchitektur
darf nicht davon abhängen, solange lokal trainiert wird (Platform-FTs hätten
kalibrierte Confidence — separater Challenger, Phase 11).

## Harness-Hinweise (Ehrlichkeit)

- L2 misst 25 Cases → ±3 Fälle = ±12 pp; Vergleiche mit Vorsicht, ggf. `--repeat`.
- Challenge misst 25 reale/Regressions-Fälle (bewusst „unfair").
- `base_eval` §22 „refusals ≤ base" mischt korrekte Negativ- und falsche
  Positiv-Refusals → immer die Zerlegung (`false_refusals_auf_positiven`,
  `korrekte Negativ-Refusals`) lesen.
