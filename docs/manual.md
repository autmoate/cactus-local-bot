# Manual

## Start
```bash
uv run python scripts/launch.py
```
Startet Postgres, `cactus serve` (Modell lädt ~20–30 s); optional SearXNG (`WEB_ENABLED=1`) bleibt als Docker-Container. Die TUI wartet nach Auto-Start auf den Server. **Persistenz:** Daten bleiben zwischen Neustarts erhalten (Dim wird kanonisch auf `EMBED_DIM` gehalten, kein Schema-Drop). Beim Start zeigt `cactus` ein kurzes **Briefing** (Datum/Zeit, offene To-dos, Termine diese Woche, Inventar/Fakten) und führt optional die Wartung aus.

## Architektur (v3, geschärft)
- **Toolwahl 100 % Gemma** (Function-Calling + Few-Shots): keine Regex-Fallbacks, die Tools erraten. Nur **deterministische Zeit-/Struktur-Fixes** (`parse_calendar`, `resolve_dt`, `_fix_time`) korrigieren Argumente nach — Zeit ist lösbar, Toolwahl nicht.
- **Needle = Zweitmeinung nur bei Writes** (advisory); Reads ohne Verifier. Approval-Gate bleibt die einzige Schreibgrenze.
- **Gedächtnis nach Mem0-Muster** (`modules/facts.py`): Fakten **ADD-only** (`valid_from/valid_to` + `supersede`-Kante im Graph), Retrieval mit **Zeit-Decay** (aktuelles schlägt Altes). Lernen ohne Finetuning.
- **Injection-Guard**: `web_search`-Ergebnisse sind Daten („WEB:"-Tag); Writes in derselben Runde nach einer Suche werden blockiert.
- **Roh-Chat-Retention**: nächtliche Wartung löscht Nachrichten älter als `MSG_RETENTION_HOURS` (Std, Default 24).
- **Output-Normalisierung**: Cactus/Gemma-Quirks (Calls als Text, verschmolzene name+args, `\u`-Escapes) werden generell geparst — kein Einzelfall-Coding.
- **TUI**: Hintergrund-Aktivität als Spinner-Zeile („· ⠹ trage in den Kalender ein …", Messenger-tauglich), danach max. 4 kompakte Logzeilen; `/logs` schaltet um.

## Eval-Suite (robustheits-check)
```bash
uv run python scripts/eval.py            # alle 26 goldenen Fälle (braucht laufendes serve)
uv run python scripts/eval.py --filter calendar
```
Baseline: **25–26/26** (bekannter Flake: „verbrauche 2 flaschen öl" → title manchmal ‚flaschen'). Fälle: Kalender (Wochentag/„kommende woche"/Uhrzeit), Todos/Timern (relative Zeiten), Inventar, Rückfragen (nie Write), Reads, Silence/Off-topic/Gated-Writes.

## Empfohlener Testablauf (Beispiele)
```
welche termine stehen an?                     -> list_upcoming (Read, sofort)
wir haben nur noch 4 säcke grillkohle          -> Vorschlag update_inventory(grillkohle,4) -> y
erinnere mich in 10 min daran, grillkohle
    nachzukaufen                               -> create_todo (Approval) -> "Gemerkt: … (…). Ich erinnere daran."
kannst du für kommende woche sa. ab 12:30Uhr
    kindergeburtstag eintragen?                -> create_calendar_event (Sa 12.09 12:30, Titel sauber)
moment, wann hast du kindergeburtstag …?       -> search_records -> "Gefunden: kindergeburtstag (Sa 12.09 12:30)"
hi / danke                                    -> still (kein Output)
@ wie ist das wetter?                          -> Antwort bzw. Grenze (außerhalb Kompetenz)
```
Duplikatschutz: identischer Termin (±1 Tag) wird nicht doppelt angelegt.

## Befehle
`/status` · `/logs` · `/graph` · `/soul[ <key>=<wert>]` · `/facts` · `/changes` · `/memory` · `/help` · `quit`

## Konfiguration (`.env`)
`EMBED_MODEL`, `RECALL_TOP_K`, `BEHAVIOR`, `SENSORS_ON`, `REMINDER_LOOKAHEAD_MIN`, `REASONING_LEVEL`,
`TZ_LOCAL`, `SEARXNG_URL`, `WEB_ENABLED`, `MSG_RETENTION_HOURS` (Roh-Chat-Löschung, Default 24).

## needle-only v3.1 („Plan-Werkstatt", experimentell)
```bash
uv run python needle-only/run.py            # TUI: Ops → Plan → EIN Approval (y/n/e/q) → atomar
uv run python needle-only/eval.py           # 22 Fälle, ø ~2,3 s, kein serve nötig
uv run python needle-only/eval.py --repeat 2  # Determinismus-Check
```
**Sprache = Diff-Spezifikation für den Kalender-Zustand.** Needle extrahiert atomare Ops, der Planner (`orga.py`) macht den Rest:
- **Upsert-Semantik**: `upsert_event` heißt so, weil es add/change nicht mehr geben kann — existiert ein ähnlicher Termin (pg_trgm-Fuzzy, Tippfehler-tolerant) → VERSCHIEBEN, sonst NEU. `upsert_reminder` auf existierenden Termin → wird automatisch zum Event-Op.
- **Endzustand statt Einzelschritte**: Ops werden normalisiert, Reihenfolge egal, Kollisionen im SOLL-Zustand geprüft und im Plan gewarnt; Ausführung atomar (1 Transaktion).
- **EIN Approval pro Turn**: gerenderter Plan („ÄNDERN: Zahnarzt: Do 10:00 → 10:30"), `e` = Freitext-Korrektur → neue Needle-Runde (kein Befehls-Format!), `q` = abbrechen.
- **Multi-Op-Sätze**: bei „… und …" ein zweiter Needle-Sample mit Merge (dedup nach Tool+Titel).
- Relative Verschiebung: „verschiebe X um 30 min (nach hinten/später/früher)" → shift vom Ist-Wert. Absolute Zeiten („auf 10:30") schlagen Modellwerte; alles LOCAL-Zeit gerendert.
- Stand: **14/22, ø ~2,3 s, deterministisch (2 Läufe identisch)**. Rest = Needle-Varianz: Titel-Transkription („familientreffen"→„Kommendung"), Erinnerung-vs-Termin-Verwechslung bei „setze X auf Uhr", fehlende Calls bei Verwaltungs-Ops — Fehl-Adds durch Duplikatschutz/Approval abgefangen. Kein Chat.

## Hinweise / Grenzen
- „list todos" (engl. Alias) degeneriert gelegentlich zu leerem Output (Gemma-Quirk) — natürliche Phrasen („zeig alle todos", „was steht an?") sind robust.
- Hängt `serve` (z. B. nach abgebrochenen Läufen): neustarten (`scripts/launch.py` erneut oder `cactus serve …`).
- `events` (ICS-orientiert: start/end, status, urgency, repeats, ort, teilnehmer) + `event_changes`-Log.
- Websuche (SearXNG) ist best-effort (Engines teils rate-limited von der RPi-IP).
