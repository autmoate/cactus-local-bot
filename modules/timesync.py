import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Berlin"
_TZ = ZoneInfo(DEFAULT_TZ)
_WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")


def now() -> datetime:
    return datetime.now(_TZ)


def human_now() -> str:
    d = now()
    wd = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")[d.weekday()]
    return f"{wd}, {d.strftime('%d.%m.%Y')}, {d.strftime('%H:%M')}"


def today_iso() -> str:
    return now().strftime("%Y-%m-%d")


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="minutes")


def format_local(iso: str, fmt: str = "%a %d.%m %H:%M") -> str:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ)
        return dt.astimezone(_TZ).strftime(fmt)
    except Exception:
        return str(iso)


def _match_time(low: str) -> int:
    scan = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b", " ", low)  # Datumsangaben raus (kein HH.MM-Fehlgriff)
    m = re.search(r"(\d{1,2})[:.](\d{2})", scan)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"\b(\d{1,2})\s*uhr\b", scan)
    if m:
        return int(m.group(1)) * 60
    return 9 * 60


def _next_weekday(ref: datetime, name: str) -> datetime:
    target = _WEEKDAYS.index(name)
    days = (target - ref.weekday()) % 7
    if days == 0:
        days = 7
    return ref + timedelta(days=days)
def resolve_dt(text: str, ref: datetime | None = None) -> datetime | None:
    ref = ref or now()
    low = text.lower().strip()
    for en, de in (("day after tomorrow", "übermorgen"), ("next week", "kommende woche"),
                   ("monday", "montag"), ("tuesday", "dienstag"), ("wednesday", "mittwoch"),
                   ("thursday", "donnerstag"), ("friday", "freitag"), ("saturday", "samstag"),
                   ("sunday", "sonntag"), ("tomorrow", "morgen"), ("today", "heute")):
        low = low.replace(en, de)
    minutes = _match_time(low)
    m = re.search(r"\bin\s+(\d+)\s*(min(uten?|ute)?s?|m|stunde?n?|h(our)?s?)\b", low)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith(("min", "m")):
            delta = timedelta(minutes=amount)
        else:
            delta = timedelta(hours=amount)
        return (ref + delta).replace(second=0, microsecond=0)
    base: datetime | None = None
    if "übermorgen" in low:
        base = ref + timedelta(days=2)
    elif "uebermorgen" in low:
        base = ref + timedelta(days=2)
    elif "morgen" in low:
        base = ref + timedelta(days=1)
    elif "heute" in low:
        base = ref
    else:
        found = next((w for w in _WEEKDAYS if w in low), None)
        if found:
            base = _next_weekday(ref, found)
        else:
            for fmt in ("%d.%m.%Y", "%d.%m.", "%Y-%m-%d"):
                m = re.search(r"\d", low)
                try:
                    marker = low
                    if fmt == "%d.%m.":
                        m = re.search(r"(\d{1,2})\.(\d{1,2})\.", low)
                        if not m:
                            continue
                        base = datetime(int(ref.year), int(m.group(2)), int(m.group(1)), tzinfo=_TZ)
                    else:
                        m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", low) if fmt == "%d.%m.%Y" else re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", low)
                        if not m:
                            continue
                        if fmt == "%d.%m.%Y":
                            base = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=_TZ)
                        else:
                            base = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=_TZ)
                    break
                except ValueError:
                    continue
    if base is None:
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", low)
        if m:
            base = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=_TZ)
    if base is None:
        return None
    if minutes // 60 > 23:
        return None  # z. B. '30min' fälschlich als Uhrzeit gelesen
    candidate = base.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    if candidate < ref:
        candidate += timedelta(days=1)
    return candidate


_WEEKDAY_ALIASES = {
    "montag": 0, "mo": 0, "montags": 0,
    "dienstag": 1, "di": 1, "dienstags": 1,
    "mittwoch": 2, "mi": 2, "mittwochs": 2,
    "donnerstag": 3, "do": 3,
    "freitag": 4, "fr": 4, "freitags": 4,
    "samstag": 5, "sa": 5, "sam": 5, "samstags": 5,
    "sonntag": 6, "so": 6, "sonntags": 6,
}


def parse_calendar(text: str, ref: datetime | None = None) -> dict:
    """Generalistischer deutscher Kalender-Auflöser (RFC-5545-ähnlich).
    Liefert {found, iso, cleaned, hint}: on terminal Tag/Zeit aus 'kommende woche sa. ab 12:30Uhr'.
    """
    ref = ref or now()
    low = text.lower()
    result = {"found": False, "iso": None, "cleaned": text, "hint": ""}
    week_off = 0
    if re.search(r"(kommende|nächste|naechste|folgende)\s+woche", low):
        week_off = 1
    wd = None
    found_wd = None
    for alias in sorted(_WEEKDAY_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b\.?", low):
            wd = _WEEKDAY_ALIASES[alias]
            found_wd = alias
            break
    monday = ref - timedelta(days=ref.weekday())
    base: datetime | None = None
    if wd is not None:
        base = (monday + timedelta(days=week_off * 7 + wd)).replace(hour=0, minute=0, second=0, microsecond=0)
        if week_off == 0 and base <= ref:
            base = (monday + timedelta(days=7 + wd)).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        for kw, delta in (("übermorgen", 2), ("uebermorgen", 2), ("morgen", 1), ("heute", 0), ("gestern", -1)):
            if kw in low:
                base = (ref + timedelta(days=delta)).replace(hour=0, minute=0, second=0, microsecond=0)
                break
    m = (re.search(r"(\d{1,2}):(\d{2})\s*(?:uhr)?\b", low)
         or re.search(r"(\d{1,2})\.(\d{2})\s*uhr\b", low)
         or re.search(r"\b(\d{1,2})\s*uhr\b", low))
    if m and m.lastindex == 2:
        hh, mm = int(m.group(1)), int(m.group(2))
    elif m:
        hh, mm = int(m.group(1)), 0
    else:
        hh, mm = 9, 0
    if base is not None:
        start = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if start < ref:
            start += timedelta(days=1)
        result["found"] = bool(found_wd or not wd)
        result["iso"] = to_utc_iso(start)
        result["hint"] = found_wd or "tag"
    cleaned = low
    for pat in (r"(kommende|nächste|naechste|folgende|diese)\s+woche\b",
                r"\b(?:am|ab|um|von|bis|gegen)\b",
                r"\b\d{1,2}\s*(?::\d{2}|\.\d{2})?\s*uhr\b",
                r"\b\d{1,2}:\d{2}\b",
                r"s\.?\s*ab",
                r"der\s+kommende\s+woche"):
        cleaned = re.sub(pat, " ", cleaned)
    if found_wd:
        cleaned = re.sub(rf"\b{re.escape(found_wd)}\b\.?", " ", cleaned)
    for kw in ("in den kalender", "im kalender", "eintragen", "eingetragen", "eintrag", "anlegen", "plane", "plant", "bitte", "für", "kannst du", "könntest du", "sollst"):
        cleaned = re.sub(rf"\b{kw}\b", " ", cleaned)
    result["cleaned"] = " ".join(w for w in cleaned.split() if w).strip(" ,;.!?:")
    return result
