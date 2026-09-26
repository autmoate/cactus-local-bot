# FT-Plan — Thunderbird Calendar (Datum/Zeit-Span-Extraktion)

Status: **Entwurf zur Freigabe. Kein Training gestartet.** Isolation: alle neuen
Dateien unter `business_cases/thunderbird_calendar/ft/`; `experiments/ft/`,
`experiments/command_ir/` und `src/local_calendar/` bleiben unberührt.

## 1. Warum überhaupt FT (Beleg, nicht Bauchgefühl)

Der Spike hat die Base-Grenze präzise lokalisiert (60 Fixtures,
`reports/n2.json`):

| Modell | whole-mail final | selection final | False-pos |
|---|---|---|---|
| N2 Base | 0.667 | 0.40 | 0.143 |
| N3 Base | 0.10 | 0.00 | 0.857 |

Der Compiler ist nicht das Problem (43/43 Gold-Spans, nach Fix `temporal.py`).
Der Fehler ist die **`when`-Span-Extraktion**: Base Needle löst Datum/Zeit auf
(ISO wie `2025-10-12 14:00`) statt den Span zu kopieren, und lässt Endzeiten
fallen. Genau diese „Span-Kopier-Shift" steht auch in
`experiments/ft/README.md` als Base-Baseline-Grund — das v2-FT hat sie dort
nachweislich reduziert. Die Aufgabe ist also strukturell dieselbe, nur mit
einem anderen Toolset und längerem Input (Mailtext statt Kurzbefehl).

## 2. Modellwahl (fixiert)

- **Primär: Needle 2 Base + LoRA** (`cactus-needle==2.0.13`).
  Begründung: N2 Base 0.667 ≫ N3 Base 0.10 auf dieser Aufgabe; N2-FT ist
  Produktionsreferenz; N2-Training war auf der RTX-Box thermisch stabil
  (`TRAINING_ENV.md`).
- **Sekundär (nur falls N2-Gate knapp verfehlt): Needle 3 Base + LoRA**
  (`cactus-needle==3.0.4`, Modal-Pfad existiert bereits).
- **Nicht** verwendet: `autmoate/cactus-needle2-calendar` (N2-FT auf den fünf
  Produktionstool-Schemas) — falscher Contract/Use-Case.

## 3. Eingefrorener Contract (identisch zum Spike)

```python
@needle.tool
def extract_event(title: str, when: str, location: str = "") -> str:
    """Use when the message announces an appointment.
    Args:
        title:    event title exactly as written
        when:     the date AND clock-time phrase copied character for character,
                  including the end time when given; never ISO or calculated
        location: the place exactly as written; empty if none
    """

@needle.tool
def no_event() -> str:
    """Use when the message contains no appointment and no date."""
```

System-Facts exakt wie im Spike: `"locale: de-DE"`.

**Prompt-Format (train == inference, kritisch):**
`extractor._extract` rendert `Betreff: {subject}\n\n{text}`. Die generierte
`query` ist daher exakt dieser String — inkl. `Betreff:`-Zeile. Bei
Selection-Mode ist `text` der markierte Ausschnitt, der Betreff bleibt Kontext.
Es wird **kein** `reference_time` an das Modell gegeben; das Modell kopiert den
Span, Python löst relativ zu `received_at` auf (bestehendes Design).

## 4. Dataset-Design (neu, deterministisch, seedbar)

Ort: `ft/dataset_spec_tb.py` + `ft/build_dataset_tb.py` (Prinzipien aus
`experiments/ft/dataset_spec.py`, aber eigenständig — keine Schema-Duplikation,
Splits familien-exklusiv, Held-out-Value-Pools, harter Count-Check).

### Güte-Konvention
- **SPARSE/evidenced-only** (Needle-Finetune-Konvention): `location` nur wenn
  im Text; leeres `arguments` ist legal (`no_event` → `answers: []`).
