"""Compact calendar snapshots with Pillow (plan §21-22, §26).

Tweek-inspired principles: little chrome, no empty hour grid, events visually
dominant, all-day ranges as bands, participant-aware. No model logic here —
the store/solver provides the truth, the renderer only visualizes."""

from __future__ import annotations

import io
from datetime import date, datetime, time, timedelta

from PIL import Image, ImageDraw, ImageFont

from . import calendar as cal

W = 1080
DAY_W = W // 7
MARGIN, TITLE_H, HEAD_H = 16, 60, 92
INK, MUTED = "#27272a", "#8a8a8a"
COLORS = {"appointment": "#cfe8ff", "absence": "#ffd9a8"}


def _font(size: int):
    return ImageFont.load_default(size)


def _monday(today: date) -> date:
    return today - timedelta(days=today.weekday())


def _week_days(first_day: date) -> list[date]:
    return [first_day + timedelta(days=i) for i in range(7)]


def _events_for(store, days: list[date], people: list[str] | None):
    start = datetime.combine(days[0], time(0, 0))
    end = datetime.combine(days[-1] + timedelta(days=1), time(0, 0))
    events = store.events_between(start, end)
    if people and "all" not in people:
        events = [e for e in events if set(e.participants) & set(people)]
    return events


def _fit(draw, text: str, font, max_width: int) -> str:
    """Truncate text to max_width with an ellipsis (plan §8: long titles must
    not bleed into neighbouring columns)."""
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


