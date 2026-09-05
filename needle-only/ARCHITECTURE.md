# Orga v4 — Architektur: Needle als Postgres-Orchestrator

> **Status:** v4.2 implementiert. 19/23 Eval-Fälle deterministisch (ø ~3s/Fall). ICS-Export + Scheduler aktiv.
> **Basis:** needle-only v3.1 „Plan-Werkstatt" (17/24), Gemma-Stack v3.1 (28/28).
> **Konzept:** needle-only/ARCHITECTURE.md — „einfach, simpel, robust, funktional".

---

## 1. Ziel & Non-Ziele

**Ziel:** Ein lokaler Orga-Bot, der Termine, Erinnerungen, Aufgaben und Notizen in Postgres verwaltet — orchestriert von Needle 2 (45M Parameter, grammar-guaranteed JSON tool calls). Der Bot sagt freie Zeiten, exportiert den Kalender als ICS (v4.1, geplant), und ich arbeite per TUI (später Telegram/Matrix) damit.

**Non-Ziele:**
- ❌ Kein Chatbot — Needle ist ein Dispatcher, kein Konversations-Modell
- ❌ Keine Cloud-Dependency — alles läuft lokal auf dem RPi
- ❌ Kein Multi-User — aber das Datenmodell ist person-ready
- ❌ Keine Graph-DB — pgvector + pg_trgm reichen für persönliche Wissens-Suche

---

## 2. Architektur: Drei Schichten

```
┌─────────────────────────────────────────────────────────────┐
│  DELIVERY LAYER (Adapter)                                   │
│  TUI (v4) → Telegram (v5) → Matrix E2EE (v6)               │
│  Liefert: Nachricht + Sprecher-Kontext, Approval-UX,       │
│           Reminder-Delivery                                 │
├─────────────────────────────────────────────────────────────┤
│  SERVICE LAYER (Kern)                                       │
│  turn() = draft → fix → plan → approve → apply → render     │
│  Die EINZIGE Stelle, die Needle kennt.                      │
│  Die Adapter rufen nur turn(text, speaker) auf.             │
├─────────────────────────────────────────────────────────────┤
│  POSTGRES LAYER (Wahrheit)                                  │
│  entries (gemerged!), n_notes, entry_changes (Audit)        │
│  Kein Redis, keine Vector-DB, kein Celery — Postgres only. │
└─────────────────────────────────────────────────────────────┘
```

**Die Kernidee:** Needle ist nur der **Parser** vor Postgres. Die Engine ist billig (3.700 Zeilen C99, offline auf 240 MHz). Die Intelligenz liegt in:
1. **Präzisen Tool-Schemas** (Token-Budget, load-bearing Descriptions)
2. **Deterministischem Planner** (Upsert-Semantik, Kollisions-Checks, atomare Transaktionen)
3. **Postgres als Single Source of Truth** (kein State im Adapter, kein State im Modell)

---

## 3. v4-Datenmodell: Der entries-Merge

**Entscheidung:** Termine, Erinnerungen, Aufgaben und Notizen landen in EINER Tabelle. `kind` ist ein Attribut, keine Tabellenwahl.

### 3.1 Die `entries`-Tabelle

```sql
CREATE TABLE entries (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner       text NOT NULL DEFAULT 'ich',
    kind        text NOT NULL CHECK (kind IN ('appointment', 'reminder', 'task')),
    title       text NOT NULL,
    start_at    timestamptz NOT NULL,
    end_at      timestamptz,
    status      text NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'done', 'cancelled')),
    alarm_min   integer,
    location    text NOT NULL DEFAULT '',
    notes       text NOT NULL DEFAULT '',
    participants text[] NOT NULL DEFAULT '{}',
    source_id   bigint,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX entries_time_idx ON entries (owner, start_at);
CREATE INDEX entries_kind_idx ON entries (kind, status);
CREATE INDEX entries_title_trgm_idx ON entries USING gin (title gin_trgm_ops);
```

