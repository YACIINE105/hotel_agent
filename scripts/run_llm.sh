#!/usr/bin/env bash
# Local text model on vLLM (same ~/vllm-env as ASR), sized to share a 6 GB GPU with Qwen3-ASR.
# Start ASR first (0.46 of VRAM, ~2.5 GB), then this (0.50; ~3 GB incl. CUDA graphs). Text-only: the vision encoder is skipped.
set -euo pipefail
env_dir="${VLLM_ENV:-$HOME/vllm-env}"
export PATH="$env_dir/bin:$PATH"
# Load from the local cache only: download first with
#   HF_HUB_DISABLE_XET=1 ~/vllm-env/bin/hf download cyankiwi/Qwen3.5-2B-AWQ-4bit
# (an in-server download through the proxy stalled silently).
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
exec "$env_dir/bin/vllm" serve "${LLM_LOCAL_MODEL:-cyankiwi/Qwen3.5-2B-AWQ-4bit}" \
  --served-model-name "${LLM_SERVED_NAME:-qwen3.5-2b}" \
  --port "${LLM_PORT:-8003}" \
  --language-model-only \
  --gpu-memory-utilization "${LLM_GPU_UTIL:-0.50}" \
  --max-model-len "${LLM_MAX_LEN:-8192}" \
  --max-num-seqs "${LLM_MAX_SEQS:-4}" \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --enable-prefix-caching \
  --max-cudagraph-capture-size 4
