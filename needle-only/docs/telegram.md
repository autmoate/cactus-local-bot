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
- **/today · /day [Datum] · /week [Datum]** rendern direkt aus Python/SQLite
  (keine Modellinferenz). `/day 29.9.` zeigt genau diesen Tag, `/week 29.9.`
  die Woche, die ihn enthält; `/week next` die nächste Woche.
- **/status** (owner-only) zeigt Build-SHA, Modus, Modell-Tag, Schema, Eventzahl,
  Uptime — ohne Secrets und ohne vollständigen Pfad.
- **/cancel** verwirft offene Vorschläge · **/debug** ist owner-only.

### Overlaps sind Warnungen, keine Blocker

Ein klassischer Kalender darf überlappende Einträge haben. Überschneidungen
erscheinen im Preview als `⚠️ …`, der Termin kann per **✅ Trotzdem eintragen**
bestätigt werden. In Gruppen wird nie ein fremder privater Konflikttitel genannt,
sondern „Eine teilnehmende Person ist zu dieser Zeit bereits belegt."

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

## Domain-Semantik (Round 2)

- **Identität ist `person_id`.** Der Owner wird idempotent mit dem migrierten
  Legacy-„Ich" versöhnt (in-place binden oder zusammenführen, Backup vorher).
  Symbole wie `""`/`"Ich"`/der eigene Anzeigename lösen auf den Actor auf — nie
  auf eine separate „Ich"-Person.
- **Absence blockiert keine manuellen Termine.** Urlaub 24.–27. + Frühstück
  25. 08:00 ist gültig. Absence zählt aber **weiterhin als busy** für
  `find_slot`/Availability (zwei getrennte Konzepte).
- **Ein Read = eine Wahrheit:** `calendar_list` führt genau EINE gescopte Query
  aus; Text und PNG entstehen aus demselben `ReadResult`
  (`resolved.first_day/last_day/person_ids/calendar_ids` + `data`).
- **Zeitfenster deterministisch:** explizites Datum/Range im Text > „nächste/
  diese Woche" > Wochentag > heute/morgen > Modellargs > Default (7 Tage).
  `nächste Woche` = Mo–So.
- **Skalierung:** 1 Tag → DayView, 2–7 Tage → Range/WeekView, > 7 Tage → nur Text.
- **Gescopte Mutation:** move/delete suchen ausschließlich im aktuellen Kalender
  (privat = persönlich, Gruppe = Gruppenkalender). Gleicher Titel in einem
  anderen Kalender erzeugt keine Ambiguität und wird nie mutiert.
- **Darstellungsschichten:** All-Day/Absence als Header-Banner, timed Termine als
  pro Tag geklippte Segmente (halboffen). „Büro 29.09. 09–16" erscheint nur am
  Dienstag, nie als Wochenband. Private Events sind **nie** „shared".

## Gemeinsame Verfügbarkeit

`calendar_find_slot` berücksichtigt die **persönlichen Kalender** der genannten
Personen plus geteilte Gruppentermine — berechnet mit dem **exakten
Intervall-Solver** (halboffene Intervalle), ID-basiert und gescopto
(`find_free_slots_for_people`, kein globaler Namens-Lookup). Der 15-Minuten-
Bitset-Kernel (`experiments/ft/availability_kernel.py`) ist bewusst **nur**
Rendering/Projektion, nicht der Solver (Off-grid-Korrektheit).
Das Availability-Widget rendert `resolved.first_day`, **nie** `now()` — eine
Abfrage für den 29.09. zeigt nie den 24.09. Schlägt der Read fehl, wird **kein**
Widget gerendert (nur ein deutscher Fehlertext, optional ein datumsbasierter
Day-Button ohne zweiten Modellcall).

## Privacy (korrekt formuliert)

Kalenderdaten und Modellinferenz laufen **lokal auf dem Pi**. Telegram ist der
**externe Nachrichtentransport** — die Kommunikation mit Telegram ist nicht
lokal. Es werden keine Kalenderdaten an Drittmodelle gesendet.

## Datenmodell & Migration

Eine SQLite-DB, normalisiert: `people` · `calendars` · `calendar_members` ·
`events` (+ `calendar_id`, `created_by_person_id`) · `event_participants`
(person_id) · `action_proposals` (Token, TTL, Ziel-Fingerprint).

**SQL/exakte Intervalle = Wahrheit.** Views/Telegram sind eine deterministische
Projektion (X=Tage, Y=Zeit, Z=Personen) — keine Event-Tabelle pro User, keine DB
pro User. Direktes `sqlite3` bleibt in Ordnung (kein ORM).

Vor jeder Schemaänderung wird automatisch nach `data/backups/calendar-YYYYMMDD.db`
gesichert (SQLite-Backup-API, keine blinde Dateikopie). Migration ist idempotent
(`PRAGMA user_version`) und erhält alle Events (Anzahl/Titel/Zeiten/Teilnehmer).
Der Bot legt zusätzlich täglich ein Backup an; Backup-Fehler crashen nie.

Audit ohne private Inhalte:

```bash
python scripts/audit_calendar_state.py --db data/calendar.db
python scripts/audit_calendar_state.py --fix-owner <telegram_user_id> --name Oll
```

`--fix-owner` sichert vorher und versöhnt das Legacy-„Ich" mit dem autorisierten
Owner (ID kommt aus der CLI, nie aus `.env`). Es werden nur Counts/IDs/Schema
ausgegeben, niemals private Event-Titel.

Render-Gallery (dev-only, synthetische Fixtures, gitignored):

```bash
python scripts/render_regression_gallery.py   # artifacts/render-regression/*.png
```

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
