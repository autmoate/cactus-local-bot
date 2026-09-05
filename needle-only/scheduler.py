"""Scheduler v4.2: Reminder-Firing + Appointment-Alarme.
Pollt entries alle 30 Sekunden (Background-Thread, daemon=True).
- Reminder fällig (start_at <= now) → Notification + status='done'
- Appointment-Alarm (start_at - alarm_min <= now, alarmed_at IS NULL)
  → Notification + alarmed_at=now() (einmalig)
Terminiert sauber mit stop()."""
import threading
from datetime import datetime, timezone

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
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="orga-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        # Ersten Tick sofort, danach alle INTERVAL Sekunden
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                # DB weg? Nächster Versuch in INTERVAL Sekunden
                print(f"  · scheduler: {str(exc)[:60]}")
            self._stop.wait(INTERVAL)

    def tick(self):
        """Ein Scheduler-Durchlauf (auch manuell testbar)."""
        now = tz_now()
        # 1) Fällige Reminder feuern (einmalig via status-Übergang)
        due = self.orga._q(
            "select id, title, start_at from entries "
            "where kind='reminder' and status='active' "
            "and start_at <= %s order by start_at limit 10",
            (now,))
        for rid, title, start_at in due:
            self.orga._q(
                "update entries set status='done', updated_at=now() "
                "where id=%s", (rid,))
            self.notify(f"⏰ Erinnerung fällig: {title}")

        # 2) Appointment-Alarme feuern (einmalig via alarmed_at)
        alarms = self.orga._q(
            "select id, title, start_at, alarm_min from entries "
            "where kind='appointment' and status='active' "
            "and alarm_min is not null and alarmed_at is null "
            "and start_at - (alarm_min || ' minutes')::interval <= %s "
            "and start_at >= %s "
            "order by start_at limit 10",
            (now, now))
        for aid, title, start_at, alarm_min in alarms:
            self.orga._q(
                "update entries set alarmed_at=now(), updated_at=now() "
                "where id=%s", (aid,))
            remaining = max(0, int((start_at - now).total_seconds() / 60))
            self.notify(f"🔔 {title} in {remaining} min")
