"""Browser voice check: Chromium's fake microphone plays a Kokoro-generated question.

Exercises the real widget path: getUserMedia -> VAD -> WAV -> WebSocket -> ASR -> agent -> TTS -> playback.

    uv run --with playwright python scripts/smoke_voice_browser.py OUT_DIR
"""

import io
import json
import sys
import time
import wave

import httpx
from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "."
QUESTION = "Hello. What time does breakfast start?"

tts = httpx.post("http://localhost:8002/v1/audio/speech", timeout=60, json={
    "model": "oddadmix/Kokoro-7M-Distill", "voice": "af_msa", "input": QUESTION, "response_format": "wav"})
tts.raise_for_status()
with wave.open(io.BytesIO(tts.content)) as src:
    params, frames = src.getparams(), src.readframes(src.getnframes())
silence = b"\x00\x00" * params.framerate * 3  # trailing silence ends the utterance before the file loops
path = f"{OUT}/question.wav"
with wave.open(path, "wb") as dst:
    dst.setparams(params)
    dst.writeframes(b"\x00\x00" * (params.framerate // 2) + frames + silence)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                      f"--use-file-for-fake-audio-capture={path}", "--autoplay-policy=no-user-gesture-required"])
    page = browser.new_page()
    received, t0 = [], {}

    def on_ws(ws):
        def frame(payload):
            try:
                msg = json.loads(payload)
            except (TypeError, ValueError):
                return
            received.append((round(time.perf_counter() - t0.get("sent", time.perf_counter()), 2), msg))
        ws.on("framereceived", frame)
        ws.on("framesent", lambda payload: isinstance(payload, str) and "utterance_end" in payload
              and t0.setdefault("sent", time.perf_counter()))

    page.on("websocket", on_ws)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://localhost:8000/?hotel=atlas-bay")
    page.locator(".msg.ai").first.wait_for(timeout=10000)
    page.locator("button.voice").click()
    deadline = time.time() + 60
    while time.time() < deadline and not any(m["type"] == "audio_end" for _, m in received):
        page.wait_for_timeout(250)
    page.locator("button.voice").click()  # stop voice mode before the looped file triggers another turn
    page.screenshot(path=f"{OUT}/voice.png")
    for at, msg in received:
        if msg["type"] in ("transcript", "sentence", "audio", "audio_end", "error", "interrupted"):
            print(f"@{at}s {msg['type']}: {msg.get('text', msg.get('detail', ''))}")
    print("page errors:", errors or "none")
    browser.close()
