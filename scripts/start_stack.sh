#!/usr/bin/env bash
# Start everything in the order the 6 GB GPU needs: ASR first, then the local LLM, then TTS and the API.
# Logs go to .run/*.log. Stop with: kill $(pgrep -f "vllm [s]erve") $(pgrep -f "uvicorn [a]pp.main") $(pgrep -f "services.kokoro.[s]erver")
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .run
wait_for() {  # name, log file
  for _ in $(seq 1 150); do
    grep -qE "Application startup complete" "$2" 2>/dev/null && { echo "  $1 ready"; return 0; }
    grep -qE "Traceback|Error:" "$2" 2>/dev/null && { echo "  $1 FAILED, see $2"; tail -3 "$2"; exit 1; }
    sleep 2
  done
  echo "  $1 timed out, see $2"; exit 1
}
if ! nvidia-smi >/dev/null 2>&1; then
  echo "GPU not visible in WSL. From Windows PowerShell run: wsl --shutdown   then reopen the terminal."; exit 1
fi
echo "ASR (Qwen3-ASR, vLLM)";   nohup ./scripts/run_asr.sh > .run/asr.log 2>&1 & wait_for ASR .run/asr.log
echo "LLM (Qwen3.5-2B, vLLM)";  nohup ./scripts/run_llm.sh > .run/llm.log 2>&1 & wait_for LLM .run/llm.log
echo "TTS (Kokoro)";            nohup ./scripts/run_tts.sh > .run/tts.log 2>&1 & wait_for TTS .run/tts.log
echo "API";                     nohup ./scripts/run_api.sh > .run/api.log 2>&1 & wait_for API .run/api.log
echo "Open http://localhost:8000  (staff inbox: /inbox, key demo-aldau-staff-key)"