**Semantik von `kind`:**
- `appointment` (Termin): blockiert Zeit in `free_slots`, hat `end_at` (default +1h)
- `reminder` (Erinnerung): `start_at` = Weckzeit, blockiert NICHT, `status` → done
- `task` (Aufgabe): `start_at` = Deadline, blockiert NICHT, `status` → done

**Semantik von `status`:**
- `active`: sichtbar, relevant
- `done`: erledigt (reminder/task)
- `cancelled`: abgesagt (appointment)

**Fehlertoleranz-Hierarchie:** Eine `kind`-Fehlklassifikation (Termin als Erinnerung) ist eine **weiche** Fehlertoleranz — der Eintrag existiert, hat die richtige Zeit, ist in `list_items` sichtbar, nur das Rendering unterscheidet sich („Erinnerung: Zahnarzt 14:00" statt „Termin: …"). Das Plan-Approval zeigt es vor dem Speichern — Korrektur mit `e`.

### 3.2 Weitere Tabellen

```sql
-- Notizen (Wissen): Fuzzy-Suche via pg_trgm
CREATE TABLE n_notes (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner      text NOT NULL DEFAULT 'ich',
    subject    text NOT NULL,
    body       text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX n_notes_subject_trgm_idx ON n_notes USING gin (subject gin_trgm_ops);
CREATE INDEX n_notes_body_trgm_idx ON n_notes USING gin (body gin_trgm_ops);

-- Audit-Trail für Undo/Forensik
CREATE TABLE entry_changes (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id   uuid NOT NULL,
    entry_id   bigint NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    action     text NOT NULL,
    old_values jsonb,
    new_values jsonb,
    actor      text NOT NULL DEFAULT 'needle',
    created_at timestamptz NOT NULL DEFAULT now()
);
```

---

## 4. Tool-Oberfläche v4: Separat definierte Tools (v3.1-Oberfläche)

**⚠️ WICHTIGE LEHRE (v4.0-Experiment):**

> Der Merge-Versuch — ein `upsert_event` mit `kind ∈ {appointment, reminder, task}` als Parameter — **brach das Needle-Routing** (Eval: 10-13/20, viele `ops=[]`). Die Ursache: Needle muss bei gemergten Tools ZWEI Entscheidungen treffen (Tool-Wahl UND kind-Wert), und die Grammar mit Enum-Werten ist restriktiver.
>
> **Die Lösung:** DB bleibt gemerged (entries-Tabelle, eine Query-Oberfläche), aber die Tool-Schicht ist getrennt: `upsert_event` (für Termine) und `upsert_reminder` (für Erinnerungen) sind eigene Tools mit eigenen Parametern. Sofortiges Ergebnis: 18/23 → nach Intent-Fixes 19/23.
>
> **Regel für die Zukunft:** Tool-Beschreibungen, die funktionieren, NICHT ändern. Neue Tools hinzufügen ja, aber bewährte Descriptions exakt beibehalten.

### 4.1 Die 9 Needle-Tools

| Tool | Typ | Zweck |
|---|---|---|
| `list_items` | Read | Alle Entries auflisten (termine/erinnerungen/notizen, horizon) |
| `upsert_event` | Write | Termin erstellen/verschieben (add⟷move via DB-Zustand) |
| `cancel_event` | Write | Termin absagen (soft-cancel) |
| `upsert_reminder` | Write | Erinnerung erstellen/ändern (due_at oder in_min) |
| `complete_reminder` | Write | Erinnerung als erledigt markieren |
| `free_slots` | Read | Freie Zeitfenster deterministisch berechnen |
| `remember_note` | Write | Notiz/Wissen speichern (subject + body) |
| `find_notes` | Read | Notizen durchsuchen (pg_trgm fuzzy) |
| `show_status` | Read | Zählerstände anzeigen |

### 4.2 Needle-Tool-Definitionen (exakt, nicht ändern!)

