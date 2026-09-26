# FT-Plan v2 — Thunderbird Calendar (enges Produktproblem)

Status: **Entwurf v2 zur Freigabe. Kein Training.** Isolation: alles unter
`business_cases/thunderbird_calendar/`; `experiments/ft/`,
`experiments/command_ir/`, `src/local_calendar/` bleiben unberührt.

v2 revidiert v1 vollständig nach dem Produkt-Review: Die Aufgabe wird von
„Datum aus beliebiger Mail ziehen" auf **eine klar abgegrenzte semantische
Grenze** verengt. v1 hätte ein Modell auf das falsche Produktproblem trainiert.

## 0. Produkt-Wedge (die eigentliche Definition)

> Schließt die Lücke zwischen **informeller Terminvereinbarung per Mail** und
> einer **echten Kalendereinladung**.

Nicht: „AI erkennt Termine". Sondern:

> Ein neuer Termin ist **bereits konkret vereinbart, bestätigt oder
> angekündigt**, aber es existiert **noch kein strukturiertes Kalenderobjekt**.

- Native Kalender-Einladung vorhanden? → Thunderbird kann es schon. **Bypass.**
- Noch laufende Terminabstimmung? → noch nichts erstellen.
- Absage / Verschiebung? → später ein eigener Workflow, nicht V1.
- Mehrere Termine? → Selection-Fallback.
- Ein klarer informeller Termin? → **hier glänzt das Add-on.**

## 1. Mail-Taxonomie → V1-Verhalten (festschreiben)

| Mailtyp | Beispiel | V1-Verhalten |
|---|---|---|
| Native Kalenderinvitation (ICS/text-calendar) | Outlook/Teams-Einladung | **Bypass. Kein Needle.** |
| Informell bestätigter Termin | „Dienstag 14 Uhr passt, bis dann." | **Kern-Use-Case** |
| Formelle Terminbestätigung ohne Invite | „Ihr Termin ist am 12.10. um 9 Uhr." | **Kern-Use-Case** |
| Veranstaltungsankündigung | „Workshop am 8.11. von 10–16 Uhr" | **Kern-Use-Case** |
| Terminabstimmung | „Passt Di 14 oder Mi 10?" | **Kein Create** |
| Zusage nach Abstimmung | „Dienstag 14 Uhr passt." | **Kern-Use-Case**, ggf. Subject als Titel |
| Mehrere Termine | Agenda Mo/Di/Fr | Whole-mail schwierig → **Selection** |
| Verschiebung | „Statt Dienstag jetzt Mittwoch" | **Kein Create** (Update, später) |
| Absage | „Termin fällt leider aus" | **Kein Create** |
| Deadline | „Unterlagen bis Freitag schicken" | Kein Kalendertermin |
| Reine FYI-Daten | „… das Meeting vom 12.10. war …" | Kein Termin |

Der „📌 Termin vorbereiten"-Button liefert bereits eine starke Intent-
Präselektion; die Modellentscheidung ist nur noch **Innerhalb**: eindeutig /
unvollständig / kein eindeutiger neuer Event.

## 2. Zwei Buttons — Einladung ist NIE Default

```
[📅 In meinen Kalender]      → nur EventCandidate, keine Teilnehmer
[👥 Meeting organisieren]    → erst dann: Teilnehmer aus From/To/Cc
                                (Checkboxen), dann [Einladung vorbereiten]
```

Grund: Bei „Ihr Termin ist am 12.10. um 9:30 Uhr" (Arztpraxis) darf das Add-on
die Praxis nicht automatisch einladen. Teilnehmer stammen ausschließlich aus
strukturierten Headern (`workflow.participants_from_message`) und werden nie vom
Modell entschieden — bleibt so.

## 3. Selection ist Escalation Path, nicht erster Schritt

Produktflow:

```
Mail öffnen → 📌 Termin vorbereiten → whole-mail extraction
   eindeutig            → Preview
   unvollständig        → Preview + markierte Unsicherheiten
   kein eindeutiger     → „Relevante Stelle markieren" → Selection
```

