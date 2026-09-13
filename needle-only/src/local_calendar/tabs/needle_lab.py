"""Tab 4 — Needle Lab: complete/run/extract isolated, toolsets, schemas, presets."""

from __future__ import annotations

import time

import gradio as gr
import needle as needle_lib

from .. import calendar as cal
from ..agent import (EventExpression, Gemma, ParticipantExpression,
                     TemporalExpression, build_tools, system_facts)

ALL_TOOLS = ["calendar_list", "calendar_find_slot", "calendar_create",
             "calendar_move", "calendar_delete"]
EXTRACT_SCHEMAS = {"TemporalExpression": TemporalExpression,
                   "ParticipantExpression": ParticipantExpression,
                   "EventExpression": EventExpression}
PRESETS = {
    "Create appointment": "Create an appointment tomorrow at 14:00.",
    "Move event": "Move my dentist appointment to Friday.",
    "Delete event": "Delete my dentist appointment.",
    "Vacation range": "I am on vacation from August 3 to August 18.",
    "Find common slot": "When are Lisa and Max free tomorrow afternoon?",
    "Tomorrow afternoon": "Termin morgen Nachmittag Zahnarzt",
    "German ambiguous": "Schieb das Meeting mit Lisa lieber auf übermorgen nach dem Mittag",
    "English canonical": "List my appointments next week.",
}


class Lab:
    """Reusable Needle sessions per toolset; production path stays separate."""

    def __init__(self, store: cal.CalendarStore):
        self.store = store
        self.tools = build_tools(store)
        self.gemma = Gemma()
        self._sessions: dict = {}

    def session(self, names: tuple[str, ...], use_facts: bool) -> needle_lib.Needle:
        key = (names, use_facts)
        if key not in self._sessions:
            self._sessions[key] = needle_lib.Needle(
                tools=[self.tools[n] for n in names],
                system=system_facts() if use_facts else None)
        return self._sessions[key]

    def run(self, mode: str, names: list[str], schema_name: str,
            text: str, use_facts: bool, canonicalize: bool) -> dict:
        t0 = time.perf_counter()
        out: dict = {"mode": mode, "input": text}
        if canonicalize:
            if self.gemma.available():
                out["canonical"] = self.gemma.canonicalize(text)
                text = out["canonical"]
            else:
                out["canonical_error"] = self.gemma.error
        if mode == "extract":
            schema = EXTRACT_SCHEMAS.get(schema_name, TemporalExpression)
            got = needle_lib.extract(text, schema, system=system_facts() if use_facts else None,
                                     strict=False)
            out["schema"] = schema_name
            out["extracted"] = got.model_dump() if hasattr(got, "model_dump") else got
            out["validation"] = {"type": type(got).__name__,
                                 "ok": got is not None}
        else:
            session = self.session(tuple(sorted(set(names or []))), use_facts)
            resp = session.run(text) if mode == "run" else session.complete(text)
            out["raw_response"] = resp
            calls = resp.get("function_calls") or []
            out["validation"] = {"tool": (calls[0]["name"] if calls else None),
                                 "confidence": resp.get("confidence"),
                                 "resolved": self._resolve(calls)}
        out["lab_latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return out

    def _resolve(self, calls: list) -> dict:
        if not calls:
            return {}
        args = calls[0].get("arguments") or {}
        return {"timing": cal.resolve_timing(
                    str(args.get("date", "")),
                    str(args.get("time", "")),
                    str(args.get("until", "")),
                    cal.DEFAULT_DURATION_MIN),
                "participants": cal.parse_persons(args.get("participants", ""))}

    def schemas(self, names: list[str]) -> dict:
        """The real compiled schemas of the selected tools (plan §38)."""
        return {n: self.tools[n]._needle_tool for n in names or []}


def build_needle_lab_tab(agent) -> None:
    lab = Lab(agent.store)
    with gr.Tab("Needle Lab"):
        gr.Markdown("### Needle isoliert testen — Schemas, Toolsets, Modi")
        with gr.Row():
            with gr.Column():
                tools_box = gr.CheckboxGroup(choices=ALL_TOOLS, value=ALL_TOOLS,
                                             label="Toolset")
                loaded_md = gr.Markdown()
                mode_radio = gr.Radio(["complete", "run", "extract"],
                                      value="complete", label="Mode")
                schema_dd = gr.Dropdown(choices=list(EXTRACT_SCHEMAS),
                                        value="TemporalExpression",
                                        label="Extract-Schema", visible=False)
                facts_chk = gr.Checkbox(value=True, label="System Facts mitsenden")
                canon_chk = gr.Checkbox(value=False,
                                        label="Gemma zuerst normalisieren (hybrid)")
            with gr.Column():
                schemas_json = gr.JSON(label="Aktive Schemas (real)")
        inp = gr.Textbox(label="Input", lines=2,
                         value="Create an appointment tomorrow at 14:00.")
        with gr.Row():
            run_btn = gr.Button("Run", variant="primary")
        for i, label in enumerate(PRESETS):
            gr.Button(label, size="sm").click(
                lambda v=list(PRESETS.values())[i]: v, None, inp)
        resp_json = gr.JSON(label="Response")

        def _loaded(names):
            return gr.update(value=f"**Loaded tools: {len(names or [])}** · "
                                   f"{', '.join(names or [])}")

        def _schemas(names):
            return lab.schemas(names)

        def _mode_change(mode):
            return gr.update(visible=mode == "extract")

        def _run(mode, names, schema_name, text, use_facts, canon):
            try:
                return lab.run(mode, names, schema_name, text, use_facts, canon)
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"}

        tools_box.change(_loaded, tools_box, loaded_md)
        tools_box.change(_schemas, tools_box, schemas_json)
        mode_radio.change(_mode_change, mode_radio, schema_dd)
        run_btn.click(_run,
                      [mode_radio, tools_box, schema_dd, inp, facts_chk, canon_chk],
                      resp_json)
