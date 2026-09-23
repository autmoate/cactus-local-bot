# Decomposer-Contract (Hypothese) — N3 als Zerleger, nicht als Arg-Extraktor

**Status: Design, kein Training, kein Produktivpfad.** Entstanden aus dem Befund,
dass N3 stark bei Tool-Wahl/Multi-Call (E5: multi 0.90, tool_ok 1.000), aber
schwächer bei `date`/`until` (0.89/0.86) ist. Also: N3 soll **nicht** die finalen
Kalenderargumente erzeugen — nur die Zerlegung.

## Vertrag (Entwurf, freeze-Kandidat)

Jedes Goal wird in **atomare Schritte** zerlegt, jeweils mit **verbatim-Text** aus
der Anfrage (kein Paraphrasieren, keine Datums-Normalisierung, keine IDs):

```python
read_step(text: str)     # ein atomarer Lese-Wunsch, text = Substring der Anfrage
write_step(text: str)    # ein atomarer Schreib-Wunsch, text = Substring der Anfrage
```

Regeln:
1. **Ein Call pro Intent**, Reihenfolge wie im Text.
2. `text` ist ein **verbatim Substring** der User-Anfrage (Grounding wie bisher).
3. Kein `text` → kein Call (Off-Topic/kein Kalenderbezug → `[]`).
4. Keine Kalenderargumente (date/time/title/…) im Output — nur Schritt-Typ + Text.
5. Mehrere unabhängige Intents → mehrere Calls; erkannte Abhängigkeit darf als
   Reihenfolge abgebildet werden, aber die Auflösung macht Python/Gemma.

Beispiel:

```
User: "Zeig mir Donnerstag meine Termine, lösch den Zahnarzt und trag Freitag 15 Uhr Sport ein"
→ [read_step("Zeig mir Donnerstag meine Termine"),
   write_step("lösch den Zahnarzt"),
   write_step("trag Freitag 15 Uhr Sport ein")]
```

Weiterverarbeitung:
```
read_step(text)  → Python-Snapshot / Gemma (NL-Antwort) / Availability-Kernel
write_step(text) → N2-FT (Atomar-Spezialist, 0.977) → Python verify/execute
```

## Warum das reizvoll ist
- N3 muss die **schwierigen finalen Args nicht mehr** liefern (date/until).
- N2-FT bekommt **atomare Instruktionen** — genau seine Stärke (0.977).
- Der Text bleibt **verbatim** → Grounding/Span-Kopieren ist trivial und prüfbar.
- Python bleibt Wahrheit; keine Mutation durch das Sprachmodell.

## Dataset-Design (v5, Entwurf)
Quelle: v4-Generator. Nötig ist, dass der Multi-Generator die **atomaren
Teil-Queries** mit ausgibt (heute wird nur die kombinierte Query + Gold-Calls
gespeichert). Dann:
- `atomic` (v2/v4): query → **1 Schritt** (`read_step`/`write_step` je nach Tool).
- `multi` (v3/v4): kombinierte Query → **N Schritte**, jeder `text` = die jeweilige
  atomare Teil-Query (verbatim im kombinierten Text enthalten).
- `negatives`: → `[]`.
- Split/Validator wie v2/v3 (Familien-exklusiv, Grounding-Check: jedem `text`
  muss ein Substring der Query sein; Abdeckung: die Vereinigung der Schritte muss
  alle Gold-Calls abdecken).

Offene Designpunkte:
- Sollen Schritt-Texte **exakt** die atomare Teil-Query sein (dann ist die Aufgabe
  „Segmentierung“) oder eine **frei formulierte** Kurzfassung (dann „Paraphrase“)?
  → Exakt ist deterministisch prüfbar; Paraphrase wäre schwerer zu validieren.
- Umgang mit **Abhängigkeiten** (`find_slot → create`): als 2 Schritte in
  Reihenfolge ausgeben; die Auflösung bleibt Python (Kontext-/Kernwissen).
- Braucht es eine **dritte Kategorie** (`ask`), wenn der Intent unklar ist? Erst
  messen, nicht vorab einbauen.

## Wie es evaluiert würde (nach Freigabe)
1. **Zerlegungs-Treffer:** Schritt-Anzahl, Schritt-Typen, Reihenfolge, Text-
   Grounding (Substring), Abdeckung der Gold-Calls (Recall).
2. **Downstream:** `write_step`-Texte durch N2-FT → atomare args_ok/exact
   (Erwartung ≈ N2-FT-Niveau, da atomar).
3. **E2E:** dieselben P1/P2-Business-Metriken (`arch_bench.py`) — Ziel:
   `autonomous_ok` steigt, `gemma_rate` sinkt, `wrong_writes` bleibt 0.

## Risiken / offene Fragen
- Zusätzlicher Needle-Pass pro Schreibwunsch (N3+decomp + N2) — auf CPU billiger
  als ein Gemma-Turn, aber nicht gratis.
- Der Decomposer könnte bei Listen segmentieren, aber die **Reihenfolge/Typen**
  verwechseln; das ist direkt messbar.
- Die Silent-Omission-Klasse (fehlende Schritte) bleibt bestehen — auch der
  Decomposer kann Intents weglassen; sie ist zentral zu messen.
