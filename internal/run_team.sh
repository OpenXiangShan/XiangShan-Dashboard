#!/usr/bin/env bash
# Run from any directory; keep this foreground process inside tmux.
set -euo pipefail
task_dashboard="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_dashboard"
export PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
exec python3 -u internal/serve_gem5.py \
  --processor "${GEM5_DATA_PROC:-$task_dashboard/../gem5_data_proc}" \
  --bind "${DASHBOARD_BIND:-127.0.0.1}" \
  --port "${DASHBOARD_PORT:-8002}" "$@"