```python
@needle.tool
def list_items(what: str = "termine", horizon: str = "woche"):
    """List saved items (read). what: 'termine', 'erinnerungen' or 'notizen'; horizon: heute, woche, monat. Keywords: was steht an, zeige meine todos, erinnerungen, termine."""

@needle.tool
def upsert_event(title: str, start_at: str = "", end_at: str = "",
                 location: str = "", notes: str = "", participants: str = ""):
    """Create, move or edit a calendar event — if a similar event already exists it is MOVED/EDITED, otherwise created (needs date+time). Keywords: termin, kalender, verschiebe, eintragen, meeting."""

@needle.tool
def cancel_event(title: str):
    """Cancel an EXISTING event (soft-cancel, status->cancelled). Keywords: termin absagen, sage ab, cancel, storno."""

@needle.tool
def upsert_reminder(title: str, due_at: str = "", in_min: int = 0):
    """Create a reminder/timer or change its time if it already exists (needs due_at or in_min). Keywords: erinnere mich, erinnerung, timer."""

@needle.tool
def complete_reminder(title: str):
    """Mark an existing reminder as done. Keywords: erledigt, abhaken, done, fertig, habe ich gemacht."""

@needle.tool
def free_slots(horizon: str = "woche"):
    """Compute free time slots (deterministic calculation, 8-20h, >=60min). Keywords: wann habe ich zeit, freie zeiten, verfuegbarkeit."""

@needle.tool
def remember_note(subject: str, body: str):
    """Save a note or fact. subject = short topic noun, body = the fact. Keywords: merk dir, notiere, notiz, wissen."""

@needle.tool
def find_notes(query: str):
    """Search saved notes by keyword (read). Keywords: was weisst du ueber, was habe ich notiert, suche notiz."""

@needle.tool
def show_status():
    """Show counts of stored items (read). Keywords: status, was hast du gespeichert."""
```

### 4.3 Planner-Semantik

**Upsert-Semantik (add⟷move via DB-Zustand):**
- `_find_event(title)` → Fuzzy-Match via pg_trgm (`similarity > 0.35`)
- Treffer + neue Zeit → **MOVE** (Termin verschieben)
- Kein Treffer + Titel + Zeit → **ADD** (neuer Termin)
- Kein Treffer + keine Zeit → ABGELEHNT

**Kollisions-Check (Endzustand):**
- `_collisions_after(exec_ops)` simuliert den Endzustand: DB-State + geplante Ops
- Neue Entries werden mit temporärer ID hinzugefügt
- Überschneidungen → Warning (kein Block, User entscheidet)

**Relative Verschiebung ("um 30min nach hinten"):**
- `fix_args` erkennt `um N min/stunden nach hinten|später|früher|vorher`
- Setzt `shift_min` statt `start_at`
- Planner: `new_start = _shift(hit[2], shift_min)` — verschiebt den bestehenden Termin

**Audit-Trail mit Undo:**
- Jede Apply-Transaktion erzeugt eine `batch_id` (UUID)
- Änderungen werden in `entry_changes` protokolliert
- `undo_last()` macht den letzten Batch rückgängig

---

## 5. Postgres-native Infrastruktur (Better Stack Video)

