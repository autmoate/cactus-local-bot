#!/usr/bin/env bash
# Register the Native Messaging host for Thunderbird (Linux).
# Run from anywhere; resolves its own path. No sudo needed.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOST_JSON="$HERE/de.example.tb_calendar_extract.json"
TARGET_DIR="$HOME/.mozilla/native-messaging-hosts"
TARGET="$TARGET_DIR/de.example.tb_calendar_extract.json"

chmod +x "$HERE/run_host.sh"

# Sanity check: the N2 venv python must know `needle` for the FT to load.
REPO="$(cd "$HERE/../../../.." && pwd)"
PY="${TB_NATIVE_PYTHON:-$REPO/needle-only/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  echo "WARN: python not found at $PY — set TB_NATIVE_PYTHON and re-run" >&2
fi

mkdir -p "$TARGET_DIR"
python3 - "$HOST_JSON" "$TARGET" "$HERE/run_host.sh" <<'PY'
import json, sys
src, dst, run_host = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(src, encoding="utf-8"))
data["path"] = run_host
json.dump(data, open(dst, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("wrote", dst)
PY

echo "Native host installed. Manifest: $TARGET"
