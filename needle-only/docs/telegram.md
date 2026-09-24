# Kalender-Pin 📌 — Telegram V1 (Raspberry Pi)

Lokaler Telegram-Kalenderbot. Der Produktionspfad ist **eingefroren**:

```
Telegram → Needle 2 (N2-FT seed44) → maximal EINE atomare Aktion
        → Python resolve / verify / scope / permissions
        → READ  sofort   ·   WRITE  Preview → Confirm → revalidate → SQLite
```

Kein Training, kein Gemma, kein Needle 3, kein Multi-Intent-Agenting im
Produktionsbetrieb. Gemma/N3 bleiben ausschließlich Research
(`needle-only/experiments/ft/`).

## Bedienung

- **Freie natürliche Sprache** ist das Hauptinterface.
- **Eine Kalenderaktion pro Nachricht.** Mehrere Aktionen werden deterministisch
  abgelehnt: _„Bitte sende Änderungen einzeln."_
- **Reads** (`list`, `find_slot`) laufen sofort.
- **Änderungen** (`create`, `move`, `delete`) erscheinen als Preview mit
  **[✅ Bestätigen] [✖ Abbrechen]**; erst der Tap mutiert die DB. Vorschläge
  verfallen nach 5 Minuten.
- **/today · /week** rendern direkt aus Python/SQLite (keine Modellinferenz).
- **/cancel** verwirft offene Vorschläge · **/debug** ist owner-only.

### Private Chats

Jede erlaubte Textnachricht wird verarbeitet.

```
Trag morgen 14 Uhr Zahnarzt ein.
Verschieb Zahnarzt auf Freitag 15 Uhr.
Lösch Zahnarzt morgen.
Was habe ich Freitag?
Wann können Lisa und ich nächste Woche 60 Minuten?
```

### Gruppen

Der Bot reagiert **nur bei direkter Ansprache**:

- `@botusername <Text>` (Mention über Telegram-Message-Entities, nicht per Regex)
- Reply auf eine Bot-Nachricht
- `/befehl@botusername`

Normale Gruppennachrichten werden **vor jeder Inferenz** ignoriert.

## Sicherheit

- **User-ID und Chat-ID sind getrennt.** In Gruppen müssen **beide** erlaubt
  sein (erlaubter Gruppenchat ≠ alle Mitglieder schreibberechtigt).
- Autorisierung via `TELEGRAM_OWNER_USER_ID`, `TELEGRAM_ALLOWED_USER_IDS`,
  `TELEGRAM_ALLOWED_CHAT_IDS` (Legacy `TELEGRAM_OWNER_CHAT_ID` bleibt
  rückwärtskompatibel, wenn keine User-ID gesetzt ist).
- First-Start-Pairing ist **default aus** und nur per
  `TELEGRAM_ALLOW_FIRST_START_PAIRING=1` aktiv (nie aus einer Gruppe).
- Unberechtigte Nutzer lösen **keine** Inferenz, DB-Abfrage oder Mutation aus.
- **Private Event-Titel bleiben in Gruppen privat** — sie erscheinen nur als
  „belegt" (Busy-Lane), niemals mit Titel. Geteilte Gruppentermine zeigen Titel.
- Cross-Kalender-Mutationen sind gesperrt: ein Delete im Gruppenchat kann keinen
  privaten Termin löschen.

## Gemeinsame Verfügbarkeit

`calendar_find_slot` berücksichtigt die **persönlichen Kalender** der genannten
Personen plus geteilte Gruppentermine — berechnet mit dem **exakten
Intervall-Solver** (halboffene Intervalle). Der 15-Minuten-Bitset-Kernel
(`experiments/ft/availability_kernel.py`) ist bewusst **nur** Rendering/Projektion,
nicht der Solver (Off-grid-Korrektheit).

## Privacy (korrekt formuliert)

Kalenderdaten und Modellinferenz laufen **lokal auf dem Pi**. Telegram ist der
**externe Nachrichtentransport** — die Kommunikation mit Telegram ist nicht
lokal. Es werden keine Kalenderdaten an Drittmodelle gesendet.

## Datenmodell & Migration

Eine SQLite-DB, normalisiert: `people` · `calendars` · `calendar_members` ·
`events` (+ `calendar_id`, `created_by_person_id`) · `event_participants`
(person_id) · `action_proposals` (Token, TTL, Ziel-Fingerprint).

Vor jeder Schemaänderung wird automatisch nach `data/backups/calendar-YYYYMMDD.db`
gesichert (SQLite-Backup-API, keine blinde Dateikopie). Migration ist idempotent
(`PRAGMA user_version`) und erhält alle Events (Anzahl/Titel/Zeiten/Teilnehmer).
Der Bot legt zusätzlich täglich ein Backup an; Backup-Fehler crashen nie.

## Betrieb

```bash
cd needle-only
uv sync --extra telegram
NEEDLE_WEIGHTS=experiments/ft/models/sa-r16-lr1e-4-e8-seed44.cact \
  uv run local-calendar-telegram --mode needle
```

systemd: `deploy/kalender-pin.service` (WorkingDirectory, `EnvironmentFile=.env`,
`Restart=on-failure`). Secrets stehen **nur** in der Environment-Datei, nie im
Unit-File und nie in Git.

## V1-Limitationen (bewusst)

- **Eine Aktion pro Nachricht** — kein Multi-Intent, keine dependent chains in
  einem Turn.
- **Kein Pending-Dialog** bei Ambiguität; der Nutzer sendet eine neue präzisere
  Nachricht.
- **Kein Gemma/N3** im Produktionspfad (Core/Lite-Profil).
- Der Telegram-Testbot bleibt `@needle2orga_bot`; der Name `Kalender-Pin 📌`
  ist user-facing. Eine spätere Umbenennung zu `@kalender_pin_bot` erfordert
  **keine Codeänderung** (Username kommt aus `getMe`).
- N2-FT ist Needle-2-gebunden (`cactus-needle==2.0.13`).
