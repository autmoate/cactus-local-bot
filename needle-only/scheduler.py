"""Scheduler v5.3: Reminder-Firing + Appointment-Alarme.

Neuer Lifecycle (vom Nutzer so spezifiziert):
- Erinnerungen (kind='reminder') werden nach dem Auslösen GELÖSCHT
- Appointment-Alarme werden via alarmed_at genau einmal gefeuert
- Vergangene Termine bleiben (Archiv) — der Scheduler räumt sie NICHT auf

Der Scheduler läuft als Background-Thread (daemon=True) und tickt alle 30 Sekunden."""
import threading
from datetime import datetime, timedelta

from modules.timesync import now as tz_now

INTERVAL = 30  # Sekunden


class Scheduler:
    def __init__(self, orga, notify=None):
        self.orga = orga
        self.notify = notify or (lambda msg: print(f"  · {msg}"))
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        """Scheduler als daemon-Thread starten (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="orga-scheduler")
        self._thread.start()

    def stop(self):
        """Scheduler anhalten."""
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        """Hauptschleife: tick alle INTERVAL Sekunden."""
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                # DB nicht erreichbar? Nächster Versuch in INTERVAL Sekunden
                print(f"  · scheduler fehler: {str(exc)[:60]}")
            self._stop.wait(INTERVAL)

    def tick(self):
        """Ein Scheduler-Durchlauf (auch manuell testbar)."""
        now = tz_now()

        # 1) Fällige Reminder feuern und SOFORT LÖSCHEN
        #    Lifecycle: active → [Feuerung] → DELETE (nicht done!)
        due = self.orga._q(
            "SELECT id, title FROM entries "
            "WHERE kind = 'reminder' AND start_at <= %s "
            "ORDER BY start_at LIMIT 10",
            (now,))
        for rid, title in due:
            self.orga._q("DELETE FROM entries WHERE id = %s", (rid,))
            self.notify(f"⏰ {title}")

        # 2) Appointment-Alarme feuern (einmalig via alarmed_at)
        #    Ein Appointment mit alarm_min=30 feuert 30 Minuten vor start_at
        alarms = self.orga._q(
            "SELECT id, title, start_at FROM entries "
            "WHERE kind = 'appointment' AND alarm_min IS NOT NULL "
            "AND alarmed_at IS NULL "
            "AND start_at - (alarm_min || ' minutes')::interval <= %s "
            "AND start_at >= %s "
            "ORDER BY start_at LIMIT 10",
            (now, now))
        for aid, title, start_at in alarms:
            self.orga._q(
                "UPDATE entries SET alarmed_at = now() WHERE id = %s",
                (aid,))
            remaining = max(0, int((start_at - now).total_seconds() / 60))
            self.notify(f"🔔 {title} in {remaining} min")