def render_week_png(store, first_day: date,
                    people: list[str] | None = None) -> bytes:
    """Week snapshot: one column per day, only actual events are drawn."""
    days = _week_days(first_day)
    events = _events_for(store, days, people)
    allday = [e for e in events if e.all_day]
    timed = [e for e in events if not e.all_day]
    today = cal.now().date()

    H = HEAD_H + 80 + max(120, 34 * (len(timed) + 1)) + 34 * (
        sum(1 for e in allday for d in days if e.start.date() <= d < e.end.date()))
    H = min(max(H, 320), 1400)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f_title, f_head, f_body, f_small = _font(30), _font(24), _font(19), _font(15)

    title = ("Diese Woche" if first_day == _monday(today)
             else f"{days[0]:%d.%m.} – {days[-1]:%d.%m.}")
    d.text((MARGIN, 14), title, font=f_title, fill=INK)
    d.text((W - 230, 22), f"{days[0]:%d.%m.} – {days[-1]:%d.%m.}",
           font=f_small, fill=MUTED)

    for i, day in enumerate(days):
        x = i * DAY_W
        d.text((x + 10, TITLE_H), f"{cal.WEEKDAYS_DE[day.weekday()]} {day.day}.",
               font=f_head if day == today else f_small,
               fill=INK if day == today else MUTED)
        d.line([(x + 6, HEAD_H), (x + DAY_W - 6, HEAD_H)],
               fill="#dddddd", width=1)

    y_abs = HEAD_H + 6
    for i, day in enumerate(days):
        yy = y_abs
        for e in allday:
            if e.start.date() <= day < e.end.date():
                text = e.title if len(e.participants) <= 1 \
                    else f"{e.title} ({', '.join(e.participants)})"
                d.rectangle([i * DAY_W + 4, yy, (i + 1) * DAY_W - 4, yy + 20],
                            fill=COLORS["absence"])
                d.text((i * DAY_W + 8, yy + 2),
                       _fit(d, text, f_small, DAY_W - 16), font=f_small, fill=INK)
                yy += 24
    y_body = max(y_abs + 24, HEAD_H + 60)
    for i, day in enumerate(days):
        day_events = sorted(
            (e for e in timed if e.start.date() <= day < e.end.date()),
            key=lambda e: e.start)
        yy = y_body
        for e in day_events:
            d.rectangle([i * DAY_W + 4, yy, (i + 1) * DAY_W - 4, yy + 26],
                        fill=COLORS.get(e.kind, "#eeeeee"))
            d.text((i * DAY_W + 8, yy + 2),
                   _fit(d, f"{e.start:%H:%M} {e.title}", f_body, DAY_W - 14),
                   font=f_body, fill=INK)
            yy += 32
    for i in range(1, 7):
        d.line([(i * DAY_W, TITLE_H), (i * DAY_W, H)], fill="#eeeeee", width=1)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_availability_png(store, people: list[str],
                            day: date | None = None,
                            resolved: dict | None = None) -> bytes:
    """Availability widget (plan §15/§26/§34): the executed solver result is the
    truth. The rendered day is resolved.first_day, NEVER cal.now() — otherwise a
    query for the 29.09. would show the 24.09. The renderer never re-schedules."""
    if day is None:
        first = (resolved or {}).get("first_day")
        try:
            day = cal.date.fromisoformat(first) if first else cal.now().date()
        except ValueError:
            day = cal.now().date()
    if resolved:
        names = [cal._canonical_person(p) for p in resolved.get("persons", [])]
        names = [n for n in names if n] or ["Ich"]
        h0, h1 = 7, 21
        try:
            h0 = int(resolved.get("window_start", "07:00")[:2])
            h1 = int(resolved.get("window_end", "21:00")[:2]) + 1
        except ValueError:
            pass
        slots = []
        for s_iso, e_iso in resolved.get("slots", []):
            try:
                s_dt, e_dt = datetime.fromisoformat(s_iso), datetime.fromisoformat(e_iso)
                if s_dt.date() == day:
                    slots.append((s_dt, e_dt))
            except ValueError:
                continue
    else:
        names = sorted(set(people)) if people and "all" not in people \
            else sorted(set(store.participant_names()) or {"Ich"})
        h0, h1 = 7, 21
        slots = cal.find_free_slots(store, names, day, day, 60)
    n_slots = (h1 - h0) * 2
    bar_w, row_h, pad, label_w = 27, 40, 20, 140
    Wv = pad + label_w + n_slots * bar_w + pad
    Hv = row_h + row_h * (len(names) + 1) + 70
    img = Image.new("RGB", (Wv, Hv), "white")
    d = ImageDraw.Draw(img)
    f_head, f_body = _font(22), _font(18)

    d.text((pad, 8), f"{cal.WEEKDAYS_DE[day.weekday()]} {day:%d.%m.%Y}",
           font=f_head, fill=INK)
    for j, h in enumerate(range(h0, h1, 3)):
        x = pad + label_w + j * 6 * bar_w
        d.text((x, row_h), f"{h:02d}", font=f_body, fill=MUTED)
    win_start = datetime.combine(day, time(h0, 0))
    win_end = datetime.combine(day, time(h1, 0))
    for i, p in enumerate(names):
        y = row_h + 26 + i * row_h
        d.text((pad, y), p, font=f_body, fill=INK)
        busy = cal.busy_intervals(store, [p], win_start, win_end)
        for j in range(n_slots):
            slot = win_start + timedelta(minutes=30 * j)
            slot_end = slot + timedelta(minutes=30)
            hit = any(s < slot_end and slot < e for s, e in busy)
            x = pad + label_w + j * bar_w
            d.rectangle([x, y + 4, x + bar_w - 4, y + row_h - 12],
                        fill="#5a7d9a" if hit else "#e8e8e8")
    y_free = row_h + 26 + len(names) * row_h
    d.text((pad, y_free), "frei:", font=f_body, fill=INK)
    d.text((pad + 60, y_free),
           ", ".join(f"{s:%H:%M}–{e:%H:%M}" for s, e in slots)
           or "keine gemeinsamen Slots",
           font=f_body, fill=INK)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_day_png(store, day: date,
                   people: list[str] | None = None) -> bytes:
    """Day snapshot (plan §7): same principles as the week view, one column."""
    events = _events_for(store, [day], people)
    allday = [e for e in events if e.all_day]
    timed = sorted((e for e in events if not e.all_day),
                   key=lambda e: e.start)
    H = 90 + 30 * (len(allday) + len(timed)) + 60
    img = Image.new("RGB", (W, max(H, 240)), "white")
    d = ImageDraw.Draw(img)
    f_title, f_head, f_body, f_small = _font(30), _font(24), _font(19), _font(15)
    today = cal.now().date()
    title = "Heute" if day == today else f"{cal.WEEKDAYS_DE[day.weekday()]} {day:%d.%m.%Y}"
    d.text((MARGIN, 14), title, font=f_title, fill=INK)
    yy = 66
    for e in allday:
        text = e.title if len(e.participants) <= 1 \
            else f"{e.title} ({', '.join(e.participants)})"
        d.rectangle([MARGIN, yy, W - MARGIN, yy + 24], fill=COLORS["absence"])
        d.text((MARGIN + 8, yy + 2), _fit(d, text, f_small, W - 2 * MARGIN - 16),
               font=f_small, fill=INK)
        yy += 30
    for e in timed:
        d.rectangle([MARGIN, yy, W - MARGIN, yy + 26],
                    fill=COLORS.get(e.kind, "#eeeeee"))
        d.text((MARGIN + 8, yy + 2),
               _fit(d, f"{e.start:%H:%M}–{e.end:%H:%M} {e.title}", f_body,
                    W - 2 * MARGIN - 16), font=f_body, fill=INK)
        yy += 32
    if not allday and not timed:
        d.text((MARGIN, yy), "Keine Termine an diesem Tag.", font=f_body,
               fill=MUTED)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------ multi-user view rendering

