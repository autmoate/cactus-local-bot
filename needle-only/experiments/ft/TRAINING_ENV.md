# Trainingsumgebung: WSL-Grenzen, Thermik-Befund, Modal-Fallback

Kurzfassung der harten Erkenntnisse aus den Needle-3-Läufen auf der lokalen
RTX-3090-Box (WSL2) — und warum wir die FT-Läufe auf Modal auslagern.

## Was gemessen wurde

| Fakt | Wert | Konsequenz |
|---|---|---|
| WSL-VRAM-Limit pro GPU (JAX `bytes_limit`) | **19,3 GB** (von 24 GB) | Kein Config-Fehler: batch 6 nutzt 8,5 GB, batch 8 (>19,3 GB) OOMt. |
| `fan.speed`, `Memory Current Temp` | **N/A in WSL** | GDDR6X-Junction (der kritische Sensor) ist unsichtbar. |
| Temperatur/`clocks.sm` in `nvidia-smi` | **gespiegelt** (beide GPUs identisch) | „GPU 1 im Idle 79 °C" ist ein Anzeige-Artefakt (Task Manager fasst beide als einen Adapter zusammen). |
| Kern-Temp unter Volllast | 70–78 °C bei ~348 W (Limit 350 W) | Unter Slowdown (95 °C)/Shutdown (98 °C) — kein Hinweis auf Schaden. |
| Abbruchzeitpunkt Needle 3 | **~25–30 min** nach Start, unabhängig von Batch/Steps | Zeit- und nicht Schritt-getrieben → wärme-/strommarginal. |
| Needle 2 | 2,9-h-Läufe stabil | Kleineres Modell (45M) → weniger Leistung → mehr Marge. |

**Fehlerarten:** `CUDA_ERROR_LAUNCH_FAILED`, `_ILLEGAL_ADDRESS`,
`_ILLEGAL_INSTRUCTION`, teils vorausgehend `loss nan`.

**Hypothesen (nicht belegbar in WSL):** Speicher-Junction zu heiß (GDDR6X
typisch +15–25 °C über Kern), Netzteil-/Power-Marge bei 350 W Dauerlast,
WSL-Treiber-/TDR-Reset. Alle drei sind strom-/wärmemarginal.

## Lokale Sicherheiten, die wir nutzen

- **Nur eine GPU gleichzeitig**, Karten abkühlen lassen.
- **Kurze Runs** (< 25 min) passten: `n3-v2-s` (3500 Zeilen, batch 6, 583 Steps)
  lief in **14,1 min** durch → `n3-v2-s.cact` (63,4 MB).
- **`train_guarded.sh`**: Cooldown-/Hygiene-Runner — prüft vor jedem Versuch,
  dass keine Alt-Prozesse laufen (kein Stapeln hängender CUDA-Kontexte), macht
  einen Matmul-Gesundheitscheck und retryt begrenzt.
- **Empfehlung an den Host (Windows, Admin):** `nvidia-smi -pl 280` (senkt
  Abwärme und Netzteil-Spitzen; min. 100 W), Clocks begrenzen
  (`nvidia-smi -lgc 300,1700`), echte Sensoren mit HWiNFO64/GPU-Z lesen
  (GPU Memory Junction / Hot Spot), Netzteil-Leistung prüfen. Wirkt nach
  `wsl --shutdown`.

## Modal (Cloud-FT) — Setup

Modal läuft in Rechenzentren: stabile Kühlung/Strom, große GPUs, kein
19,3-GB-Cap, per-Sekunde-Abrechnung, kein Idle-Vorhalt.

```bash
cd needle-only
uv sync --extra modal --no-dev          # modal-Client (kein pip)
uv run --extra modal modal setup        # Browser-Login (einmalig)
uv run --extra modal modal run experiments/ft/modal_train.py \
    --runs v2,v3 --epochs 1 --batch-size 16 --gpu A100-40GB
```

`modal_train.py` lädt die **Rohdatensätze** hoch (11,5 MB) und injiziert
`tools.json` + `SYSTEM_FACTS` **im Container** (spart 65 MB Upload). Es startet
v2 und v3 **parallel** (je eigener Container/GPU) und legt Adapter, `.cact`,
Log und Run-Manifest im Volume `cactus-ft-artifacts` ab.

Artefakte holen:
```bash
uv run --extra modal modal volume get cactus-ft-artifacts ./ft-models
```

### Kartenwahl (Modal-Preise, USD/h)
| GPU | $/h | Eignung |
|---|---|---|
| L4 | 0,80 | günstigste Option, 24 GB (langsamer) |
| A10 | 1,10 | 24 GB |
| **A100-40GB** | **2,10** | **Empfehlung**: ~5–8× 3090, stabil, 40 GB |
| A100-80GB | 2,50 | mehr VRAM für batch 32 |
| L40S | 1,95 | 48 GB, modern |
| H100 | 3,95 | Overkill für 121M |

**Empfehlung: A100-40GB.** Unsere Jobs sind klein (121M, LoRA): v2+v3 je
~1 Epoche ≪ 1 GPU-Stunde → **Kosten im einstelligen Dollar-Bereich**, mit den
$30 Gratis-Compute des Starter-Plans faktisch kostenlos. Ladder für den Pi
später per `--layers 8|12|16` (dasselbe `.cact`-Format, gleiche Engine-Version).

## Confidence-Hinweis (für die Eskalations-Idee)

Needle 3 liefert **kalibrierte** Confidence nur für die **Base**. Fine-Tunes
(N2 und N3) tragen **keinen** mittrainierten Confidence-Head → `confidence: None`
(Warnung: „platform fine-tunes keep the head"). Ein Confidence-Gate < 0.3 ist
damit **nur mit Base** oder mit auf der Cactus Platform trainierten Modellen
möglich — für unsere FT-Modelle braucht es ein anderes Sicherheitssignal
(z. B. deterministische Validierung/Leer-Call).

## Ladder-Export (Pi-Benchmark, vorbereitet)

`needle build --layers N` (2..20) existiert in Needle 3. Nach dem FT-Export
lassen sich 8/12/16/20 Layer als getrennte `.cact` bauen und auf dem Pi gegen
Accuracy/Latenz/RAM messen.
