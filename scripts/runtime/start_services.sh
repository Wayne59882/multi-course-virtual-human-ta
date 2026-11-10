#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/../lib/paths.sh"
cd "$VHTA_APP_DIR"
BASE="${VQ_BASE:-/path/to/workdir}"
# Resolve before the TTS environment changes PATH. The installer and server
# must import PyMuPDF with the exact same interpreter.
PYTHON_BIN="$(command -v "${PYTHON_BIN:-python3}")" || { echo '[ERROR] Serving Python was not found.' >&2; exit 127; }
export PYTHON_BIN
mkdir -p logs
[[ -x "$BASE/ollama/bin/ollama" ]] && export PATH="$BASE/ollama/bin:$PATH"
[[ -x "$BASE/node-v22/bin/node" ]] && export PATH="$BASE/node-v22/bin:$PATH"
[[ -d "$BASE/miniforge-teacher-tts/bin" ]] && export PATH="$BASE/miniforge-teacher-tts/bin:$PATH"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$BASE/ollama-models}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11435}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-4096}"
export VQ_NUM_CTX="${VQ_NUM_CTX:-4096}"
# Match the running private Ollama, or choose from free GPU memory before
# starting it (scripts/lib/gpu_capacity.sh; app/gpu_policy.py reads these).
source "$VHTA_SCRIPTS_DIR/lib/gpu_capacity.sh"
if curl -fsS "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1; then
  if [[ -f "$BASE/ollama-capacity.env" ]]; then
    # shellcheck disable=SC1091
    source "$BASE/ollama-capacity.env"
  else
    # Started by an older script: assume one request at a time until it is restarted.
    export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"
    export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-1}"
    echo '[INFO] Private Ollama was started without capacity settings; restart it to let students and course preparation share the GPU.'
  fi
else
  vq_choose_gpu_capacity
fi
export VQ_GPU_SHARING="${VQ_GPU_SHARING:-auto}"
export VQ_MODEL_KEEP_ALIVE="${VQ_MODEL_KEEP_ALIVE:-30m}"
export VQ_LOCAL_MAX_WAIT_SEC="${VQ_LOCAL_MAX_WAIT_SEC:-240}"
export VQ_BATCH_MAX_ITEMS="${VQ_BATCH_MAX_ITEMS:-4}"
export VQ_BATCH_NUM_CTX="${VQ_BATCH_NUM_CTX:-4096}"
export VQ_BATCH_CHAR_BUDGET="${VQ_BATCH_CHAR_BUDGET:-9000}"
export VQ_BATCH_EVIDENCE_CHARS="${VQ_BATCH_EVIDENCE_CHARS:-1250}"
export VQ_REFERENCE_EMBED_HOST="${VQ_REFERENCE_EMBED_HOST:-127.0.0.1:11438}"
export VQ_REFERENCE_EMBED_MODEL="${VQ_REFERENCE_EMBED_MODEL:-bge-m3:latest}"
export VQ_REFERENCE_CONTEXT_CHARS="${VQ_REFERENCE_CONTEXT_CHARS:-8500}"
export VQ_REFERENCE_OPTION_CANDIDATES="${VQ_REFERENCE_OPTION_CANDIDATES:-3}"
export VQ_REFERENCE_OPTION_CONTRAST_CHARS="${VQ_REFERENCE_OPTION_CONTRAST_CHARS:-2200}"
if [[ -f feedback/feedback.env ]]; then
  # shellcheck disable=SC1091
  source feedback/feedback.env
fi
# A feedback.env copied from an older release can contain that release's
# absolute DB path. Keep new reports in the current release after migration.
export VQ_FEEDBACK_DB="$PWD/feedback/feedback.sqlite3"
export VQ_FEEDBACK_TO_EMAIL="${VQ_FEEDBACK_TO_EMAIL:-admin@example.com}"
if [[ -f benchmark/state/final_profile.env ]]; then
  # shellcheck disable=SC1091
  source benchmark/state/final_profile.env
fi
export VQ_V231_PROFILE="${VQ_V231_PROFILE:-optimized}"
# v224: optional QLoRA adapters for Granite/Gemma/Ministral.
if [[ -f finetune/active_models.env ]]; then
  # shellcheck disable=SC1091
  source finetune/active_models.env