**Selection-Semantik muss korrigiert werden.** Aktuell schickt
`extractor._extract` auch im Selection-Mode `Betreff: …\n\n<selected>` — also
„Subject + Selection", und der Spike-Report dokumentiert Subject-Leak als
Fehlerklasse. Neu:

- **Modellinput im Selection-Mode = ausschließlich `selected_text`.** Kein Betreff.
- Nach der Extraction darf **Python** sagen: `title` leer → `cleaned_subject`
  als Fallback-Titel.

Damit ist Selection semantisch wirklich „Selection only".

## 4. Contract v2 (Kandidat, NICHT final — Argumentreihenfolge erst proben)

```python
@needle.tool
def extract_event(when: str, title: str = "", location: str = "") -> str:
    """Use only when the message contains ONE concrete new calendar event that
    is already agreed, confirmed or announced.

    when:  copy the complete temporal phrase verbatim. Include all date,
           clock-time, end-time and timezone evidence present. Date-only is
           valid for all-day events. Never calculate or normalize.
    title: copy the event name only if explicitly present, else empty.
    location: copy the place or meeting medium only if explicitly present.
    """
```

**Negativantwort — faktisch entschieden (Problem B):**
`needle/model/finetune.py` rendert `answers` wörtlich; sowohl `[]` als auch
`[{"name":"no_event","arguments":{}}]` sind trainierbar. Needle's eigener
Generator-Template schreibt für Refusals **`"answers": []`** vor, und genau so
hat das v2-Kalender-FT Refusals gelernt. **Kanonische Negativantwort = `[]`.**
Konsequenz: Das `no_event`-Tool ist für das FT **nicht nötig**; Contract v2
nutzt nur `extract_event` und trainiert Negatives als leeren Call. (Base hatte
ohne `no_event` 100 % FP — deshalb existierte das Tool; das FT lernt `[]`
direkt, die frühere v2-Produktion belegt das.) Der Runtime-Handler behandelt
`[]` bereits als `status=none`.

**Argumentreihenfolge:** In einem kleinen Probe-Set gegen die neue
Realism-Kollektion testen (`title`-first vs `when`-first), **bevor** eingefroren
wird. Der alte Spike sagt title-first; das war auf dem alten, sauberen Set —
nicht blind übernehmen.

**Title-Last reduzieren:** `title` wird optional. Echter Fall: Subject „Re:
Projekt Alpha", Body „Dienstag 14 Uhr passt." → Needle liefert keinen
Titel-Span; Python fällt auf den **bereinigten Betreff** zurück. Cleaning rein
generisch (`Re:`, `AW:`, `Fwd:`, `WG:` strippen) — keine semantische
Titelgenerierung.

**Widerspruch auflösen (Problem A):** Die v1-Formulierung „date AND clock-time"
passt nicht zu Day-only/mehrtägig. v2 verlangt den **vollständigen temporalen
Span**; date-only ist für ganztägig gültig. Kein „Uhrzeit ist Pflicht".

## 5. Zeit & Zeitzonen

- Modell kopiert den Span; Python löst gegen `received_at` auf. Unverändert.
- **Zeitzonen gehören ins Dataset** („Thursday at 3pm CET / 9am ET", „14:00 BST",
  „10 AM Pacific"). Kann der Produktions-Compiler das noch nicht, gilt:
  Span bleibt erhalten, `needs_review = true`. Kein stiller Default.
- „verschieben/absagen" wird **nicht** als Create trainiert → `[]`
  (sonst Dubletten). Später eigener Workflow „Kalenderänderung erkannt".

## 6. XAI / Provenance (wird Dataset- und Eval-Kriterium)

Jedes Feld bekommt eine Provenance: `value`, `source_span`, `source_start`,
`source_end`, `transformation`. Da String-Args Verbatim-Spans sind, findet
Python den Offset per Substring-Suche. Neue Eval-Metrik
**`evidence_grounded_rate`**. Das FT labelt **niemals** normalisierte Datetimes,
nur Evidenz.

## 7. Dataset-Design v2

Ort: `ft/dataset_spec_tb.py` + `ft/build_dataset_tb.py`. Prinzipien wie gehabt
(keine Schema-Duplikation, familien-exklusive Splits, Held-out-Value-Pools,
harter Count-Check, SPARSE/evidenced-only, Verbatim-Grounding-Validator).

### Verteilung (Produktnähe, nicht Kalender-FT-Historie)
| Block | Anteil |
|---|---|
| Klare positive whole-mail | ~55–60 % |
| Selection positiv (selected_text only) | ~20–25 % |
| Kalender-nahe Negative / ambiguous (near-miss) | ~15–20 % |
| True off-topic | ~5 % |

### Stil = Discourse-Struktur, nicht Sprachstil
**Kein „Gemma-atomarer Stil"** (das war Kalender-FT-Historie, keine
E-Mail-Realität). Stattdessen variieren:
kurze Antwort · lange Mail · Reply · quoted thread · Signatur · Disclaimer ·
HTML→Text-Artefakte · Subject hilfreich · Subject irreführend · mehrere
Datumsangaben · **alte Daten im Quote**.

