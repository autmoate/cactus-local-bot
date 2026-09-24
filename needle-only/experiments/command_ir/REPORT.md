# REPORT — Kalender Command-IR Spike

**Basis-Commit:** `d046a97` (Production unangetastet — 0 Änderungen an
`src/local_calendar/`).
**Datum-Kontext (fixiert):** today = 2026-09-24 (Thu).
**Fälle:** 81 (15 add, 12 change, 8 remove, 8 show, 7 availability, 21
independent multi, 10 negative).
**Roh-Reports:** `reports/*.json`.

---

## 1. Contract A / B

| | Contract A (phrase-first) | Contract B (span-slotted) |
|---|---|---|
| add | `title, when, people` | `title, when, people` |
| change | `target, change` | `target, new_when, new_title` |
| remove | `target` | `target` |
| show | `query` | `when, person` |
| availability | `query` | `people, when, duration` |

Nur String-Args, alle semantischen Werte ausschließlich im Compiler.
`change_rename_time` (Rename **und** Zeit in einem Zug) ist in Contract A
strukturell nicht ausdrückbar → nur gegen B gewertet (im Report als
Contract-Grenze markiert, keine Model-Schuld).

---

## 2. Compiler-Orakel (Gold Surface → finaler DB-Zustand)

| Familie | A compile/final | B compile/final |
|---|---|---|
| add | 1.00 / 1.00 | 1.00 / 1.00 |
| change | 1.00 / 1.00 | 1.00 / 1.00 |
| remove | 1.00 / 1.00 | 1.00 / 1.00 |
| show | 1.00 / 1.00 | 1.00 / 1.00 |
| availability | 1.00 / 1.00 | 1.00 / 1.00 |
| multi | 1.00 / 1.00 | 1.00 / 1.00 |
| negative | 1.00 / 1.00 | 1.00 / 1.00 |

**`compiler_oracle_goal_ok = 1.00`** für beide Contracts. Der Compiler ist
generisch und vollständig; er brauchte **keine** Formulierungs-Sonderfälle.
Alle untenstehenden Fehler sind daher Modell-Fehler, nicht Compiler-Fehler.

---

## 3. Base Needle 2 (N2, cactus-needle 2.0.13)

| Familie | tool_ok | compile_ok | final_ok | span_ground | wrong_mut | p50 ms |
|---|---|---|---|---|---|---|
| add | 0.87 | 0.60 | **0.27** | 0.61 | 3 | 1092 |
| change | 0.00 | 0.73 | **0.00** | 0.50 | 0 | 504 |
| remove | 0.38 | 0.62 | **0.25** | 1.00 | 0 | 831 |
| show | 0.00 | 0.50 | **0.38** | 0.86 | 1 | 977 |
| availability | 0.00 | 1.00 | **0.71** | 1.00 | 2 | 754 |
| multi | 0.52 | 0.62 | 0.33 (all_intents) | 0.74 | 6 | 882 |
| negative | — | 0.90 | refusal 0.80 | 0.33 | 1 | 387 |

**Atomic final_goal_ok (add/change/remove/show/availability, gewichtet) =
0.286.** A ≈ B (B 0.320).

---

## 4. Base Needle 3 (N3, cactus-needle 3.0.4)

| Familie | tool_ok | compile_ok | final_ok | span_ground | wrong_mut | p50 ms |
|---|---|---|---|---|---|---|
| add | 0.87 | 0.53 | **0.33** | 0.76 | 2 | 550 |
| change | 0.36 | 0.64 | **0.27** | 0.83 | 4 | 509 |
| remove | 0.75 | 0.62 | **0.62** | 0.80 | 0 | 449 |
| show | 1.00 | 1.00 | **1.00** | 1.00 | 0 | 558 |
| availability | 0.00 | 0.71 | **0.71** | 1.00 | 0 | 466 |
| multi | 0.67 | 0.48 | 0.38 (all_intents) | 0.90 | 2 | 840 |
| negative | — | 1.00 | refusal 0.10 | 1.00 | 0 | 429 |

**Atomic final_goal_ok = 0.531.** A ≈ B (B 0.500; B gewinnt change/remove,
verliert add).

N3 ist klar besser als N2 (0.53 vs 0.29–0.32), **show** und **availability**
funktionieren solide, aber: **negative refusal bricht ein (0.10)** — N3 ruft bei
Off-Topic zu oft Tools auf — und **Multi-Intent-Recall bleibt 0.33–0.38**.

---

## 5. Alte Production-Baseline (N2-FT `ba3212ab`, alter 5-Tool-Contract, dieselben Goals)

| Familie | final_ok | wrong_mut |
|---|---|---|
| add | 0.67 | 5 |
| change | 0.58 | 4 |
| remove | 0.88 | 0 |
| multi | 0.19 (all_intents) | 13 |
| negative | refusal 0.90 | 0 |

**Atomic final_goal_ok = 0.686.** Die alte Pipeline ist auf denselben freien
Goals messbar besser als die Base-Modelle auf dem neuen Contract — aber
(siehe §16) noch unter dem GO-Schwellwert, und sie produziert in Multi **13
tatsächliche Wrong Mutations**, weil sie Calls sequenziell ausführt statt
all-or-nothing. (Base-vs-FT ist kein sauberer Contract-Vergleich; deshalb ist
§5 nur eine Produkt-Referenz, kein Contract-Sieger.)

---

## 6. Atomic-Breakdown (final_goal_ok, gewichtet)

