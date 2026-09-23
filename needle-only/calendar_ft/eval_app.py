#!/usr/bin/env python3
"""Gradio-Eval-App für die Calendar-FT-Modelle (analog cactuscompute.com/needle).

Links: Tool-Liste (JSON, editierbar) — Rechts: Query + Run + Result.
Preset-Wechsel entlädt das aktuelle Modell (gc + JAX-Cache-Clear), lädt das
neue (.cact bzw. Base-Engine) und deaktiviert die Controls solange.

Start (aus Repo-Root, FT-Umgebung):
  unset LD_LIBRARY_PATH
  NEEDLE_TELEMETRY=0 XLA_PYTHON_CLIENT_PREALLOCATE=false \
      .venv-ft/bin/python needle-only/calendar_ft/eval_app.py
  # GPU: zusätzlich CUDA_VISIBLE_DEVICES=<n>, Port: --port
"""
from __future__ import annotations

import gc
import json
import sys
import threading
import time
from pathlib import Path

FT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(FT_DIR))

import gradio as gr

import needle

MODELS_DIR = FT_DIR / "models"
SCHEMAS_DIR = FT_DIR / "schemas"
MAX_NEW_TOKENS_DEFAULT = 256

# Preset -> Konfiguration (weights=None = Base-Engine ohne FT-Weights)
PRESETS = {
    "calendar_write": {
        "label": "Calendar Write (FT)",
        "weights": MODELS_DIR / "calendar_write.cact",
        "examples": [
            "Morgen um 14 Uhr Zahnarzt.",
            "Verschieb Teammeeting auf 15:30.",
            "Trag Zahnarzttermin kommende Woche um 17 Uhr ein.",
            "Sag den Termin Friseur ab.",
        ],
    },
    "calendar_read": {
        "label": "Calendar Read (FT)",
        "weights": MODELS_DIR / "calendar_read.cact",
        "examples": [
            "Was steht diese Woche an?",
            "Zeige meine Termine heute.",
            "Welche Termine habe ich nächsten Monat?",
        ],
    },
    "reminder": {
        "label": "Reminder (FT)",
        "weights": MODELS_DIR / "reminder.cact",
        "examples": [
            "Erinnere mich in 10 Minuten an die Wäsche.",
            "Timer für 2 Minuten.",
            "Erinner mich morgen früh an die Medikamente.",
        ],
    },
    "calendar_all": {
        "label": "Calendar All (merged FT)",
        "weights": FT_DIR.parent / "calendar_ft_all" / "models" / "all_tools.cact",
        "examples": [
            "Erinnere mich in 10 Minuten an die Wäsche.",
            "Morgen um 14 Uhr Zahnarzt.",
            "Was steht diese Woche an?",
            "Erinner mich an den Zahnarzttermin morgen früh.",
            "Wie wird das Wetter morgen?",
        ],
    },
    "base": {
        "label": "Base (needle2, kein FT)",
        "weights": None,
        "examples": [
            "Morgen um 14 Uhr Zahnarzt.",
            "Was steht diese Woche an?",
            "Erinnere mich in 10 Minuten an die Wäsche.",
            "Wie wird das Wetter morgen?",
        ],
    },
}

_lock = threading.Lock()
_state = {"preset": None, "agent": None}


def _load_schema(preset: str) -> list:
    if preset == "base":
        # Base-Modell: alle drei Calendar-Schemas als natives Multi-Tool-Set
        return [json.loads((SCHEMAS_DIR / f"{t}.json").read_text())
                for t in ("calendar_write", "calendar_read", "reminder")]
    if preset == "calendar_all":
        # Merged-FT: Schemas mit Negative-Boundaries aus calendar_ft_all/
        all_schemas = FT_DIR.parent / "calendar_ft_all" / "schemas"
        return [json.loads((all_schemas / f"{t}.json").read_text())
                for t in ("calendar_write", "calendar_read", "reminder")]
    return [json.loads((SCHEMAS_DIR / f"{preset}.json").read_text())]


def _unload() -> None:
    if _state["agent"] is not None:
        _state["agent"] = None
        _state["preset"] = None
        gc.collect()
        try:
            import jax

            jax.clear_caches()
        except Exception:
            pass


def switch_preset(preset: str):
    """Generator: erst Controls sperren + Hinweis, dann laden, dann freigeben."""
    tools_json = json.dumps(_load_schema(preset), ensure_ascii=False, indent=2)
    yield (f"⏳ Lade **{PRESETS[preset]['label']}** …", tools_json,
           gr.update(interactive=False), gr.update(interactive=False))
    try:
        with _lock:
            weights = PRESETS[preset]["weights"]
            w = str(weights) if weights else None
            t0 = time.time()
            agent = needle.Needle(tools=_load_schema(preset), weights=w)
            _unload()
            _state["agent"], _state["preset"] = agent, preset
            status = (f"✅ **{PRESETS[preset]['label']}** geladen in {time.time() - t0:.1f}s · "
                      f"Quelle: `{w or 'Engine (Base, weights=None)'}`")
    except Exception as exc:  # noqa: BLE001
        _unload()
        raise gr.Error(f"Modell-Load fehlgeschlagen: {exc}") from exc
    yield (status, tools_json,
           gr.update(interactive=True), gr.update(interactive=True))


