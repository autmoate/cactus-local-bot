"""Gradio app assembly and start only — no domain logic here (plan §app.py)."""

from __future__ import annotations

import argparse
import os
import socket

import gradio as gr

from .agent import Agent
from .calendar import CalendarStore
from .tabs.assistant import build_assistant_tab
from .tabs.calendar import build_calendar_tab
from .tabs.debug import build_debug_tab
from .tabs.needle_lab import build_needle_lab_tab


def build_app(agent: Agent) -> gr.Blocks:
    with gr.Blocks(title="Local Calendar Agent") as app:
        gr.Markdown("# 🗓️ Local Calendar Agent — Needle 2 + Gemma 4 E2B")
        with gr.Tabs():
            build_assistant_tab(agent)
            build_calendar_tab(agent)
            build_debug_tab(agent)
            build_needle_lab_tab(agent)
    return app


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> None:
    ap = argparse.ArgumentParser(prog="local-calendar")
    ap.add_argument("--mode", choices=["needle", "hybrid"], default="hybrid",
                    help="needle: kein Gemma; hybrid: Gemma-Normalisierung + Repair")
    ap.add_argument("--db", default=os.environ.get("CALENDAR_DB", "data/calendar.db"))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7860)
    ap.add_argument("--share", action="store_true",
                    help="gradio share tunnel (tun works per network; LAN is default)")
    args = ap.parse_args()
    agent = Agent(CalendarStore(args.db), mode=args.mode)
    print(f"  LAN URL: http://{_lan_ip()}:{args.port}")
    build_app(agent).launch(server_name=args.host, server_port=args.port,
                            share=args.share)


if __name__ == "__main__":
    main()