| Contract/Modell | add | change | remove | show | availability | **atomic** |
|---|---|---|---|---|---|---|
| N2 + A | 0.27 | 0.00 | 0.25 | 0.38 | 0.71 | **0.286** |
| N2 + B | 0.20 | 0.00 | 0.25 | 0.62 | 0.86 | **0.320** |
| N3 + A | 0.33 | 0.27 | 0.62 | 1.00 | 0.71 | **0.531** |
| N3 + B | 0.00 | 0.50 | 0.75 | 0.88 | 0.86 | **0.500** |
| old N2-FT | 0.67 | 0.58 | 0.88 | n/a | n/a | **0.686** |

Keine Kombination erreicht 0.70. `add` und `change` sind durchgehend am
schwächsten.

## 7. Multi-Breakdown (unabhängige Intents)

| Modell/Contract | all_intents_covered | silent_omission | wrong_mut |
|---|---|---|---|
| N2 + A | 0.33 | 9 | 6 |
| N2 + B | 0.10 | 11 | 10 |
| N3 + A | 0.38 | 12 | 2 |
| N3 + B | 0.33 | 11 | 3 |
| old N2-FT | 0.00 | — | 13 |

N3 verliert weiterhin regelmäßig unabhängige Intents (Recall 0.33–0.38, hohe
Silent Omission). Der neue all-or-nothing-Preflight senkt Wrong Mutations stark
(N3 2–3 vs. alt 13), weil ungültige Teilpläne gar nicht angewendet werden.

## 8. Failure-Taxonomie (Summen)

| Modell/Contract | MODEL_SPAN | MODEL_TOOL | MODEL_EXTRA | MODEL_OMISSION | COMPILER_TARGET | COMPILER_TIME | AMBIGUOUS | EXECUTION |
|---|---|---|---|---|---|---|---|---|
| N2 + A | 24 | 15 | 4 | 1 | 3 | 4 | 3 | 2 |
| N2 + B | 25 | 15 | 3 | 3 | 3 | 4 | 3 | 2 |
| N3 + A | 13 | 10 | 9 | 1 | 6 | 3 | 2 | 2 |
| N3 + B | 21 | 16 | 8 | 1 | 5 | 1 | 1 | 3 |

**Dominant: MODEL_SPAN + MODEL_TOOL.** Konkret: die Base-Modelle **kopieren die
Textspans nicht**, sondern normalisieren — z. B. `when="2026-09-28T07:13:00"`
statt `"am 28.9. um 7:13 Uhr"` — und verweigern oder verwechseln Tools (bevorzugt
bei `add`/`change`). COMPILER_* ist klein und in allen Fällen Folge eines bereits
fehlerhaften Modell-Outputs (z. B. ISO statt Span), kein Compiler-Bug.

Wichtig: `span_grounding` der **Base-Modelle** liegt bei 0.50–1.00 — sie
verletzen die Evidence-only-Regel systematisch. Das ist genau die Kontrakt-
Eigenschaft, die ein FT lernen müsste.

## 9. Latenz (p50, CPU/Pi)

| Modell | p50 ms | p95 ms |
|---|---|---|
| N2 | 733–1261 | ~1200–3500 |
| N3 | 408–867 | ~560–870 |
| old N2-FT | ~1000 | ~2500 |

N3 ist schneller als N2 und deutlich schneller als der alte Agent-Pfad.

---

## 10. GO / PROMISING / KILL (nach pre-registrierten Regeln §16)

| Kriterium | Ziel | Ergebnis |
|---|---|---|
| Compiler-Orakel | ~1.0 | **1.00 erfüllt** |
| Atomic final_goal_ok | ≥ 0.85 (GO) / 0.70–0.85 (PROMISING) | **0.29–0.53** |
| systematische Operation-Familie | keine < 0.75 | add/change < 0.75 |
| Multi all_intents_covered (N3) | ≥ 0.80 | **0.33–0.38** |
| Wrong Mutations nach Preflight | keine unerklärten | 2–16 (erklärbar, aber vorhanden) |

### Verdikt: **KILL** (Calendar-NL-Research-Strang)

Atomic final_goal_ok liegt mit **0.29–0.53** klar unter der 0.70-Grenze, und N3
verliert weiterhin regelmäßig unabhängige Intents (0.33–0.38). Nach den
vorab definierten Regeln wird der Calendar-Natural-Language-Researchstrang
**beendet**. Es folgt **kein** drittes Contract-Design.

### Differenzierte Einordnung (ehrlich, nicht schönreden)

- Der **Compiler ist sauber** (Orakel 1.00, keine Phrase-Zoos). Die Hypothese
  „der alte Contract sei die eigentliche Bremse" ist damit **für den Compiler
  bestätigt**, aber die Zielrate rettet das nicht.
- Die **Bremse ist das Base-Modell**: die dominante Fehlerklasse ist MODEL_SPAN
  (Base-Modelle normalisieren statt Spans zu kopieren) plus MODEL_TOOL
  (Verwechslung/Verweigerung bei add/change). Genau diese Fähigkeit wäre nur
  über **ein neues FT auf dem finalen Contract** zu heben.
- Das war **nicht** Teil dieses Spikes (§17). Die Zahlen liefern aber eine
  belastbare Entscheidungsgrundlage für ein solches FT: es gäbe einen
  sprach-nahen Contract, der zu 100 % deterministisch kompilierbar ist, und ein
  isoliertes Lernziel („Span-Kopie statt Normalisierung").

### Production bleibt

`d046a97` bleibt unverändert der Quick-Action-/UI-Core
(Telegram + N2-FT seed44 + Python + SQLite + Preview/Confirm + ID-UI). Die
Erkenntnisse (Trennung Geometrie/Busy/Sichtbarkeit; UI-getriebene ID-Edit-Pfade;
Span-Kopie als Lernziel) werden auf den nächsten Use Case übertragen.

**Kein FT, keine Production-Migration, keine Telegram-Änderung, keine weitere
Contract-Runde.**
