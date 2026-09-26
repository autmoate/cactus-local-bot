"""Gradio test surface for the Thunderbird calendar spike (§12-§16).

This is deliberately NOT a ChatInterface: it is a workflow/debug app that mirrors
the later Thunderbird flow. It only wires UI events to `workflow.CalendarSpike`;
all business logic stays outside. Mail content in manual mode is kept in RAM only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gradio as gr  # noqa: E402

from extractor import LocalNeedleHost  # noqa: E402
from models import EventCandidate, InvitationDraft, MailMessage, ParticipantCandidate  # noqa: E402
from workflow import CalendarSpike, to_ics  # noqa: E402

MY_ADDRESSES = ("me@example.org",)


# --------------------------------------------------------------- parsing


def _split_lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _message_from_fields(subject, sender, to, cc, received_at, body) -> MailMessage:
    try:
        ref = datetime.fromisoformat((received_at or "").strip())
    except ValueError:
        ref = datetime.now()
    return MailMessage(id="manual", subject=subject or "",
                       sender=(_split_lines(sender) or [""])[0],
                       to=_split_lines(to), cc=_split_lines(cc),
                       received_at=ref, body_text=body or "")


def _fmt_candidate(cand: EventCandidate | None):
    if cand is None:
        return ("", "", "", "", False, "")
    d = cand.start.date().isoformat() if cand.start else ""
    s = cand.start.strftime("%H:%M") if cand.start and not cand.all_day else ""
    e = cand.end.strftime("%H:%M") if cand.end and not cand.all_day else ""
    return (cand.title, d, s, e, cand.all_day, cand.location)


def _cand_from_fields(title, d, start, end, all_day, location, src) -> EventCandidate:
    start_dt = end_dt = None
    try:
        day = date.fromisoformat(d)
    except (ValueError, TypeError):
        day = None
    if day:
        start_dt = datetime.combine(
            day, time(0, 0) if all_day else time.fromisoformat(start or "00:00"))
        if all_day:
            end_dt = datetime.combine(day, time(0, 0))
        elif end:
            end_dt = datetime.combine(day, time.fromisoformat(end))
    return EventCandidate(
        title=title or "", start=start_dt, end=end_dt, all_day=bool(all_day),
        location=location or "", source_message_id=src.get("source_message_id", ""),
        source_text=src.get("source_text", ""),
        extraction_mode=src.get("extraction_mode", "message"),
        model=src.get("model", ""), confidence=src.get("confidence"))


def _fmt_when(cand: EventCandidate) -> str:
    if not cand.start or not cand.end:
        return "unvollständig"
    if cand.all_day:
        return f"{cand.start.date().isoformat()} – {cand.end.date().isoformat()} (ganztägig)"
    return f"{cand.start:%d.%m.%Y %H:%M} – {cand.end:%H:%M}"


def _status_md(result: dict) -> str:
    status = result.get("status")
    if status == "none":
        return ("📭 Kein eindeutiger Termin erkannt.\n"
                "Markiere den relevanten Satz und versuche es erneut.")
    if status == "multiple":
        return ("📅 Mehrere mögliche Termine erkannt.\n"
                "Wähle einen aus oder markiere den gewünschten Text.")
    if status in ("error", "invalid"):
        return f"⚠️ Fehler: {result.get('error') or status}"
    cands = result.get("candidates") or []
    if cands and cands[0].get("status") == "incomplete":
        return ("⚠️ Termin erkannt, Zeitpunkt unvollständig.\n"
                "Bitte Datum/Uhrzeit im Preview ergänzen.")
    return f"✅ Termin erkannt ({len(cands)}). Bitte prüfen und bestätigen."


def _choices(participants) -> list[tuple[str, str]]:
    return [(f"{p['name']} <{p['email']}>  ({p['source']})", p["email"])
            for p in participants]


def _trace_md(trace: list[dict]) -> str:
    if not trace:
        return "_noch keine Runs_"
    lines = ["| # | id | mode | status | lat | candidates |",
             "|---|---|---|---|---|---|"]
    for i, t in enumerate(trace[-10:], 1):
        titles = "; ".join(c.get("title", "") for c in t.get("candidates", []))
        lines.append(f"| {i} | {t.get('id')} | {t.get('mode')} | "
                     f"{t.get('status')} | {t.get('latency_ms', 0):.0f}ms | {titles} |")
    return "\n".join(lines)


def build_app(backend: str) -> gr.Blocks:
    host = LocalNeedleHost(backend)
    spike = CalendarSpike(host, my_addresses=MY_ADDRESSES)
    cases = [json.loads(ln) for ln in (HERE / "cases.jsonl").read_text(
        encoding="utf-8").splitlines() if ln.strip()]
    cases_by_id = {c["id"]: c for c in cases}

    def rows() -> list[list]:
        return spike.store.rows()

    with gr.Blocks(title="Thunderbird → Calendar Spike") as app:
        gr.Markdown(f"# 📧 Thunderbird → Local Calendar — Feasibility Spike  \n"
                    f"Backend: **{backend}** ({host.model}) — kein echter Write, "
                    f"kein Versand, Human Approval Pflicht.")
        result_state = gr.State({})
        draft_state = gr.State({})
        trace_state = gr.State([])

        with gr.Tabs():
            # ---------------------------------------------------------- Tab 1
            with gr.Tab("1 · Mail"):
                with gr.Row():
                    example = gr.Dropdown(choices=list(cases_by_id), label="Fixture",
                                          value="e01", scale=3)
                    load_btn = gr.Button("Beispiel laden", scale=1)
                with gr.Row():
                    subject = gr.Textbox(label="Subject", scale=2)
                    sender = gr.Textbox(label="From", scale=2)
                    received = gr.Textbox(label="Received at (ISO)", scale=1)
                with gr.Row():
                    to = gr.Textbox(label="To (eine pro Zeile)", lines=2)
                    cc = gr.Textbox(label="Cc (eine pro Zeile)", lines=2)
                body = gr.Textbox(label="Mail body", lines=8)
                selection = gr.Textbox(label="Markierter Text (Selection Mode)", lines=3)
                with gr.Row():
                    recog_msg = gr.Button("📌 Termin aus Mail erkennen", variant="primary")
                    recog_sel = gr.Button("✂️ Markierung als Termin erkennen")
                    reset_btn = gr.Button("Zurücksetzen")
                status = gr.Markdown()
                with gr.Accordion("Debug (Raw function calls)", open=False):
                    debug = gr.JSON(label="Extraction response")

            # ---------------------------------------------------------- Tab 2
            with gr.Tab("2 · Candidate / Approval"):
                picker = gr.Dropdown(choices=[], label="Erkannte Termine (bei mehreren)")
                with gr.Row():
                    c_title = gr.Textbox(label="Titel")
                    c_date = gr.Textbox(label="Datum (YYYY-MM-DD)")
                with gr.Row():
                    c_start = gr.Textbox(label="Beginn (HH:MM)")
                    c_end = gr.Textbox(label="Ende (HH:MM)")
                    c_allday = gr.Checkbox(label="Ganztägig")
                c_location = gr.Textbox(label="Ort")
                participants = gr.CheckboxGroup(choices=[],
                                                label="Teilnehmer (aus From/To/Cc)")
                with gr.Row():
                    commit_btn = gr.Button("📅 Simuliert eintragen", variant="primary")
                    invite_btn = gr.Button("✉️ Einladung vorbereiten")
                    discard_btn = gr.Button("Verwerfen")
                approve_note = gr.Markdown()
                invite_preview = gr.Markdown()
                ics_preview = gr.Code(label=".ics Preview", language="markdown",
                                      visible=False)
                with gr.Row():
                    send_btn = gr.Button("📤 Simuliert senden")
                    outbox_note = gr.Markdown()

            # ---------------------------------------------------------- Tab 3
            with gr.Tab("3 · Simulated Calendar"):
                table = gr.Dataframe(headers=["Datum", "Zeitraum", "Titel", "Ort",
                                              "Teilnehmer"], interactive=False,
                                     wrap=True)
                refresh = gr.Button("Aktualisieren")

            # ---------------------------------------------------------- Tab 4
            with gr.Tab("4 · Eval / Trace"):
                with gr.Row():
                    eval_backend = gr.Dropdown(choices=["n2", "n3"], value=backend,
                                               label="Backend")
                    eval_limit = gr.Number(value=10, label="Limit", precision=0)
                    eval_btn = gr.Button("Fixture-Suite ausführen", variant="primary")
                eval_out = gr.Markdown()
                trace_view = gr.Markdown("_noch keine Runs_")

        # -------------------------------------------------------------- wiring
        def load_example(case_id):
            case = cases_by_id.get(case_id)
            if not case:
                return ("", "", "", "", "", "", "", {}, gr.update(choices=[], value=[]),
                        gr.update(choices=[], value=None), "", {})
            m = case["message"]
            return (m.get("subject", ""), "\n".join(m.get("from") or []),
                    "\n".join(m.get("to") or []), "\n".join(m.get("cc") or []),
                    m.get("received_at", ""), m.get("body", ""),
                    case.get("selection", ""), {}, gr.update(choices=[], value=[]),
                    gr.update(choices=[], value=None), "", {})

        load_btn.click(
            load_example, [example],
            [subject, sender, to, cc, received, body, selection, result_state,
             participants, picker, status, debug])

        def recognize(subject_v, sender_v, to_v, cc_v, received_v, body_v, sel_v,
                      mode, trace):
            message = _message_from_fields(subject_v, sender_v, to_v, cc_v,
                                           received_v, body_v)
            extraction = spike.extract(message, mode=mode, selection=sel_v)
            cands = [c.to_dict() for c in extraction["candidates"]]
            parts = [p.__dict__ for p in spike.participants(message)]
            result = {"status": extraction["status"],
                      "error": extraction.get("error", ""),
                      "latency_ms": extraction.get("latency_ms", 0),
                      "confidence": extraction.get("confidence"),
                      "candidates": cands, "participants": parts,
                      "message": message.to_dict(),
                      "raw": extraction.get("raw", {})}
            choices = [(f"{i}: {c['title']} – {c['start']} – "
                        f"{c['location'] or '–'}", str(i))
                       for i, c in enumerate(cands)]
            pick = gr.update(choices=choices,
                             value=str(0) if choices else None)
            form = _fmt_candidate(EventCandidate.from_dict(cands[0]) if cands
                                  else None)
            trace = (trace or []) + [{
                "id": message.id, "mode": mode, "backend": backend,
                "status": result["status"], "latency_ms": result["latency_ms"],
                "raw_calls": (result["raw"] or {}).get("raw", []),
                "candidates": cands}]
            return (result, _status_md(result), pick, *form,
                    gr.update(choices=_choices(parts), value=[]), trace,
                    (result["raw"] or {}), _trace_md(trace))

        recog_outputs = [result_state, status, picker, c_title, c_date, c_start,
                         c_end, c_allday, c_location, participants, trace_state,
                         debug, trace_view]
        recog_msg.click(
            lambda s, se, t, c, r, b, sel, tr: recognize(s, se, t, c, r, b, sel,
                                                         "message", tr),
            [subject, sender, to, cc, received, body, selection, trace_state],
            recog_outputs)
        recog_sel.click(
            lambda s, se, t, c, r, b, sel, tr: recognize(s, se, t, c, r, b, sel,
                                                         "selection", tr),
            [subject, sender, to, cc, received, body, selection, trace_state],
            recog_outputs)

        def pick_candidate(index, result):
            cands = (result or {}).get("candidates") or []
            if not cands:
                return _fmt_candidate(None)
            try:
                chosen = cands[int(index)]
            except (ValueError, IndexError, TypeError):
                chosen = cands[0]
            return _fmt_candidate(EventCandidate.from_dict(chosen))

        picker.change(pick_candidate, [picker, result_state],
                      [c_title, c_date, c_start, c_end, c_allday, c_location])

        def _invitees(result, selected):
            parts = (result or {}).get("participants") or []
            return [ParticipantCandidate(**p) for p in parts
                    if p["email"] in (selected or [])]

        def _source(result):
            cands = (result or {}).get("candidates") or []
            return cands[0] if cands else {}

        def commit(title, d, start, end, allday, location, result, selected):
            if not (result or {}).get("candidates"):
                return rows(), "Kein Candidate — erst Termin erkennen."
            cand = _cand_from_fields(title, d, start, end, allday, location,
                                     _source(result))
            spike.commit(cand, _invitees(result, selected))
            return rows(), f"✅ **{title}** im simulierten Kalender eingetragen."

        commit_btn.click(commit,
                         [c_title, c_date, c_start, c_end, c_allday, c_location,
                          result_state, participants],
                         [table, approve_note])

        def prepare(title, d, start, end, allday, location, result, selected):
            src = {**_source(result), "source_message_id": "manual"}
            cand = _cand_from_fields(title, d, start, end, allday, location, src)
            invitees = _invitees(result, selected)
            draft = spike.prepare_invitation(cand, invitees)
            lines = [f"**Termin:** {cand.title}", f"**Zeit:** {_fmt_when(cand)}",
                     f"**Ort:** {cand.location or '–'}", "", "**Einzuladen:**"]
            lines += [f"- {p.name} <{p.email}>" for p in invitees] or ["- (niemand)"]
            ics = to_ics(draft)
            return (draft.to_dict(), "\n".join(lines),
                    gr.update(value=ics, visible=bool(ics)), "")

        invite_btn.click(prepare,
                         [c_title, c_date, c_start, c_end, c_allday, c_location,
                          result_state, participants],
                         [draft_state, invite_preview, ics_preview, outbox_note])

        def send(draft):
            if not draft:
                return "Keine Einladung vorbereitet — erst 'Einladung vorbereiten'."
            invitees = [ParticipantCandidate(**p) for p in draft.get("invitees", [])]
            spike.send_invitation(InvitationDraft(
                event=EventCandidate.from_dict(draft["event"]), invitees=invitees,
                organizer=draft.get("organizer", "")))
            return (f"📨 Simuliert gesendet. Outbox: **{len(spike.outbox.sent)}** "
                    f"(kein SMTP, kein Compose).")

        send_btn.click(send, [draft_state], [outbox_note])

        discard_btn.click(lambda: (*_fmt_candidate(None), {}, "Verworfen."), None,
                          [c_title, c_date, c_start, c_end, c_allday, c_location,
                           draft_state, approve_note])

        def run_eval(be, limit):
            import eval as ev
            report = ev.run_backend(be, ev.load_cases(ev.CASE_FILE), None, None,
                                    int(limit) if limit else None)
            lines = [f"### {be} — {report.get('model', '?')}"]
            for mode, m in report.get("groups", {}).items():
                if m.get("n"):
                    lines.append(
                        f"- **{mode}**: n={m['n']} final={m['final_event_ok']:.2f} "
                        f"approval={m['approval_ready']:.2f} "
                        f"detect={m.get('event_detection_recall', 0):.2f} "
                        f"fp={m.get('false_positive_rate', 0):.2f} "
                        f"p50={m.get('latency_p50_ms', '?')}ms")
            return "\n".join(lines)

        eval_btn.click(run_eval, [eval_backend, eval_limit], [eval_out])

        def reset_all():
            return ("", "", "", "", "", "", "", {}, gr.update(choices=[], value=[]),
                    gr.update(choices=[], value=None), "", {}, [])

        reset_btn.click(reset_all, None,
                        [subject, sender, to, cc, received, body, selection,
                         result_state, participants, picker, status, debug,
                         trace_state])
        refresh.click(rows, None, [table])
    return app


def main() -> None:
    ap = argparse.ArgumentParser(prog="tb-calendar-spike")
    ap.add_argument("--backend", choices=["n2", "n3"], default="n2")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7861)
    ap.add_argument("--share", action="store_true")
    args = ap.parse_args()
    build_app(args.backend).launch(server_name=args.host, server_port=args.port,
                                   share=args.share)


if __name__ == "__main__":
    main()