def _banner_label(banner) -> str:
    title = banner.title or "Abwesenheit"
    if banner.start_day == banner.end_day:
        span = f"{banner.start_day:%d.%m.}"
    else:
        span = f"{banner.start_day:%d.%m.}–{banner.end_day:%d.%m.}"
    return f"🏖 {title} · {span}"


def _block_label(b, day) -> str:
    seg = cal.segment_event(b, day)
    if seg is None:
        return ""
    s, e = seg
    text = b.title if b.title else "belegt"
    if b.all_day:
        return text
    return f"{s:%H:%M}–{e:%H:%M} {text}"


def render_week_view(view) -> bytes:
    """Week from a WeekView (plan §22/§24): all-day banners on top, timed cards
    clipped inside exactly their own day column, one lane per person. Private
    titles of others are already masked in the view data (plan §9)."""
    days = view.days
    lanes = view.lanes
    lane_h = 42
    shared = view.shared
    shared_h = 30 * len(shared) + (10 if shared else 0)
    H = HEAD_H + shared_h + lane_h * max(1, len(lanes)) + 56
    img = Image.new("RGB", (W, max(H, 320)), "white")
    d = ImageDraw.Draw(img)
    f_title, f_head, f_body, f_small = _font(30), _font(24), _font(19), _font(13)
    today = cal.now().date()
    title = ("Gruppenwoche" if view.group else
             ("Diese Woche" if days[0] == _monday(today)
              else f"{days[0]:%d.%m.} – {days[-1]:%d.%m.}"))
    d.text((MARGIN, 14), title, font=f_title, fill=INK)
    for i, day in enumerate(days):
        x = i * DAY_W
        d.text((x + 8, TITLE_H), f"{cal.WEEKDAYS_DE[day.weekday()]} {day.day}.",
               font=f_head if day == today else f_small,
               fill=INK if day == today else MUTED)
        d.line([(x + 4, HEAD_H), (x + 4, H)], fill="#eeeeee", width=1)
    # all-day / absence banners: only over the days they actually cover (§7)
    y_band = HEAD_H + 2
    for i, day in enumerate(days):
        yy = y_band
        for banner in view.banners:
            if banner.start_day <= day <= banner.end_day:
                txt = _fit(d, _banner_label(banner), f_small, DAY_W - 16)
                d.rectangle([i * DAY_W + 4, yy, (i + 1) * DAY_W - 4, yy + 20],
                            fill=COLORS["absence"])
                d.text((i * DAY_W + 8, yy + 2), txt, font=f_small, fill=INK)
                yy += 24
    y_body = y_band + 28 + (24 * max((b.end_day - b.start_day).days + 1
                                     for b in view.banners) if view.banners else 0)
    # shared group event cards (titles are safe: group events)
    yy = y_body
    for c in shared:
        prefix = f"{cal.WEEKDAYS_DE[c.start.weekday()]} "
        txt = f"{prefix}{c.start:%H:%M} {c.title}"
        d.rectangle([MARGIN, yy, W - MARGIN, yy + 22], fill="#dcecdc")
        d.text((MARGIN + 6, yy + 2), _fit(d, txt, f_small, W - 2 * MARGIN - 12),
               font=f_small, fill=INK)
        yy += 28
    # one lane per person (private: the actor's single lane)
    y0 = y_body + shared_h + 4
    for li, lane in enumerate(lanes):
        y = y0 + li * lane_h
        d.rectangle([MARGIN, y + 6, MARGIN + 14, y + lane_h - 8], fill=lane.color)
        d.text((MARGIN + 20, y + 8), _fit(d, lane.name, f_small, 92),
               font=f_small, fill=INK)
        for b in lane.blocks:
            for day in days:
                label = _block_label(b, day)
                if not label:
                    continue
                ix = (day - days[0]).days
                x = ix * DAY_W + 6
                d.rectangle([x, y + 6, x + DAY_W - 12, y + lane_h - 8],
                            fill=lane.color, outline="white")
                d.text((x + 3, y + 8),
                       _fit(d, label, f_small, DAY_W - 18), font=f_small,
                       fill="white")
    if view.group:
        d.text((MARGIN, H - 26), "gemeinsam frei: siehe /today", font=f_small,
               fill=MUTED)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_day_view(view) -> bytes:
    """Day from a DayView (plan §21/§23): banner header, shared cards, lanes with
    masked busy, then free windows. Private context renders the actor's own
    timed segments as rows."""
    n_rows = sum(len(l.blocks) for l in view.lanes) + len(view.shared)
    H = 120 + 30 * n_rows + 34 * max(1, len(view.banners)) + 50
    img = Image.new("RGB", (W, max(H, 260)), "white")
    d = ImageDraw.Draw(img)
    f_title, f_body, f_small = _font(30), _font(19), _font(14)
    title = ("Gruppentag" if view.group else
             ("Heute" if view.day == cal.now().date()
              else f"{cal.WEEKDAYS_DE[view.day.weekday()]} {view.day:%d.%m.%Y}"))
    d.text((MARGIN, 14), title, font=f_title, fill=INK)
    yy = 60
    for banner in view.banners:  # header layer first (§7)
        d.rectangle([MARGIN, yy, W - MARGIN, yy + 24], fill=COLORS["absence"])
        d.text((MARGIN + 8, yy + 2), _fit(d, _banner_label(banner), f_body,
                                          W - 2 * MARGIN - 16),
               font=f_body, fill=INK)
        yy += 30
    for c in view.shared:
        d.rectangle([MARGIN, yy, W - MARGIN, yy + 24], fill="#dcecdc")
        d.text((MARGIN + 8, yy + 2),
               _fit(d, f"{c.start:%H:%M}–{c.end:%H:%M} {c.title}", f_body,
                    W - 2 * MARGIN - 16), font=f_body, fill=INK)
        yy += 30
    for lane in view.lanes:
        for b in lane.blocks:
            label = _block_label(b, view.day)
            if not label:
                continue
            color = lane.color if (b.title or b.shared) else "#c9c9c9"
            d.rectangle([MARGIN, yy, W - MARGIN, yy + 24], fill=color)
            d.text((MARGIN + 8, yy + 2),
                   _fit(d, f"{lane.name}: {label}" if view.group else label,
                        f_body, W - 2 * MARGIN - 16), font=f_body, fill=INK)
            yy += 30
    free = ", ".join(f"{s:%H:%M}–{e:%H:%M}" for s, e in view.free[:6])
    prefix = "gemeinsam frei" if view.group else "frei"
    d.text((MARGIN, yy + 8), f"{prefix}: {free or 'keine'}", font=f_body,
           fill=MUTED)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
