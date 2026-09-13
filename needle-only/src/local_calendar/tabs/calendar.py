"""Tab 2 — Calendar: week grid (30 min), participant filter, busy/free bars, table."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import gradio as gr

from .. import calendar as cal

GRID_START, GRID_END = time(7, 0), time(20, 0)
SLOT_MIN = 30
COLORS = {"appointment": "#cfe8ff", "absence": "#ffd9a8"}


def week_days(offset: int) -> list[date]:
    monday = cal.now().date() - timedelta(days=cal.now().weekday())
    monday += timedelta(weeks=offset)
    return [monday + timedelta(days=i) for i in range(7)]


def _in_view(events: list, days: list[date], people: list[str]):
    start = datetime.combine(days[0], time(0, 0))
    end = datetime.combine(days[-1] + timedelta(days=1), time(0, 0))
    picked = [e for e in events if e.start < end and e.end > start]
    if people and "all" not in people:
        picked = [e for e in picked if set(e.participants) & set(people)]
    return picked


def grid_html(store, days: list[date], people: list[str]) -> str:
    events = _in_view(store.events_between(
        datetime.combine(days[0], time(0, 0)),
        datetime.combine(days[-1] + timedelta(days=1), time(0, 0))), days, people)
    allday = [e for e in events if e.all_day]
    timed = [e for e in events if not e.all_day]
    n_slots = ((GRID_END.hour * 60 + GRID_END.minute)
               - (GRID_START.hour * 60 + GRID_START.minute)) // SLOT_MIN
    head = "".join(f"<th>{cal.WEEKDAYS_DE[d.weekday()]} {d:%d.%m.}</th>" for d in days)
    ad_cells = []
    for d in days:
        titles = [e.title for e in allday if e.start.date() <= d < e.end.date()]
        ad_cells.append(f"<td style='background:{COLORS['absence']};"
                        f"font-size:11px'>{'<br>'.join(titles)}</td>")
    rows = [f"<tr><th>ganztägig</th>{''.join(ad_cells)}</tr>"]
    for i in range(n_slots):
        t = (datetime.combine(date(2000, 1, 1), GRID_START)
             + timedelta(minutes=SLOT_MIN * i)).time()
        cells = []
        for d in days:
            content, bg = "", ""
            for e in timed:
                slot_start = datetime.combine(d, t)
                slot_end = slot_start + timedelta(minutes=SLOT_MIN)
                if e.start < slot_end and e.end > slot_start:  # half-open overlap
                    bg = f"background:{COLORS.get(e.kind, '#ddd')};"
                    if e.start < slot_end and slot_start <= e.start:
                        content = e.title
                    break
            cells.append(f"<td style='{bg}font-size:11px'>{content}</td>")
        rows.append(f"<tr><th>{t.strftime('%H:%M')}</th>{''.join(cells)}</tr>")
    return ("<style>.cal td,.cal th{border:1px solid #ccc;padding:2px 5px;}"
            ".cal{border-collapse:collapse;width:100%}</style>"
            "<table class='cal'>"
            f"<tr><th>Zeit</th>{head}</tr>{''.join(rows)}</table>")


def availability_html(store, people: list[str],
                      first_day: date | None = None) -> str:
    """One line per participant: 7 days x 30-min slots (07:00-21:00), █=busy ░=free."""
    if people and "all" not in people:
        names = sorted(set(people))
    else:
        names = sorted(set(store.participant_names()) or {"Ich"})
    day0 = datetime.combine(first_day or cal.now().date(), time(0, 0))
    n_slots = (21 - 7) * 60 // SLOT_MIN
    lines = ["<div style='font-family:monospace;font-size:12px'>"
             f"<b>Beschäftigt/Frei ab {day0:%d.%m.} (7 Tage, 30-min-Raster, 07–21 Uhr)</b>"]
    for p in names:
        bar = []
        for d in range(7):
            day_start = day0 + timedelta(days=d)
            busy = cal.busy_intervals(store, [p], day_start,
                                      day_start + timedelta(days=1))
            for i in range(n_slots):
                slot = day_start + timedelta(minutes=7 * 60 + SLOT_MIN * i)
                slot_end = slot + timedelta(minutes=SLOT_MIN)
                hit = any(s < slot_end and slot < e for s, e in busy)
                bar.append("█" if hit else "░")
            bar.append("|")
        lines.append(f"<b>{p:<10}</b> {''.join(bar)}")
    lines.append("</div>")
    return "".join(lines)


def event_table(store, people: list[str]) -> list[list]:
    events = store.events_between(cal.now() - timedelta(days=7),
                                  cal.now() + timedelta(days=60))
    if people and "all" not in people:
        events = [e for e in events if set(e.participants) & set(people)]
    return [[f"{e.start:%d.%m. %H:%M}", f"{e.end:%d.%m. %H:%M}",
             "ja" if e.all_day else "nein", e.title,
             ", ".join(e.participants)] for e in events]


def build_calendar_tab(agent) -> None:
    with gr.Tab("Calendar") as tab:
        gr.Markdown("### Kalender (Teilnehmer-Ebene)")
        with gr.Row():
            prev_btn = gr.Button("◀ Woche")
            today_btn = gr.Button("Heute")
            next_btn = gr.Button("Woche ▶")
            refresh_btn = gr.Button("Refresh")
        people_box = gr.CheckboxGroup(choices=["all", "Ich"], value=["all"],
                                      label="Teilnehmer")
        week_view = gr.HTML(grid_html(agent.store, week_days(0), ["all"]))
        avail_view = gr.HTML(availability_html(agent.store, ["all"]))
        table = gr.Dataframe(value=event_table(agent.store, ["all"]),
                             headers=["start", "end", "all_day", "title",
                                      "participants"], interactive=False)
        offset_state = gr.State(0)

        def _people_choices() -> dict:
            names = ["all", "Ich"] + [p for p in agent.store.participant_names()
                                      if p != "Ich"]
            return gr.update(choices=names)

        def _render(offset, people):
            days = week_days(int(offset or 0))
            return (grid_html(agent.store, days, people),
                    availability_html(agent.store, people, days[0]),
                    event_table(agent.store, people))

        prev_btn.click(lambda o: int(o) - 1, offset_state, offset_state)
        next_btn.click(lambda o: int(o) + 1, offset_state, offset_state)
        today_btn.click(lambda: 0, None, offset_state)
        refresh_btn.click(_render, [offset_state, people_box],
                          [week_view, avail_view, table])
        refresh_btn.click(_people_choices, None, people_box)
        offset_state.change(_render, [offset_state, people_box],
                            [week_view, avail_view, table])
        people_box.change(_render, [offset_state, people_box],
                          [week_view, avail_view, table])
        tab.select(_render, [offset_state, people_box],
                   [week_view, avail_view, table])
        tab.select(_people_choices, None, people_box)
