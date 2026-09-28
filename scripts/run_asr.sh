#!/usr/bin/env bash
# Qwen3-ASR on vLLM, in its own virtualenv (default ~/vllm-env).
set -euo pipefail
env_dir="${VLLM_ENV:-$HOME/vllm-env}"
export PATH="$env_dir/bin:$PATH"  # vLLM launches tools such as ninja from this environment
exec "$env_dir/bin/vllm" serve "${ASR_MODEL:-Qwen/Qwen3-ASR-0.6B}" \
  --port "${ASR_PORT:-8001}" \
  --trust-remote-code \
  --gpu-memory-utilization "${ASR_GPU_UTIL:-0.75}" \
  --max-model-len 4096 \
  --enforce-eager
