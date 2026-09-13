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
                d.text((i * DAY_W + 8, yy + 2), text, font=f_small, fill=INK)
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
            d.text((i * DAY_W + 8, yy + 2), f"{e.start:%H:%M} {e.title}",
                   font=f_body, fill=INK)
            yy += 32
    for i in range(1, 7):
        d.line([(i * DAY_W, TITLE_H), (i * DAY_W, H)], fill="#eeeeee", width=1)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_availability_png(store, people: list[str],
                            day: date | None = None) -> bytes:
    """Availability widget (plan §26): one 30-min busy/free bar (07:00-21:00)
    per person plus the computed common free slots. No model logic."""
    day = day or cal.now().date()
    names = sorted(set(people)) if people and "all" not in people \
        else sorted(set(store.participant_names()) or {"Ich"})
    bar_w, row_h, pad, label_w = 27, 40, 20, 140
    Wv = pad + label_w + 28 * bar_w + pad
    Hv = row_h + row_h * (len(names) + 1) + 70
    img = Image.new("RGB", (Wv, Hv), "white")
    d = ImageDraw.Draw(img)
    f_head, f_body = _font(22), _font(18)
    slots = cal.find_free_slots(store, names, day, day, 60)

    d.text((pad, 8), f"{cal.WEEKDAYS_DE[day.weekday()]} {day:%d.%m.%Y}",
           font=f_head, fill=INK)
    for j, h in enumerate(range(7, 22, 3)):
        x = pad + label_w + j * 6 * bar_w
        d.text((x, row_h), f"{h:02d}", font=f_body, fill=MUTED)
    for i, p in enumerate(names):
        y = row_h + 26 + i * row_h
        d.text((pad, y), p, font=f_body, fill=INK)
        busy = cal.busy_intervals(store, [p], datetime.combine(day, time(7, 0)),
                                  datetime.combine(day, time(21, 0)))
        for j in range(28):
            slot = datetime.combine(day, time(7, 0)) + timedelta(minutes=30 * j)
            slot_end = slot + timedelta(minutes=30)
            hit = any(s < slot_end and slot < e for s, e in busy)
            x = pad + label_w + j * bar_w
            d.rectangle([x, y + 4, x + bar_w - 4, y + row_h - 12],
                        fill="#5a7d9a" if hit else "#e8e8e8")
    y_free = row_h + 26 + len(names) * row_h
    d.text((pad, y_free), "frei:", font=f_body, fill=INK)
    d.text((pad + 60, y_free),
           ", ".join(f"{s:%H:%M}–{e:%H:%M}" for s, e in slots)
           or "keine gemeinsamen Slots (07-21 Uhr)",
           font=f_body, fill=INK)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