fi
"$PYTHON_BIN" -c "import fitz, course_runtime; assert course_runtime.fitz is not None" || { echo "[ERROR] PDF parser missing in serving Python: $PYTHON_BIN. Install requirements/runtime.txt with the same PYTHON_BIN." >&2; exit 1; }

if ! curl -fsS "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1; then bash "$VHTA_SCRIPTS_DIR/runtime/start_ollama.sh"; fi
if [[ ! -x "$BASE/miniforge-teacher-tts/bin/python" ]] || ! "$BASE/miniforge-teacher-tts/bin/python" -c "import faster_whisper" >/dev/null 2>&1; then
  echo '[ERROR] faster-whisper environment is unavailable; voice-question recognition is mandatory in v206.7.'
  exit 1
fi
bash "$VHTA_SCRIPTS_DIR/runtime/start_stt.sh"
curl -fsS http://127.0.0.1:11437/health >/dev/null || { echo '[ERROR] Multilingual STT health check failed.'; exit 1; }
echo '[OK] Multilingual STT: 127.0.0.1:11437'

if [[ "${VQ_ENABLE_LOCAL_FT_ROUTER:-0}" == "1" ]]; then
  bash "$VHTA_APP_DIR/finetune/START_FINETUNED_LOCAL_ROUTER.sh"
  echo "[OK] Fine-tuned local-model router: ${VQ_GRANITE_API_BASE:-${VQ_GEMMA_API_BASE:-${VQ_MISTRAL_API_BASE:-http://127.0.0.1:11439}}}"
else
  echo '[INFO] Local models use original Ollama weights; question-type prompt engineering remains active for the two selectable local models.'
fi

# Prepare the dedicated semantic retrieval path before exposing Reference Book.
bash "$VHTA_SCRIPTS_DIR/setup/prepare_reference.sh"

pkill -f '[k]b_server.py.*8181' 2>/dev/null || true
pkill -f '[r]eference_server.py.*8182' 2>/dev/null || true
pkill -f '[r]ag_server.py.*8182' 2>/dev/null || true
nohup "$PYTHON_BIN" kb_server.py --mode ollama --host 127.0.0.1 --port 8181 --open none > logs/kb_backend.log 2>&1 & echo $! > logs/kb_backend.pid
nohup "$PYTHON_BIN" reference_server.py --mode ollama --host 127.0.0.1 --port 8182 --open none > logs/reference_backend.log 2>&1 & echo $! > logs/reference_backend.pid
sleep 2
# Stable serving: pre-warm the default Gemma model once, server-side.
# Granite is no longer student-selectable; browser model switching is limited to Gemma/Ministral.
(
  for _ in $(seq 1 20); do curl -fsS http://127.0.0.1:8182/api/reference/health >/dev/null 2>&1 && break; sleep 1; done
  curl -fsS --max-time 180 -X POST http://127.0.0.1:8182/api/warm-model \
    -H 'Content-Type: application/json' -d '{"model_key":"gemma"}' || true
  # With room for two models, Ministral is loaded too so its first answer does not wait.
  if (( ${OLLAMA_MAX_LOADED_MODELS:-1} >= 2 )); then
    curl -fsS --max-time 180 -X POST http://127.0.0.1:8182/api/warm-model \
      -H 'Content-Type: application/json' -d '{"model_key":"mistral"}' || true
  fi
) > logs/default_model_warm.log 2>&1 &
echo $! > logs/default_model_warm.pid
echo '[OK] Query Knowledge Base backend: 127.0.0.1:8181 (Local LLM only)'
echo '[OK] Reference Textbook backend: 127.0.0.1:8182 (Semantic Vector RAG + Local LLM)'
echo '[INFO] Legacy Reference Textbook mode remains physiology-scoped; uploaded-course mode uses the active uploaded course scope and its independent course RAG index.'
echo '[OK] Public gateway: 0.0.0.0:8179'
exec "$PYTHON_BIN" gateway.py --host "${VQ_HOST:-0.0.0.0}" --port "${VQ_PORT:-8179}"
