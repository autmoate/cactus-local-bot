#!/usr/bin/env python3
"""Read-only DB/identity audit (plan §29).

Prints counts and schema state only — NEVER private event titles, Telegram
tokens or .env contents. Optional --fix-owner <telegram_user_id> binds/merges
the legacy 'Ich' bucket to that authorized owner (with a backup first); the id
comes from the CLI, never from .env.

Usage:
    python scripts/audit_calendar_state.py [--db data/calendar.db]
    python scripts/audit_calendar_state.py --fix-owner 123456 [--name Oll]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from local_calendar.calendar import CalendarStore  # noqa: E402


def _count(c, sql: str, *params) -> int:
    return c.execute(sql, params).fetchone()[0]


def audit(store: CalendarStore) -> None:
    with store._conn() as c:
        uv = c.execute("PRAGMA user_version").fetchone()[0]
        personal = _count(c, "SELECT COUNT(*) FROM calendars WHERE kind='personal'")
        groups = _count(c, "SELECT COUNT(*) FROM calendars WHERE kind='group'")
        orphan_ev = _count(c, "SELECT COUNT(*) FROM events WHERE calendar_id IS NULL")
        orphan_ep = _count(c, "SELECT COUNT(*) FROM event_participants ep "
                              "LEFT JOIN people p ON p.id=ep.person_id "
                              "WHERE p.id IS NULL")
        no_part = _count(c, "SELECT COUNT(*) FROM events e WHERE NOT EXISTS "
                            "(SELECT 1 FROM event_participants ep "
                            "WHERE ep.event_id=e.id)")
        print(f"schema_version: {store.SCHEMA_VERSION} (db user_version={uv})")
        print(f"people: {_count(c, 'SELECT COUNT(*) FROM people')}")
        print(f"personal_calendars: {personal}")
        print(f"group_calendars: {groups}")
        print(f"events_total: {_count(c, 'SELECT COUNT(*) FROM events')}")
        rows = c.execute("SELECT calendar_id, COUNT(*) n FROM events "
                         "GROUP BY calendar_id ORDER BY calendar_id").fetchall()
        print("events_per_calendar: " +
              ", ".join(f"cal={r['calendar_id']}:{r['n']}" for r in rows))
        # orphan rows (never event titles)
        print(f"orphan_events_no_calendar: {orphan_ev}")
        print(f"orphan_participants: {orphan_ep}")
        print(f"events_without_participants: {no_part}")
        dup_names = c.execute(
            "SELECT display_name, COUNT(*) n FROM people WHERE enabled=1 "
            "GROUP BY display_name HAVING n>1").fetchall()
        print(f"duplicate_name_rows: {len(dup_names)}")
        legacy = c.execute("SELECT COUNT(*) FROM people WHERE telegram_user_id "
                           "IS NULL AND display_name='Ich'").fetchone()[0]
        print(f"legacy_owner_candidates: {legacy}")


def fix_owner(store: CalendarStore, telegram_user_id: int, name: str | None) -> None:
    store.backup()
    existing = store.person_by_telegram(telegram_user_id)
    display = name or (existing["display_name"] if existing else "Ich")
    pid = store.reconcile_owner(telegram_user_id, display)
    print(f"reconciled owner -> person_id={pid} (backup written)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Kalender-Pin state audit (read-only)")
    ap.add_argument("--db", default="data/calendar.db")
    ap.add_argument("--fix-owner", type=int, default=None,
                    help="authorized Telegram owner user id to reconcile (writes!)")
    ap.add_argument("--name", default=None, help="display name for --fix-owner")
    args = ap.parse_args()
    store = CalendarStore(args.db)
    if args.fix_owner is not None:
        fix_owner(store, args.fix_owner, args.name)
    audit(store)


if __name__ == "__main__":
    main()
