# Multi-Intent Probe (Phase C) — Reproduktion, KEINE Fixes

Ziel: reale Termin-Nachrichten durch den **Produktions-`Agent`** im `needle`-Mode
schicken und pro Fall Needle-Calls, Ausführung und finalen DB-Zustand festhalten.
Grundlage: `needle-only/experiments/multi_intent_probe.py`.
Hybrid/Gemma konnte hier **nicht** gemessen werden (kein `cactus serve` auf der RTX-Box).

## Ergebnisse (FT = Release-Kandidat seed 44)

| # | Input | Base needle2 | FT seed 44 | Bewertung |
|---|---|---|---|---|
| 1 | „Trag morgen Zahnarzt 10 Uhr und Meeting 14 Uhr ein." | 2 Calls, aber fehlerhaftes `end_time` → nur 1 Event | **2 Calls, 2 Events korrekt** | FT besser |
| 2 | Bullet-Liste „Mach am Freitag:\n- 9 Uhr Zahnarzt\n- 12 Uhr Mittagessen mit Lisa\n- 16 Uhr TÜV" | **3 Calls, 3 Events korrekt** | **1 Call** (9–12 als `end_time`), 2 Items fehlen | **Regression** |
| 3 | „Montag Teammeeting 9, Dienstag Arzt 11, Freitag Urlaub" | 3 Calls (Trennung ok, Daten falsch) | 1 Call, Parse-Fehler („9. Dienstag") → 0 Events | **Regression** |
| 4 | „Verschieb Zahnarzt auf Freitag und lösch danach Meeting." (Einträge existieren) | 2 Calls | **2 Calls in Reihenfolge** (move → delete), beide ausgeführt | ok |

Fall 4 im leeren Fixture scheitert nur, weil die Ziel-Einträge nicht existieren
(produktionsfremd) — mit geseedeten Einträgen läuft er korrekt.

## Warum
- Das FT-Dataset trainiert **ausschließlich atomare Requests** (README/`dataset_spec`:
  „NICHT trainiert: Multi-Step-Planning"). LoRA hat die native Multi-Call-Fähigkeit
  von Needle 2 für **strukturierte Listen** (Bullets, komma-separierte Mehrtages-Angaben)
  zu Gunsten einzelner sparse Calls verschoben.
- Für „A **und** B" (Fall 1) bleibt Multi-Call erhalten; für einzeilige Listen kollabiert
  das Modell mehrere Angaben in einen Call (Zeitspanne/`end_time`).
- Base behält die vortrainierte Multi-Call-Robustheit, ist aber bei den Argumenten
  unzuverlässig (siehe `RESULTS.md`).

## Einordnung / Deployment
- `telegram.py` ist standardmäßig **`--mode hybrid`**; es gibt **kein** systemd-/Docker-Unit
  im Repo → der reale Start ist manuell. Der zuletzt laufende Bot wurde mit
  `--mode needle` gestartet — damit geht der komplette Text **direkt an Needle**,
  Gemma zerlegt nichts. Das erklärt „Terminlisten gingen nicht an Gemma".
- Ohne laufendes `cactus serve` fällt auch `hybrid` in den dokumentierten Fallback
  „Gemma nicht erreichbar — Needle direkt" → identisches Verhalten wie `needle`.

## Akzeptanz (laut Plan)
Für Multi-Intent (Listen/mehrere Tage) gilt weiterhin: **entweder** zerlegt Gemma im
Hybrid-Pfad vor Needle, **oder** Needle 3 liefert mehrere Calls korrekt und der Pfad
akzeptiert sie. Bis dahin: FT-Modell nicht allein im `needle`-Mode für Listen einsetzen.

**Keine Code-Änderung in Phase C vorgenommen** (Plan: erst Benchmark von Needle 3).
