#!/usr/bin/env bash
# Launcher registered as the Native Messaging host. Resolves paths relative to
# its own location, so the host manifest can point here directly.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SPIKE="$(cd "$HERE/../.." && pwd)"              # .../thunderbird_calendar
REPO="$(cd "$SPIKE/../../../.." && pwd)"        # repo root
PY="${TB_NATIVE_PYTHON:-$REPO/needle-only/.venv/bin/python}"
DEFAULT_W="$SPIKE/ft/models/n2-train-r16-e8-s42.cact"

export PYTHONPATH="$SPIKE:${PYTHONPATH:-}"
export TB_NATIVE_BACKEND="${TB_NATIVE_BACKEND:-n2}"
# Explicit weights if present; otherwise the host reports Base in the preview.
if [ -z "${TB_NATIVE_WEIGHTS:-}" ] && [ -f "$DEFAULT_W" ]; then
  export TB_NATIVE_WEIGHTS="$DEFAULT_W"
fi

exec "$PY" "$HERE/native_host.py"