Sprachmix: ~75–80 % DE, ~15–20 % EN, ~5 % sehr kurz/telegrammartig.

### Near-miss-Negatives (das gefährliche Problem, nicht Off-topic)
„Passt dir der 12.10. um 14 Uhr?" · „Ich könnte Di oder Mi." · „Rückmeldung
bis 12.10." (Deadline) · „Das Meeting vom 12.10. war produktiv." (vergangen) ·
„Termin am Di fällt aus." (Cancel) · „Wir verschieben von Di auf Mi." (Update) ·
„… die Präsentation vom Workshop am 7.10." (Referenz). Diese machen den
Großteil der Negatives aus; Off-topic nur ~5 %, separat berichten.

### Threads > Multi-Event
Discourse-Recency ist ein eigenes Problem: „aktueller authored text > quoted
history". Gehört ins Dataset (Thread-Familien mit alten Terminen im Quote).
Wenn das Modell es nicht zuverlässig löst → Selection-Fallback. **Multi-Call
(Agenda mit Mo/Di/Fr) wird in V1 nicht trainiert** — Selection deckt es ab.

### Umfang / Reihenfolge (Problem D) — drei getrennte, handgeschriebene Sets
- `ft/contract_dev.jsonl` (35): **nur** für Contract-/Order-/Wording-Probe.
- `ft/realism_challenge.jsonl` (115): **eingefroren**, niemals zur Contract-
  Wahl benutzen — finales Base/FT-Gate.
- `ft/hard_challenge.jsonl` (20): hässliche Inbox-Härte (Reply-Ketten,
  Footer, Rechnungsdatum, irreführender Betreff, Disclaimor-„morgen",
  Meeting-Link, EN/DE, `gegen`/`ca.`/`zwischen`/`nach dem Mittagessen`).

Der Split verhindert den methodischen Leak „Set dient gleichzeitig zur
Contract-Wahl und als Zertifizierung". Erst wenn Taxonomie/Contract stehen,
~6–10 k synthetische Trainingsbeispiele **entlang dieser Distribution** bauen.
8 k vom falschen Problem < 2 k richtige.

Verteilung (150 Basis-Fälle, IDs r001–r150 partitioniert):
informelle Bestätigung 24 · formelle Bestätigung ohne ICS 20 ·
Event-/Workshop-Ankündigung 15 · kurze Zusage 15 · relative Zeit 10 ·
Zeitzonen 10 · langer Body + Signatur 10 · quoted thread 13 ·
Abstimmung/Slot-Angebot 13 · Absage/Verschiebung 10 · Deadline 5 ·
ohne Kalenderbezug 5. Selection-Varianten 55. Semantisch strenge Gold-Regel:
Frage-/Bestätigungssuche oder Slot-Angebot = **kein Create**, auch mit Datum.

### Manifest
`ft/manifest.json` mit Code-/Spec-Hash, Schema-Hash (1 Tool), seed,
file_sha256, counts, Klassen-/Sprach-/Discourse-Coverage, Gold-Konvention.

## 8. Training (erst nach Design Freeze)

