#!/usr/bin/env bash
# Kokoro TTS (OpenAI-compatible /v1/audio/speech) in its own virtualenv (default ~/kokoro-env).
# TTS_WORKERS=N starts N processes on ports TTS_PORT..TTS_PORT+N-1 (each ~470 MB RAM), splitting
# the CPU threads between them; list them in .env as TTS_BASE_URLS=http://localhost:8002/v1,...
set -euo pipefail
env_dir="${KOKORO_ENV:-$HOME/kokoro-env}"
export PATH="$env_dir/bin:$PATH"
cd "$(dirname "$0")/.."
workers="${TTS_WORKERS:-1}"
port="${TTS_PORT:-8002}"
threads=$(( $(nproc) / workers )); (( threads < 1 )) && threads=1
if (( workers == 1 )); then
  exec env KOKORO_THREADS="${KOKORO_THREADS:-$threads}" "$env_dir/bin/python" -m uvicorn services.kokoro.server:app --host 127.0.0.1 --port "$port"
fi
pids=()
for ((i = 0; i < workers; i++)); do
  KOKORO_THREADS="$threads" "$env_dir/bin/python" -m uvicorn services.kokoro.server:app --host 127.0.0.1 --port $((port + i)) &
  pids+=($!)
done
trap 'kill "${pids[@]}" 2>/dev/null' EXIT INT TERM
wait
