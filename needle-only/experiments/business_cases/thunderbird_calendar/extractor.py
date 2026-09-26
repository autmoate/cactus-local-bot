"""Local Needle host boundary (§4, §17).

The UI / workflow never import `needle` directly. They talk to this logical
protocol over a subprocess:

    Gradio / later Thunderbird -> LocalNeedleHost -> worker (N2 or N3 venv)

Two interpreter paths keep Needle 2 and Needle 3 in separate venvs (they cannot
be installed together). The wire format is the one a later Native Messaging host
would speak; only the transport is a local JSON-lines pipe.

    request : {"cmd":"extract","text":..,"subject":..,"reference_time":..,"mode":..}
    response: {"status":"candidate|none|multiple|invalid|error",
               "candidates":[{"title","when","location"}],
               "confidence":..,"latency_ms":..,"raw":[...],"error":..}
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
NEEDLE_ONLY = HERE.parents[2]
REPO_ROOT = HERE.parents[3]
DEFAULT_N2_PYTHON = NEEDLE_ONLY / ".venv" / "bin" / "python"
DEFAULT_N3_PYTHON = REPO_ROOT / ".venv-ft3" / "bin" / "python"

BACKENDS = ("n2", "n3")


def interpreter_for(backend: str) -> str:
    env = os.environ.get(f"TB_{backend.upper()}_PYTHON")
    if env:
        return env
    default = DEFAULT_N2_PYTHON if backend == "n2" else DEFAULT_N3_PYTHON
    return str(default)


def _make_tools():
    """Frozen v2 extraction contract (FT_PLAN.md §4).

    One tool only. Negatives are the canonical empty call `[]` — needle's own
    finetune generator uses `"answers": []` for refusals, so the former
    `no_event` tool is gone (see FT_PLAN.md §4, Problem B). `title` is optional:
    when the text does not state one, Python falls back to the cleaned subject
    (`workflow.clean_subject`). `when` asks for the complete verbatim temporal
    phrase; date-only is valid (all-day), so it is no longer "date AND clock".

    Argument order stays a probe knob until the order probe has run against the
    realism set: TB_CONTRACT_ORDER=title (default, current evidence) | when.
    """
    import needle

    # Requiredness is identical across both orders (all optional) so the probe
    # isolates ORDER, not requiredness. Python enforces `when`: an empty `when`
    # yields status=incomplete/invalid downstream (temporal.compile_when), never
    # the tool schema.
    order = os.environ.get("TB_CONTRACT_ORDER", "title").lower()

    if order == "when":
        @needle.tool
        def extract_event(when: str = "", title: str = "",
                          location: str = "") -> str:
            """Use only when the message contains one concrete new calendar event
            that is already agreed, confirmed or announced.

            Args:
                when: the complete temporal phrase copied character for
                    character; include all date, clock-time, end-time and
                    timezone evidence present; date-only is valid for all-day
                    events; never ISO or calculated
                title: the event name copied exactly as written, or empty if the
                    text does not state one explicitly
                location: the place or meeting medium exactly as written; empty
                    if none
            """
            return ""
        return [extract_event]

    @needle.tool
    def extract_event(title: str = "", when: str = "",
                      location: str = "") -> str:
        """Use only when the message contains one concrete new calendar event
        that is already agreed, confirmed or announced.

        Args:
            title: the event name copied exactly as written, or empty if the
                text does not state one explicitly
            when: the complete temporal phrase copied character for character;
                include all date, clock-time, end-time and timezone evidence
                present; date-only is valid for all-day events; never ISO or
                calculated
            location: the place or meeting medium exactly as written; empty if
                none
        """
        return ""
    return [extract_event]


def _build_needle(weights: str | None = None):
    import needle

    kwargs = {"tools": _make_tools(), "system": "locale: de-DE"}
    if weights:
        kwargs["weights"] = weights
    return needle.Needle(**kwargs)


def _extract(engine, req: dict) -> dict:
    text = (req.get("text") or "").strip()
    subject = (req.get("subject") or "").strip()
    mode = req.get("mode") or "message"
    if mode == "selection":
        # Selection-only: the model must never see the subject. The subject is
        # only used later, by Python, as a title fallback (workflow.clean_subject).
        prompt = text
    else:
        prompt = f"Betreff: {subject}\n\n{text}" if subject else text
    started = time.perf_counter()
    try:
        engine.reset()
        response = engine.complete(prompt) or {}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}",
                "candidates": [], "raw": [], "latency_ms": 0}
    latency = (time.perf_counter() - started) * 1000
    calls = response.get("function_calls") or []
    candidates = []
    for call in calls:
        args = call.get("arguments") or {}
        if call.get("name") != "extract_event":
            continue
        candidates.append({"title": str(args.get("title") or "").strip(),
                           "when": str(args.get("when") or "").strip(),
                           "location": str(args.get("location") or "").strip()})
    if not calls:
        status = "none"
    elif not candidates:
        status = "invalid"
    elif all(not c["when"] for c in candidates):
        # A call without temporal evidence is not a usable event (Python
        # enforces `when`; the schema keeps it optional for a fair order probe).
        status = "invalid"
    elif len(candidates) == 1:
        status = "candidate"
    else:
        status = "multiple"
    return {"status": status, "candidates": candidates,
            "confidence": response.get("confidence"), "latency_ms": latency,
            "type": response.get("type"), "raw": calls, "prompt": prompt}


def _worker_main(weights: str | None = None) -> int:
    real_out = sys.stdout
    try:
        with contextlib.redirect_stdout(sys.stderr):
            engine = _build_needle(weights)
    except Exception as exc:  # noqa: BLE001
        real_out.write(json.dumps({"status": "error",
                                   "error": f"init failed: {exc}"}) + "\n")
        real_out.flush()
        return 1
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = {"status": "error", "error": f"bad request: {exc}"}
        else:
            cmd = req.get("cmd")
            with contextlib.redirect_stdout(sys.stderr):
                if cmd == "extract":
                    resp = _extract(engine, req)
                elif cmd == "reset":
                    engine.reset()
                    resp = {"status": "ok"}
                elif cmd == "close":
                    real_out.write(json.dumps({"status": "ok"}) + "\n")
                    real_out.flush()
                    return 0
                else:
                    resp = {"status": "error", "error": f"unknown cmd {cmd!r}"}
        real_out.write(json.dumps(resp) + "\n")
        real_out.flush()
    return 0


class LocalNeedleHost:
    """Spawns (and supervises) one Needle worker in the backend's venv."""

    def __init__(self, backend: str = "n2", python: str | None = None,
                 weights: str | None = None, timeout: float = 240.0) -> None:
        if backend not in BACKENDS:
            raise ValueError(f"unknown backend {backend!r}")
        self.backend = backend
        self.python = python or interpreter_for(backend)
        self.weights = weights
        self.timeout = timeout
        self.model = f"{backend}-base" if not weights else f"{backend}:{Path(weights).name}"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stderr: deque[str] = deque(maxlen=40)

    def available(self) -> tuple[bool, str]:
        if not Path(self.python).exists():
            return False, f"interpreter not found: {self.python}"
        return True, ""

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        ok, why = self.available()
        if not ok:
            raise RuntimeError(why)
        self._proc = subprocess.Popen(
            [self.python, str(Path(__file__)), "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            env={**os.environ, "PYTHONPATH": str(HERE)})
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        if self._proc and self._proc.stderr:
            for line in self._proc.stderr:
                self._stderr.append(line.rstrip())

    def _send(self, request: dict) -> dict:
        with self._lock:
            self.start()
            assert self._proc and self._proc.stdin and self._proc.stdout
            try:
                self._proc.stdin.write(json.dumps(request) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError) as exc:
                return {"status": "error", "error": f"worker pipe: {exc}",
                        "candidates": [], "raw": []}
            line = self._proc.stdout.readline()
            if not line:
                tail = " | ".join(self._stderr)
                return {"status": "error",
                        "error": f"worker exited (rc={self._proc.poll()}): {tail}",
                        "candidates": [], "raw": []}
            return json.loads(line)

    def extract(self, *, text: str, subject: str = "", reference_time: str = "",
                mode: str = "message") -> dict:
        return self._send({"cmd": "extract", "type": "extract_event", "text": text,
                           "subject": subject, "reference_time": reference_time,
                           "mode": mode})

    def reset(self) -> dict:
        return self._send({"cmd": "reset"})

    def close(self) -> None:
        proc = self._proc
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.write(json.dumps({"cmd": "close"}) + "\n")
                proc.stdin.flush()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        finally:
            self._proc = None


def main() -> int:
    ap = argparse.ArgumentParser(prog="extractor-worker")
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--weights", default=None)
    args = ap.parse_args()
    if args.worker:
        return _worker_main(args.weights)
    ap.error("use --worker")


if __name__ == "__main__":
    raise SystemExit(main())