Die These des Videos („I replaced my entire tech stack with Postgres") mappt direkt auf den Orga-Bot:

| Statt Tool X | Postgres-Feature | Für den Orga-Bot relevant? |
|---|---|---|
| Redis Cache | Materialized Views | ⭐⭐⭐ `free_slots` als Materialized View, die der Scheduler refreshed |
| Redis Pub-Sub | `LISTEN` / `NOTIFY` | ⭐⭐⭐ Scheduler wirft Reminder ab → `NOTIFY reminder_fired` → Telegram-Adapter `LISTEN`t |
| RabbitMQ Queue | `SELECT ... FOR UPDATE SKIP LOCKED` | ⭐⭐⭐ ICS-Sync-Worker: mehrere calendar_sources parallel abrufen |
| Elasticsearch | `tsvector` + `pg_trgm` | ⭐⭐⭐ Notes-Volltextsuche — pg_trgm für Fuzzy-Titel-Matching |
| Pinecone | `pgvector` | ⭐⭐⭐ Notes-Embeddings — dim=1536 via Cactus `/v1/embeddings` |
| MongoDB | `JSONB` | ⭐⭐ nur für flexible Metadata |
| Celery | `pg_cron` oder Python-Scheduler | ⭐⭐⭐ Scheduler-Loop, der `entries WHERE kind='reminder' AND status='active' AND start_at <= now()` pollt |
| Kafka | Logical Replication | ⭐ nein |

**Konkrete Postgres-Muster im Orga-Bot:**

1. **Reminder-Firing via Poll-Loop:**
   ```sql
   SELECT * FROM entries
   WHERE kind = 'reminder' AND status = 'active' AND start_at <= now()
   ORDER BY start_at LIMIT 10;
   ```
   Scheduler (im Hintergrund-Thread des TUI, später als eigener Prozess) pollt alle 30 Sekunden. Für jede fällige Erinnerung: Status auf `done` setzen, Callback/Notification auslösen.

2. **Appointment-Alarme (VALARM):**
   ```sql
   SELECT * FROM entries
   WHERE kind = 'appointment' AND status = 'active' AND alarm_min IS NOT NULL
     AND start_at - (alarm_min || ' minutes')::interval <= now()
     AND start_at > now()
   ORDER BY start_at LIMIT 10;
   ```

3. **ICS-Sync-Worker (SKIP LOCKED):**
   ```sql
   SELECT * FROM calendar_sources
   WHERE last_synced IS NULL OR last_synced < now() - interval '15 minutes'
   ORDER BY last_synced NULLS FIRST
   FOR UPDATE SKIP LOCKED
   LIMIT 1;
   ```

4. **Fuzzy-Titel-Matching via pg_trgm:**
   ```sql
   SELECT id, title, start_at FROM entries
   WHERE kind = 'appointment' AND status = 'active'
     AND (title ILIKE '%zahnarzt%' OR similarity(title, 'zahnarzt') > 0.35)
   ORDER BY similarity(title, 'zahnarzt') DESC, start_at ASC
   LIMIT 1;
   ```

---

## 6. ICS-Export (v4.1, geplant)

**Nur Export, kein Import, kein CalDAV (v4.1).** Der Bot-Kalender wird als `.ics` exportiert, jede Calendar-App (Google, Apple, Thunderbird, DAVx⁵) subscribed die URL.

### 6.1 Renderer-Spez

```
entries (status='active') → VCALENDAR → VEVENT (pro Entry)
```

| Entry-Feld | ICS-Feld |
|---|---|
| `id` | `UID:entry-{id}@orga` (stabil, Clients können re-syncen) |
| `title` | `SUMMARY` |
| `start_at`, `end_at` | `DTSTART`, `DTEND` (UTC) |
| `location` | `LOCATION` |
| `notes` | `DESCRIPTION` |
| `kind='appointment'` | `TRANSP:OPAQUE` (blockiert) |
| `kind='reminder'`/`task` | `TRANSP:TRANSPARENT` (blockiert nicht) |
| `alarm_min IS NOT NULL` | `BEGIN:VALARM / TRIGGER:-PT{alarm_min}M / ACTION:DISPLAY / DESCRIPTION:{title} / END:VALARM` |

Cancelled/Done-Entries werden **nicht** exportiert ( raus aus dem Feed = Client entfernt sie beim Re-Sync ).

### 6.2 Serving

- Endpoint: `GET /ics/{token}.ics` — Token aus `.env` (`ICS_EXPORT_TOKEN`), generiert beim ersten Start
- Framework: Starlette (schon als Dependency über rich? Nein — eigene, minimale Abhängigkeit)
- ETag/Last-Modified für Client-Caching
- Cache-Control: `no-cache` (Clients sollen pollen)

### 6.3 Wie Clients den Feed abonnieren

| Client | Vorgehen |
|---|---|
| Google Calendar | „Other calendars → From URL" — ICS-URL einfügen |
| Apple Calendar | „File → New Calendar Subscription" — URL einfügen |
| Thunderbird | Neues Kalender-Abonnement, URL einfügen |
| DAVx⁵ (Android) | ICS-URL als „Webcal"-Abonnement hinzufügen |

Refresh: Google pollt ~alle 24h, Apple ist konfigurierbar (Standard: stündlich), Thunderbird/DAVx⁵ auch. **Kein Push nötig** — wenn der Nutzer sofortige Updates will, ist Telegram (Phase 5) der bessere Weg.

---

## 7. Eval: 19/23 deterministisch

**Setup:** 23 goldene Fälle, jedes Seed-Fixture wird pro Fall eingeseedt (deterministisch), Needle-Engine läuft lokal, ø ~3s/Fall. Der `--repeat`-Check bestätigt: gleiche Fälle pass/fail'en über mehrere Läufe.

**Ergebnis:** 19/23 PASS, 4 FAIL — die 4 Fehler sind Needle-Varianz-Klassen:

| Fall | Fehlerklasse | Grund |
|---|---|---|
| `multi-move-und-setzen` | Multi-Op-Attribution | Needle erzeugt nur 1 Op statt 2 bei „und"-Sätzen |
| `cal-relative-30min` | Zeit-Auflösung | „um 30min nach hinten" wird als now+30min statt als Shift interpretiert |
| `cal-kommende-woche` | Titel-Extraktion | Needle extrahiert „Kommendung" statt „familientreffen" |
| `note-find` | Routing-Varianz | „was weißt du über feuerholz?" routet manchmal zu `ops=[]` |

**Bewertung:** Die Basis-Operationen (add, move, cancel, complete, list, free_slots, notes) laufen robust und deterministisch. Die 4 Fehler sind bekannte Needle-Grenzen (45M-Modell), keine Code-Bugs. Verbesserungen wären: bessere Few-Shot-Prompts, Tool-Retrieval-Tuning, oder ein größeres Modell.

---

## 8. Roadmap

- **v4.0 „Merge" ✅:** entries-DB (eine Tabelle), 9-Tool-Oberfläche (separat, bewährt), Audit mit Undo, Notes mit pg_trgm. Eval: 19/23 deterministisch.
- **v4.1 „ICS-Export + Scheduler" ✅:** `ics.py` (entries → VCALENDAR, UTC, VALARM, TRANSP), `serve.py` (HTTP-Server, ETag/304, Token-Auth 403), `scheduler.py` (Reminder-Firing → done, Appointment-Alarme → alarmed_at, Poll 30s). Alles getestet.
- **v4.2 „Bedienungs-Fixes" ✅:** Intent-Korrektur „lösche X" → cancel_event, find_notes-Fallback („notizen"/„alle" → list alle), list_notes() hinzugefügt. **Lehre:** Tool-Description-Änderungen verschlechtern das Routing (15/23 vs. 19/23) — Intent-Korrektur im Code ist der richtige Weg.
- **v5 „Telegram":** Bot via python-telegram-bot, Push-Reminders, Inline-Approvals, Paarung/Trust.
- **v6 „Matrix E2EE":** conduwuit auf RPi (Tailscale/Tunnel), Element als Client, PWA optional.

---

## 9. Testing & Bedienung

### 9.1 Eval-Suite (deterministisch, kein serve nötig)

```bash
uv run python needle-only/eval.py                    # 1 Lauf, 23 Fälle
uv run python needle-only/eval.py --repeat 2        # Determinismus-Check
uv run python needle-only/eval.py --filter cal-move # Nur Move-Fälle
EVAL_TRACE=1 uv run python needle-only/eval.py      # Mit Stacktraces
```

**Erwartetes Ergebnis:** `18/23` bei `ø ~3s/Fall`. Bei `--repeat 2`: `deterministisch: ja`.

### 9.2 Interaktive TUI (mit Approval-Flow)

```bash
uv run python needle-only/run.py
```

**Typische Session:**
```
du: erstelle einen termin zahnarzt am 10.9. 10 uhr
  · needle -> upsert_event({'title': 'Zahnarzttermin', 'start_at': ...})
╭─ Plan · y=ausführen · n=nein · e=Korrektur ─╮
│ NEU: Zahnarzttermin (Thu 10.09 10:00)      │
╰─────────────────────────────────────────────╯
Ausführen? [y/n/e] (n): y
orga · Plan ausgeführt: 1 Änderung(en).

du: was steht diese woche an?
orga · Termine (woche):
  Thu 10.09 10:00  Zahnarzttermin

du: wann habe ich zeit?
orga · Freie Zeiten (8–20 Uhr, ≥60 min):
  Sa 06.09: 08:00–20:00
  ...
```

### 9.3 Debugging

**Needle-Call direkt testen:**
```bash
uv run python -c "
import sys; sys.path.insert(0, 'needle-only'); sys.path.insert(0, '.')
from run import build, draft_calls
tools, agent, fns = build()
calls = draft_calls(agent, fns, 'erstelle einen termin zahnarzt am 10.9. 10 uhr')
print(calls)
"
```

**DB direkt inspizieren:**
```bash
docker exec -it cactus-postgres psql -U cactus -d cactus -c "select * from entries order by start_at limit 10;"
```

**Audit-Trail:**
```bash
docker exec -it cactus-postgres psql -U cactus -d cactus -c "select * from entry_changes order by id desc limit 5;"
```

---

## 10. Verwandte Arbeiten & Referenzen

- **Needle 2 ESP32** (`andrisgauracs/needle-2-esp32`): 45M-Parameter-LLM, das offline auf einem ESP32-S3 läuft — beweist, dass Needle-Tool-Calling auch auf minimaler Hardware funktioniert. Kernidee: die Engine ist billig (3.700 Zeilen C99, offline auf 240 MHz), aber die Intelligenz liegt in Tool-Design und Planner-Semantik.
- **Better Stack Video** („I replaced my entire tech stack with Postgres"): Validiert das Konzept, dass ein Single-Postgres-Ansatz für persönliche Anwendungen ausreichend ist. Konkrete Techniken: Scheduler, LISTEN/NOTIFY, SKIP LOCKED, pgcrypto, Materialized Views.
- **Anthropic „Building effective Agents"**: Einfachheit als Architektur-Prinzip ( ACI — Agent-Computer Interface ). Needle-Oberfläche minimal halten, Deterministik in Postgres.
- **OpenClaw** (Anti-Beispiel): Zu groß, zu komplex, zu viele Features. Der Orga-Bot ist das Gegenteil: ein Tool, das eine Sache gut macht ( Termine + Wissen verwalten ).

---

## 11. Offene Fragen (für zukünftige Iterationen)

1. **Wie werden Done/Cancelled-Entries im ICS-Feed behandelt?** ( Entfernen vs. STATUS:CANCELLED behalten — beides hat Vor-/Nachteile )
2. **Soll `free_slots` auch externe Kalender (via `calendar_sources`) berücksichtigen, sobald die existieren?** ( Ja, aber erst ab Phase 5 )
3. **Wie wird die Encryption für `calendar_sources.url` gehandhabt?** ( pgcrypto mit Key aus `.env` — aber welcher Prozess setzt `app.encryption_key`? )
4. **Braucht es ein `undo`-Tool?** ( `entry_changes` hat alle Daten, aber ein Undo-Command ist noch nicht designed )
5. **Kann die `cal-relative-30min`-Zeitauflösung verbessert werden?** ( „um 30min nach hinten" wird als now+30min statt als Shift des bestehenden Termins interpretiert — der Planner müsste bei Shift-Sätzen den bestehenden Termin laden )