- **Nur N2 Base + LoRA** (`cactus-needle==2.0.13`). **N3 wird gestrichen** —
  0.667 vs 0.10 ist kein knappes Rennen; N3 als Fallback wäre Research-
  Ausweitung ohne Evidenz. Ein sauberes N2-FT, dann Gate. Scheitert es, stoppen.
- Pfad: Modal (nach Freeze). Zuerst **Smoke-Test** (200 Zeilen, 1 Epoche), dann
  **maximal drei** N2-LoRA-Runs. A100-40GB, Cap ≤ $10 / ≤ 2 GPU-h.
- Tokens via `.env` (`MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`), intern per
  `load_dotenv()`, nie geloggt.
- Fallback lokale RTX nur falls Modal-Setup scheitert.

## 9. Gate (vor Training fixiert; auf Realism-Set + generiertem Held-out)

| Kriterium | Base (alt) | Ziel |
|---|---|---|
| `supported_final_event_ok` (ohne TZ/fuzzy) | 0.667 | ≥ 0.85 |
| `false_positive_rate` (Near-miss separat) | 0.143 | ≤ 0.05 |
| `false_positive_by_category` (tentative/cancel/deadline/past) | – | je ≤ 0.05 |
| selection `final_event_ok` (n ≥ 50) | 0.40 | ≥ 0.90 |
| `supported_approval_ready` | 0.633 | ≥ 0.80 |
| `review_routing_ok` (TZ/fuzzy → Review) | – | ≥ 0.90 |
| `evidence_grounded_rate` | – | neu, ≥ 0.95 |
| `event_detection_recall` | 1.00 | keine Regression |

Timezones/fuzzy werden **nicht** in `supported_final_event_ok` bestraft, sondern
über `review_routing_ok` bewertet (bewusstes Human-Review statt Rate-Datetime).
Near-Miss-FP wird **pro Klasse** berichtet, nicht nur global.

- **GO**: Gate hält → Add-on-Prototyp planen.
- **PROMISING**: whole-mail 0.65–0.85 **und** selection ≥ 0.90 → selection-first V1.
- **WEAK**: selection < 0.85 → kein weiteres FT; Contract/Decoding neu denken.

## 10. Nicht-Ziele
Kein Multi-Call-FT, kein Reschedule/Cancel, kein Gemma, keine Participant-Logik
im Modell, kein echtes Thunderbird/SMTP/iTIP. Confidence bleibt `None` (FT) —
Approval nutzt `needs_review`/`status`.

## 11. Reihenfolge (verbindlich, kein Training vor Schritt 9)

1. Produkt-Taxonomie festschreiben (§1) — erledigt in diesem Dokument.
2. Selection-Semantik ändern: `selected_text` only; Subject nur Python-Fallback.
3. Contract-Doku korrigieren: date-only zulässig; `title` optional.
4. Negativ-Gold eindeutig: `answers: []` (entschieden, §4).
5. Drei handgeschriebene Sets erstellen: `contract_dev` (35),
   `realism_challenge` (115, eingefroren), `hard_challenge` (20).
6. **N2 Base auf allen drei Sets** laufen lassen (Baseline neu einfrieren).
7. Argumentreihenfolge-Probe **nur auf `contract_dev`** (fair: gleiche
   Requiredness); danach Contract einfrieren. Das Challenge-Set bleibt unberührt.
8. Erst dann Generator auf die Distribution ausrichten, 6–10 k bauen.
9. N2 Smoke + max. drei N2-LoRA-Runs (Modal).
10. `eval.py --weights` + `gate_tb.py`; Bericht GO/PROMISING/WEAK.

## 12. Offene Entscheidungen für den Nutzer
1. Freigabe der Taxonomie (§1) und des Contract-v2-Kandidaten (§4).
2. Freigabe, das eingefrorene Spike-Verhalten zu ändern (Selection-only,
   `no_event` raus, `title` optional) — die alten 60er-Base-Zahlen gelten dann
   nicht mehr; wir frieren neue Baselines auf dem Realism-Set ein.
3. Go für die handgeschriebenen Sets dev/challenge/hard (Schritt 5).
4. Modal/Budget erst nach Schritt 8.
