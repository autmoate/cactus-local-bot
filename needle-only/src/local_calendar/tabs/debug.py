"""Tab 3 — Detailed Trace / Debug: the last 10 request traces in full detail."""

from __future__ import annotations

import gradio as gr


def _options(agent) -> list:
    traces = list(agent.history)
    return gr.update(choices=[(f"{t['input'][:60]}  ({t.get('total_ms')} ms)", i)
                              for i, t in enumerate(traces)])


def _show(index, agent) -> tuple:
    traces = list(agent.history)
    if index is None or int(index) >= len(traces):
        return "Kein Trace gewählt.", {}
    t = traces[int(index)]
    lines = [f"**Input:** {t['input']}", f"**Modus:** {t.get('mode')}"]
    if t.get("canonical"):
        lines.append(f"**Canonical Instruction:** {t['canonical']}")
    lines.append(f"**Confidence:** {t.get('confidence')} · "
                 f"**Gesamt:** {t.get('total_ms')} ms · RAM: {t.get('ram_mb')} MB · "
                 f"**Ausgeführt:** {t.get('executed')}")
    lines.append("**Steps:**")
    for s in t.get("steps") or []:
        err = f" — ❌ {s['error']}" if s.get("error") else ""
        out = ""
        o = s.get("output")
        if isinstance(o, dict) and o.get("message"):
            out = f" · {str(o['message'])[:100]}"
        elif o is not None:
            out = f" · {str(o)[:100]}"
        lines.append(f"- `{s['name']}` ({s['latency_ms']} ms){err}{out}")
    return "\n".join(lines), t


def build_debug_tab(agent) -> None:
    with gr.Tab("Trace / Debug") as tab:
        gr.Markdown("### Vollständiger Request-Trace (letzte 10)")
        with gr.Row():
            sel = gr.Dropdown(label="Request", choices=[], interactive=True)
            refresh_btn = gr.Button("Refresh")
        trace_md = gr.Markdown()
        trace_json = gr.JSON(label="Roh-Trace")
        refresh_btn.click(lambda: _options(agent), None, sel)
        tab.select(lambda: _options(agent), None, sel)
        sel.change(lambda i: _show(i, agent), sel, [trace_md, trace_json])