- **Verbatim-Grounding (Validator, hart):** jeder String-Arg-Wert ist ein
  Substring der `query` (normalisiert). `when` muss mindestens ein Datumstoken
  enthalten; enthält der Quell-Satz eine Uhrzeit-Range, muss `when` sie
  abdecken (Endzeit nicht abschneiden).
- Negatives: `answers: []` → `no_event`.

### Familien (split-exklusiv)
| Block | Familien | Zielanteil |
|---|---|---|
| Single explicit | `DD.MM.` / `DD.MM.YYYY` + Uhrzeit, Monatsname, ISO-Range `von..bis`, `14-16 Uhr` | ~30% |
| Day-only | Tag ohne Uhrzeit, `7. und 8. Oktober` (mehrtägig) | ~10% |
| Relativ | `morgen`, `übermorgen`, `nächsten Dienstag`, `kommenden Freitag` (+Uhrzeit) | ~15% |
| Ort/Online | Raum, Adresse, `online via Meet` | ~10% |
| Format-Varianz | lang mit Signatur, Antwort-Thread mit Zitat-Historie (Event nur im neuen Text), `Re:`-Betreff | ~15% |
| EN simple | `tomorrow`, `next Tuesday`, `from 10 to 16` | ~7% |
| Negativ | off-topic (~2/3) + kalender-nah ohne Event (Terminbestätigung, Verfügbarkeitsfrage, Rechnung) (~1/3) | ~8% |
| Selection | kurzer Ausschnitt aus Multi-Mail (Markierung) | ~5% |

- **Multi-Event** (2–3 Calls in einer Mail) ist als eigener Block vorgesehen,
  aber **v2, nicht v1**: das Architektur-Bakeoff zeigt Multi-Call als separates
  hartes Problem. v1 trainiert Single-Event + Refusal; Multi bleibt der
  Selection-Fallback des Produkts.
- Sprachmix: ~80% DE, ~15% EN, ~5% Gemma-atomar.
- Mails realistisch, synthetisch/anonymisiert, keine Anhänge.

### Umfang (v1)
train ~8.000 · validation ~800 · test ~1.500 (exakte Counts erzwungen).
Zusätzlich eingefroren und **nie im Training**:
- `cases.jsonl` (die 60 Spike-Fixtures) als Challenge-Set,
- ein neues `ft/challenge_tb.jsonl` mit realitätsnahen, handgeschriebenen
  Mails inkl. der bekannten Fehlklassen.

### Manifest
`ft/manifest.json`: `build_dataset_sha256`, `dataset_spec_sha256` (Spec-/Code-
Hash statt Parent-Commit), `schema_sha256` (der 2 Tools), `seed`,
`file_sha256` pro Split, counts, Sprach-/Klassen-Coverage, Gold-Konvention.

## 5. Training

### Primärpfad: Modal
- Setup (einmalig, Nutzer): `cd needle-only && uv sync --extra modal --no-dev`;
  Modal-Account + Token (`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` in `.env`, wird
  von den Skripten intern via `load_dotenv()` geladen, nie geloggt/gedruckt).
- Skript `ft/modal_train_tb.py`: lädt rohe JSONLs hoch, injiziert im Container
  `tools_tb.json` + System-Facts, ruft `needle finetune` und `needle build`
  → `.cact` ins Volume `cactus-ft-artifacts-tb`.
- **Smoke-Test zuerst**: Mini-Dataset (200 Zeilen), 1 Epoche, 1 GPU, um den
  N2-GPU-Finetune-Pfad auf Modal zu verifizieren, bevor die Matrix läuft.
- Matrix (erste Runde, klein): rank 8/16 × lr 5e-5/1e-4 × epochs 3/5/8 ×
  seeds 42/43/44 → **Start mit 3 Runs** (r16/lr1e-4/e5, r8/lr1e-4/e8,
  r16/lr5e-5/e8, seed 42) und nur bei Bedarf erweitern.
