"""Kompakte Terminal-Animation (Spinner + Handlungszeile) für den Hintergrund-Lauf."""
import itertools
import sys
import threading

_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Activity:
    """Zeigt '· ⠋ denke nach …' in einer Zeile, solange der Agent arbeitet."""

    def __init__(self, console, label: str = "denke nach …"):
        self.console = console
        self.label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, label: str | None = None) -> None:
        if label:
            self.label = label
        if self._thread and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def set(self, label: str) -> None:
        self.label = label

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        self._clear()

    def _spin(self) -> None:
        for frame in itertools.cycle(_FRAMES):
            if self._stop.is_set():
                break
            sys.stdout.write(f"\r  \x1b[2m{frame} {self.label}\x1b[0m\x1b[K")
            sys.stdout.flush()
            self._stop.wait(0.12)
        sys.stdout.write("\r\x1b[K")
        sys.stdout.flush()

    def _clear(self) -> None:
        sys.stdout.write("\r\x1b[K")
        sys.stdout.flush()
