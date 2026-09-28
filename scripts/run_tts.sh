#!/usr/bin/env bash
# Kokoro TTS (OpenAI-compatible /v1/audio/speech) in its own virtualenv (default ~/kokoro-env).
set -euo pipefail
env_dir="${KOKORO_ENV:-$HOME/kokoro-env}"
export PATH="$env_dir/bin:$PATH"
cd "$(dirname "$0")/.."
exec "$env_dir/bin/python" -m uvicorn services.kokoro.server:app --host 127.0.0.1 --port "${TTS_PORT:-8002}"
