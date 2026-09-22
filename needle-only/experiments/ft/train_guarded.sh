#!/usr/bin/env bash
# Abgesicherter FT-Runner für die RTX/WSL-Box.
#
# Warum: nach einem CUDA-Fault kann der needle-Prozess im Teardown HÄNGEN und
# VRAM halten. Ein naiver Retry-Loop stapelt dann Prozesse -> mehr Faults.
# Dieses Skript prüft vor jedem Versuch: keine Alt-Prozesse, VRAM frei, GPU
# gesund (Matmul-Smoke). Erst dann startet es EINEN Trainingsversuch.
#
# Usage:
#   needle-only/experiments/ft/train_guarded.sh <venv-python> <run-name> <epochs> <batch> [data]
#
# Beispiel (needle3, Dataset v2):
#   experiments/ft/train_guarded.sh .venv-ft3/bin/python n3-v2-e4 2 4
#   experiments/ft/train_guarded.sh .venv-ft3/bin/python n3-v3-e4 2 4 experiments/ft/data/train_v3.jsonl
set -u
PY=${1:?venv-python fehlt}
RUN=${2:?run-name fehlt}
EPOCHS=${3:-2}
BATCH=${4:-4}
DATA=${5:-}

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
FT="$REPO/needle-only/experiments/ft"
LOGDIR="$FT/reports/runs"
mkdir -p "$LOGDIR"

free_gpu() {                      # wartet, bis kein Trainingsprozess mehr läuft
  for _ in $(seq 1 30); do
    if ! pgrep -f "needle finetun[e]" >/dev/null && ! pgrep -f "train_rt[x]" >/dev/null; then
      return 0
    fi
    sleep 4
  done
  return 1
}

health() {                        # GPU-Gesundheit: 10 bf16-Matmuls
  CUDA_VISIBLE_DEVICES=0 "$REPO/$PY" - <<'EOF' >/dev/null 2>&1
import jax, jax.numpy as jnp
for i in range(10):
    a = jax.random.normal(jax.random.PRNGKey(i), (2048, 2048), dtype=jnp.bfloat16)
    float(jnp.isfinite((a @ a.T).sum()).block_until_ready())
EOF
}

cd "$REPO"
for attempt in 1 2 3; do
  [ -f "$LOGDIR/$RUN.json" ] && { echo "FERTIG: $RUN"; exit 0; }
  echo "### $RUN Versuch $attempt $(date +%H:%M:%S)"
  free_gpu || { echo "!! GPU von Alt-Prozessen belegt"; exit 1; }
  if ! health; then echo "!! GPU nicht gesund — 60s warten"; sleep 60; continue; fi

  ARGS=(--run-name "$RUN" --epochs "$EPOCHS" --rank 16 --lr 1e-4 --seed 42
        --batch-size "$BATCH" --val-split 0)
  [ -n "$DATA" ] && ARGS+=(--data "$DATA")
  # batch 4 passt in das WSL-Limit (~19 GB); preallocate-Pool vermeidet Churn
  unset LD_LIBRARY_PATH
  XLA_PYTHON_CLIENT_PREALLOCATE=true XLA_PYTHON_CLIENT_MEM_FRACTION=0.45 \
  CUDA_VISIBLE_DEVICES=0 "$REPO/$PY" "$FT/train_rtx.py" "${ARGS[@]}"
  code=$?
  cp -f "$LOGDIR/$RUN.log" "$LOGDIR/$RUN.attempt$attempt.log" 2>/dev/null
  # Nach einem Fault kann ein Kindprozess hängen -> gezielt räumen (kein Stapeln)
  pkill -TERM -f "needle finetun[e]" 2>/dev/null; sleep 5
  pkill -KILL -f "needle finetun[e]" 2>/dev/null; sleep 3
  [ $code -eq 0 ] && { echo "OK: $RUN"; exit 0; }
  echo "!! $RUN Versuch $attempt fehlgeschlagen (code=$code)"
done
echo "!! $RUN endgueltig fehlgeschlagen"; exit 1
