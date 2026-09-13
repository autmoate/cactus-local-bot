"""Tab 1 — Assistant: request input, live pipeline status, compact trace, result."""

from __future__ import annotations

import gradio as gr

STAGES = [("gemma_normalize", "Gemma-Normalisierung"),
          ("needle_complete", "Needle complete"),
          ("resolve", "Auflösung (Zeit/Teilnehmer)"),
          ("execute", "Ausführung")]


def _stage_of(step_name: str) -> str:
    for key, _ in STAGES:
        if step_name == key or step_name.startswith(key):
            return key
    return step_name


def status_text(trace: dict) -> str:
    steps = trace.get("steps") or []
    if not steps:
        return "Bereit."
    last = steps[-1]
    state = "❌ " + last["error"] if last.get("error") else "…"
    return (f"Schritt {len(steps)} · **{last['name']}** · {state} "
            f"({last.get('latency_ms', 0)} ms)")


def pipeline_text(trace: dict) -> str:
    done = {_stage_of(s["name"]) for s in trace.get("steps") or []}
    lines = []
    for key, label in STAGES:
        if key in done:
            lines.append(f"✓ {label}")
        else:
            lines.append(f"○ {label}")
    if trace.get("done"):
        verdict = "✅ ERFOLGREICH AUSGEFÜHRT" if trace.get("executed") \
            else "❌ NICHT AUSGEFÜHRT"
        lines.append(f"\n### {verdict} ({trace.get('total_ms')} ms)")
    return "\n".join(lines)


def iterations_text(trace: dict) -> str:
    """Phase 14: one readable block per agent iteration — Gemma's decision,
    the instruction given to Needle, and what Needle did."""
    if not trace.get("steps"):
        return ""
    lines = []
    step_no = 0
    for s in trace.get("steps") or []:
        name = s["name"]
        if name.startswith("controller"):
            step_no += 1
            d = s.get("output") or {}
            if hasattr(d, "model_dump"):  # ControllerDecision
                d = d.model_dump()
            action = (d.get("action") if isinstance(d, dict) else None) or "—"
            detail = (d.get("instruction") if isinstance(d, dict) else "") \
                or (d.get("message") if isinstance(d, dict) else "") or ""
            lines.append(f"**STEP {step_no} — Gemma: {action}**"
                         + (f"\n> {detail}" if detail else ""))
        elif name == "needle_complete":
            calls = (s.get("output") or {}).get("function_calls") or []
            tools = ", ".join(c.get("name", "?") for c in calls) or "—"
            lines.append(f"  · Needle: {tools or 'kein Call (Refusal)'}")
        elif name.startswith("execute"):
            out = s.get("output") or {}
            msg = (out.get("message") if isinstance(out, dict) else "") or ""
            for line in msg.splitlines()[:3]:
                lines.append(f"  · {line}")
        elif name.startswith("resolve"):
            r = s.get("output") or {}
            if isinstance(r, dict) and r.get("timing"):
                lines.append(f"  · aufgelöst: {r['timing']}")
    if not lines:
        return ""
    return "\n".join(lines)


def details(trace: dict) -> dict:
    tool = args = None
    for s in trace.get("steps") or []:
        if s["name"] == "needle_complete":
            calls = (s.get("output") or {}).get("function_calls") or []
            if calls:
                tool = calls[0].get("name")
                args = calls[0].get("arguments")
    return {
        "original": trace.get("input"),
        "canonical": trace.get("canonical"),
        "tool": tool,
        "arguments": args,
        "confidence": trace.get("confidence"),
        "executed": trace.get("executed"),
        "latency_ms": trace.get("total_ms"),
        "ram_mb": trace.get("ram_mb"),
        "steps": trace.get("steps"),
    }


def build_assistant_tab(agent) -> None:
    with gr.Tab("Assistant"):
        gr.Markdown("### Live Agent — jeder Schritt nachvollziehbar")
        inp = gr.Textbox(label="Anfrage (deutsch/englisch)",
                         placeholder="Pack mir morgen Nachmittag Zahnarzt rein.")
        with gr.Row():
            run_btn = gr.Button("Run", variant="primary")
            clear_btn = gr.Button("Clear")
        status = gr.Markdown("Bereit.")
        pipeline = gr.Markdown()
        result = gr.Markdown(label="Ergebnis")
        iterations = gr.Markdown(label="Agent-Iterationen")
        details_json = gr.JSON(label="Trace-Detail")

        def _run(text):
            agent_trace = None
            for trace in agent.handle(text):
                agent_trace = trace
                yield (status_text(trace), pipeline_text(trace),
                       trace.get("result") or "…", iterations_text(trace),
                       details(trace))
            _ = agent_trace

        run_btn.click(_run, inputs=inp,
                      outputs=[status, pipeline, result, iterations, details_json])
        clear_btn.click(lambda: ("", "Bereit.", "", "", None, None),
                        outputs=[inp, status, pipeline, result, iterations,
                                 details_json])
