#!/usr/bin/env bash
# 一键运行：作曲 -> 渲染 -> 混音。  Usage: scripts/run_pipeline.sh [--fallback] [--voice narration.wav] [--out out/final.wav]
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)}"   # uv sync 建的 .venv 优先
RENDER_ARGS=(); MIX_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fallback) RENDER_ARGS+=(--fallback) ;;
    --voice) MIX_ARGS+=(--voice "$2"); shift ;;
    --out) MIX_ARGS+=(--out "$2"); shift ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
  shift
done
echo "== 1/3 score (MIDI)";  "$PY" -m src.score.write_score
echo "== 2/3 render stems";  "$PY" -m src.render.render_all "${RENDER_ARGS[@]}"
echo "== 3/3 mix";           "$PY" -m src.mix.mix "${MIX_ARGS[@]}"
echo "done (see out/)"