- GPU: A100-40GB ($2.10/h); N2 ist 45M → Runs ≪ 1 GPU-h, erste Runde im
  einstelligen Dollar-Bereich. **Budget-Cap: ≤ $10 / ≤ 2 GPU-h**, Abbruch bei
  Überschreitung.

### Fallback: lokale RTX
N2 (nicht N3) lief lokal thermisch stabil; `train_rtx.py` kann mit
`--data ft/data/train.jsonl` auf das neue Dataset zeigen. Nur falls Modal-Setup
scheitert.

## 6. Evaluation & Gate (vor dem Training fixiert)

Harness: bestehendes `eval.py --weights <cact>` (unterstützt bereits FT-Weights)
gegen: (1) `cases.jsonl` (60), (2) generiertes Held-out-Testset, (3)
`challenge_tb.jsonl`. Whole-mail und Selection getrennt.

**Promotion-Gate (muss auf ALLEN drei Sets halten):**
| Kriterium | Base | Ziel |
|---|---|---|
| whole-mail `final_event_ok` | 0.667 | **≥ 0.85** |
| `false_positive_rate` | 0.143 | **≤ 0.05** |
| selection `final_event_ok` | 0.40 | **≥ 0.90** |
| `approval_ready` | 0.633 | **≥ 0.80** |
| `event_detection_recall` | 1.00 | keine Regression |

Entscheid (pre-registered, analog Spike §29):
- **GO**: Gate hält → echter Thunderbird-Add-on-Prototyp planen.
- **PROMISING**: whole-mail 0.65–0.85 **und** selection ≥ 0.90 → Produkt-V1
  bewusst selection-first.
- **WEAK**: selection < 0.85 → kein weiteres FT; Contract/Decoding neu denken.

Gate-Skript: `ft/gate_tb.py` liest die Eval-Reports und erzwingt die Tabelle
automatisch (Rückgabe != 0 bei Verfehlung).

## 7. Bewusste Nicht-Ziele

Kein Multi-Call-FT in v1, kein Gemma, kein Fine-Tuning der Participant-Logik
(Header bleiben Python-Wahrheit), kein echtes Thunderbird, kein SMTP/iTIP.
Confidence ist bei FT-Modellen `None` — der Approval-Flow nutzt `needs_review`/
`status`, nicht Confidence (bereits so im Spike).

## 8. Risiken

1. N2-GPU-Finetune auf Modal ist ungetestet → Smoke-Test vor Matrix.
2. Verbatim-Span-Kopieren könnte auch nach FT unvollständig konvergieren;
   dann greift die pre-registered WEAK-Regel (kein Add-on).
3. Lange Mails/Signaturen vs. `max_len` 1024 → Mails < ~250 Wörter halten,
   sonst `max_len` anheben.
4. Selection-Mode mit Betreff-Leak → im Dataset bewusst variieren, in Eval
   sichtbar machen.

## 9. Was vom Nutzer benötigt wird

1. **Freigabe dieses Plans** (Gate, Modellwahl, Umfang).
2. Modal-Account + Token in `.env` (`MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET`)
   und einmalig `modal setup`/Login.
3. **Finale Freigabe vor `modal run`** (kein Training ohne explizites Go).
4. Budget-Cap-Bestätigung (Vorschlag ≤ $10 / ≤ 2 GPU-h erste Runde).

## 10. Umsetzungsreihenfolge nach Freigabe

1. `dataset_spec_tb.py` + `build_dataset_tb.py` + Validator; 60er-Challenge
   einfrieren; Manifest.
2. `modal_train_tb.py` + Smoke-Test (200 Zeilen).
3. Base-Baseline auf allen drei Sets neu einfrieren (mit neuem Dataset/Seed).
4. Trainingsmatrix (3 Runs), Export `.cact`.
5. `eval.py --weights` + `gate_tb.py`; Ergebnisbericht (GO/PROMISING/WEAK).
6. Erst danach: Entscheidung über Add-on-Prototyp.
