"""OpenAI-compatible stand-in for the text model, for load-testing the app layer without a GPU.

Streams a fixed answer: STUB_TTFT seconds to the first token, then STUB_TOKENS tokens at
STUB_TPS tokens/second. Run: uv run uvicorn scripts.stub_llm:app --port 8099
"""

import asyncio
import json
import os

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

TTFT = float(os.environ.get("STUB_TTFT", "0.3"))
TPS = float(os.environ.get("STUB_TPS", "60"))
TOKENS = int(os.environ.get("STUB_TOKENS", "30"))
app = FastAPI()


@app.post("/v1/chat/completions")
async def chat(body: dict):
    async def events():
        await asyncio.sleep(TTFT)
        for i in range(TOKENS):
            chunk = {"choices": [{"index": 0, "delta": {"content": f"word{i} " if i < TOKENS - 1 else "done."}}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            await asyncio.sleep(1 / TPS)
        yield 'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