def run_query(query: str, max_new_tokens):
    if not (query or "").strip():
        raise gr.Error("Bitte eine Query eingeben.")
    if _state["agent"] is None:
        raise gr.Error("Kein Modell geladen — bitte links ein Preset wählen.")
    with _lock:
        agent = _state["agent"]
        agent.reset()
        t0 = time.time()
        resp = agent.complete(query, max_new_tokens=int(max_new_tokens or 256))
    dt = time.time() - t0
    calls = resp.get("function_calls") or []
    result = (f"**Function-Call(s):**\n\n```json\n"
              f"{json.dumps(calls, ensure_ascii=False, indent=2)}\n```"
              if calls else "**Kein Call** — Off-Topic/Refusal (`[]`)")
    meta = (f"`confidence: {resp.get('confidence')}` · `{dt * 1000:.0f} ms` · "
            f"`decode {resp.get('decode_tps')} tok/s` · `peak_ram {resp.get('peak_ram_mb')} MB`")
    reasoning = resp.get("reasoning") or ""
    raw = json.dumps(resp, ensure_ascii=False, indent=2)
    return result, meta, reasoning, raw


def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Calendar-FT Eval") as demo:
        gr.Markdown(
            "## Needle 2 — Calendar-FT Eval-Playground\n"
            "Links Tool-Liste, rechts Query + **Run**. Ein Preset-Wechsel entlädt das "
            "aktive Modell, lädt das neue und sperrt die Controls bis der Load fertig ist."
        )
        preset = gr.Radio(list(PRESETS.keys()), value="calendar_write",
                          label="Modell-Preset",
                          info="FT-.cact-Modelle aus needle-only/calendar_ft/models/, "
                               "Base = Engine ohne FT-Weights")
        status = gr.Markdown("Noch kein Modell geladen — Preset wählen.")
        with gr.Row():
            with gr.Column():
                gr.Markdown("### Tool-Liste (JSON)")
                tools_box = gr.Code(value="", language="json", lines=20)
            with gr.Column():
                gr.Markdown("### Query")
                query = gr.Textbox(lines=2, placeholder="z. B. Morgen um 14 Uhr Zahnarzt.")
                with gr.Row():
                    btn_run = gr.Button("Run", variant="primary", interactive=False)
                    max_new = gr.Number(value=MAX_NEW_TOKENS_DEFAULT, precision=0,
                                        label="max_new_tokens")
                ex_dd = gr.Dropdown(label="Beispiel-Query laden", choices=[],
                                    interactive=True)
                out_calls = gr.Markdown()
                out_meta = gr.Markdown()
                with gr.Accordion("Reasoning", open=False):
                    out_reason = gr.Markdown()
                with gr.Accordion("Rohe Antwort (JSON)", open=False):
                    out_raw = gr.Code(language="json")

        def _examples_for(p: str):
            return gr.update(choices=PRESETS[p]["examples"], value=None)

        preset.change(switch_preset, inputs=[preset],
                      outputs=[status, tools_box, btn_run, query])
        preset.change(_examples_for, inputs=[preset], outputs=[ex_dd])
        ex_dd.input(lambda q: q or "", inputs=[ex_dd], outputs=[query])
        btn_run.click(run_query, inputs=[query, max_new],
                      outputs=[out_calls, out_meta, out_reason, out_raw])
        query.submit(run_query, inputs=[query, max_new],
                     outputs=[out_calls, out_meta, out_reason, out_raw])
    return demo


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Calendar-FT Gradio Eval-App")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--preset", default="calendar_write", choices=list(PRESETS.keys()),
                    help="Preset, das beim Start geladen wird")
    args = ap.parse_args()

    demo = build_ui()
    # Start-Preset laden (kurz; danach ist der Status im UI korrekt)
    for _ in switch_preset(args.preset):
        pass
    try:
        demo.launch(server_name=args.host, server_port=args.port,
                    ssr_mode=False)  # SSR liefert in headless-Umgebungen JSON-404 statt UI
    except OSError as exc:
        raise SystemExit(f"Port {args.port} belegt? App läuft evtl. schon — "
                         f"anderen --port wählen. ({exc})") from exc
    print(f"Playground: http://{args.host}:{args.port}/")
