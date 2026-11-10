#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/paths.sh"
BASE="${VQ_BASE:-/path/to/workdir}"
OLLAMA_BIN="$BASE/ollama/bin/ollama"
export PATH="$BASE/ollama/bin:$PATH"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$BASE/ollama-models}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11435}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-4096}"
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/gpu_capacity.sh"
vq_choose_gpu_capacity
LOG="$BASE/ollama.log"
CAPACITY="$BASE/ollama-capacity.env"
if [[ ! -x "$OLLAMA_BIN" ]]; then
  echo "ERROR: private Ollama not found at $OLLAMA_BIN" >&2
  exit 1
fi
if curl -fsS "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1; then
  echo "Private Ollama already running at $OLLAMA_HOST"
  exit 0
fi
nohup env   OLLAMA_HOST="$OLLAMA_HOST"   OLLAMA_MODELS="$OLLAMA_MODELS"   OLLAMA_CONTEXT_LENGTH="$OLLAMA_CONTEXT_LENGTH"   OLLAMA_MAX_LOADED_MODELS="$OLLAMA_MAX_LOADED_MODELS"   OLLAMA_NUM_PARALLEL="$OLLAMA_NUM_PARALLEL"   "$OLLAMA_BIN" serve > "$LOG" 2>&1 &
# The serving processes read these so they match the running Ollama.
printf 'export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-%s}"\nexport OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-%s}"\n' \
  "$OLLAMA_NUM_PARALLEL" "$OLLAMA_MAX_LOADED_MODELS" > "$CAPACITY"
sleep 3
curl -fsS "http://$OLLAMA_HOST/api/version"
echo
echo "Private Ollama started at $OLLAMA_HOST; context=$OLLAMA_CONTEXT_LENGTH; parallel=$OLLAMA_NUM_PARALLEL; loaded models=$OLLAMA_MAX_LOADED_MODELS; free GPU MiB at start=$VQ_GPU_FREE_MIB_AT_START"
