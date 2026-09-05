"""ICS-Export v4.1: entries → VCALENDAR (RFC 5545).
Nur status='active'. UTC-Zeiten, CRLF, Escaping, VALARM bei alarm_min,
TRANSP je kind. Deterministisch, kein Modell."""
import hashlib
from datetime import datetime, timezone

from modules.timesync import _TZ

PRODID = "-//orga//v4.1//DE"
CALNAME = "Orga"


def _esc(text) -> str:
    """Escape , ; \\ newline für ICS-Values (RFC 5545 §3.3.11)."""
    return (str(text or "").replace("\\", "\\\\")
            .replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _fold(line: str) -> list:
    """RFC 5545 §3.1 Line-Folding: max 75 Oktette, Continuation mit Space."""
    if len(line.encode("utf-8")) <= 75:
        return [line]
    out, cur = [], line
    while len(cur.encode("utf-8")) > 75:
        cut = min(75, len(cur))
        while cut > 1 and len(cur[:cut].encode("utf-8")) > 75:
            cut -= 1
        out.append(cur[:cut])
        cur = " " + cur[cut:]
    out.append(cur)
    return out


def _dt_utc(dt) -> str:
    """datetime → UTC im ICS-Format (20260910T103000Z)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_ics(orga) -> str:
    """entries (status='active') → ICS-String mit CRLF-Line-Endings."""
    entries = orga._q(
        "select id, kind, title, start_at, end_at, alarm_min, "
        "location, notes from entries "
        "where status='active' order by start_at")
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_esc(CALNAME)}",
        "X-WR-TIMEZONE:Europe/Berlin",
    ]
    for id_, kind, title, start_at, end_at, alarm_min, location, notes in entries:
        raw.append("BEGIN:VEVENT")
        raw.append(f"UID:entry-{id_}@orga")
        raw.append(f"DTSTAMP:{now}")
        raw.append(f"DTSTART:{_dt_utc(start_at)}")
        if end_at:
            raw.append(f"DTEND:{_dt_utc(end_at)}")
        raw.append(f"SUMMARY:{_esc(title)}")
        if location:
            raw.append(f"LOCATION:{_esc(location)}")
        if notes:
            raw.append(f"DESCRIPTION:{_esc(notes)}")
        raw.append("TRANSP:OPAQUE" if kind == "appointment" else "TRANSP:TRANSPARENT")
        if alarm_min:
            raw.extend([
                "BEGIN:VALARM",
                f"TRIGGER:-PT{alarm_min}M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_esc(title)}",
                "END:VALARM",
            ])
        raw.append("END:VEVENT")
    raw.append("END:VCALENDAR")
    # Line-Folding auf jede logische Zeile anwenden
    physical = []
    for line in raw:
        physical.extend(_fold(line))
    return "\r\n".join(physical) + "\r\n"


def etag_for(ics: str) -> str:
    """Stabiler ETag (md5) für Client-Caching / 304-Antworten."""
    return f'W/"{hashlib.md5(ics.encode("utf-8")).hexdigest()[:16]}"'
