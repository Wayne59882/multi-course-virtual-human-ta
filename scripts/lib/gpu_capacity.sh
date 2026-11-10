#!/usr/bin/env bash
# Choose how many requests the private Ollama serves at once, and how many
# models it keeps loaded, from the GPU memory that is free right now.
# Explicit OLLAMA_NUM_PARALLEL / OLLAMA_MAX_LOADED_MODELS values always win.
#
#   free >= VQ_GPU_FULL_MIB (default 20000): 3 requests, 2 models
#       -> a student answer plus two chapters of course preparation at once;
#          Gemma and Ministral stay loaded together.
#   free >= VQ_GPU_SHARED_MIB (default 14000): 2 requests, 2 models
#       -> a student answer plus one preparation step at once.
#   otherwise: 1 request, 1 model (students first, preparation yields).
#
# The serving processes read the same exported values (app/gpu_policy.py).
# Restart the private Ollama after changing them.
vq_gpu_free_mib() {
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  local device="${CUDA_VISIBLE_DEVICES:-}"
  device="${device%%,*}"
  if [[ -n "$device" ]]; then
    nvidia-smi --id="$device" --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -n 1 | tr -dc '0-9'
  else
    nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -n 1 | tr -dc '0-9'
  fi
}

vq_choose_gpu_capacity() {
  local free parallel loaded
  free="$(vq_gpu_free_mib || true)"
  parallel=1; loaded=1
  if [[ -n "$free" ]]; then
    if (( free >= ${VQ_GPU_FULL_MIB:-20000} )); then parallel=3; loaded=2
    elif (( free >= ${VQ_GPU_SHARED_MIB:-14000} )); then parallel=2; loaded=2
    fi
  fi
  export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-$parallel}"
  export OLLAMA_MAX_LOADED_MODELS="${OLLAMA_MAX_LOADED_MODELS:-$loaded}"
  export VQ_GPU_FREE_MIB_AT_START="${free:-unknown}"
}
