"""OpenAI-compatible adapters for the text model, vLLM ASR, and Kokoro TTS.

One pooled httpx client per process keeps connections warm; errors never expose secrets.
"""

import json
import logging
import math
from collections.abc import AsyncIterator

import httpx

from app.config import Settings
from app.errors import Unavailable

log = logging.getLogger("hotel_agent.providers")


async def _explain(exc: Exception) -> int | None:
    """Log why a provider call failed (status + short body; never headers/keys)."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            await exc.response.aread()
            body = exc.response.text[:300]
        except Exception:
            body = ""
        log.warning("provider %s -> HTTP %s: %s", exc.request.url.path, exc.response.status_code, body)
        return exc.response.status_code
    log.warning("provider call failed: %r", exc)
    return None


class Providers:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.s, self.client = settings, client

    @staticmethod
    def _headers(key: str) -> dict:
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def _post(self, url: str, key: str, **kwargs) -> httpx.Response:
        try:
            response = await self.client.post(url, headers=self._headers(key), **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            raise Unavailable("External provider request failed", upstream_status=await _explain(exc)) from exc

    async def stream_chat(self, messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[tuple]:
        """Yields ("text", str), then ("tool_calls", [...]) if the model called tools."""
        if not self.s.llm_configured:
            raise Unavailable("The text model is not configured")
        body = {
            "model": self.s.llm_model,
            "messages": messages,
            "stream": True,
            "temperature": 0.3,
            "max_tokens": 900,
        }
        if tools:
            body["tools"] = tools
        if self.s.llm_reasoning == "off":
            body["reasoning"] = {"enabled": False}
        elif self.s.llm_reasoning:
            body["reasoning"] = {"effort": self.s.llm_reasoning}
        calls: dict[int, dict] = {}
        finished = False
        try:
            async with self.client.stream(
                "POST",
                self.s.llm_base_url.rstrip("/") + "/chat/completions",
                headers=self._headers(self.s.llm_api_key),
                json=body,
                timeout=self.s.llm_timeout_seconds,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()  # keep the error body readable for logging
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        finished = True
                        break
                    data = json.loads(payload)
                    if data.get("error"):
                        raise ValueError("provider error")
                    for choice in data.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield ("text", delta["content"])
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["arguments"] += fn.get("arguments") or ""
                        if choice.get("finish_reason") == "length":
                            raise ValueError("truncated")
                        if choice.get("finish_reason"):
                            finished = True
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise Unavailable("The text model stream failed", upstream_status=await _explain(exc)) from exc
        if not finished:
            raise Unavailable("The text model stream ended early")
        if calls:
            yield ("tool_calls", [calls[i] for i in sorted(calls)])

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not (self.s.llm_api_key and self.s.embedding_model):
            raise Unavailable("Embeddings are not configured")
        response = await self._post(
            self.s.llm_base_url.rstrip("/") + "/embeddings",
            self.s.llm_api_key,
            json={"model": self.s.embedding_model, "input": texts},
        )
        try:
            rows = sorted(response.json()["data"], key=lambda r: r["index"])
            vectors = [r["embedding"] for r in rows]
            if len(vectors) != len(texts) or any(
                not v or not all(math.isfinite(x) for x in v) for v in vectors
            ):
                raise ValueError()
            return vectors
        except (ValueError, KeyError, TypeError) as exc:
            raise Unavailable("Embedding provider returned invalid vectors") from exc

    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> str:
        response = await self._post(
            self.s.asr_base_url.rstrip("/") + "/audio/transcriptions",
            self.s.asr_api_key,
            data={"model": self.s.asr_model},
            files={"file": (filename, audio, content_type)},
            timeout=30,
        )
        try:
            text = response.json()["text"].strip()
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise Unavailable("Speech recognition returned an invalid response") from exc
        # Qwen3-ASR may prefix "language X<asr_text>"; keep only the transcript.
        if "<asr_text>" in text:
            text = text.split("<asr_text>", 1)[1].strip()
        return text[:4000]

    async def speak(self, text: str) -> bytes:
        response = await self._post(
            self.s.tts_base_url.rstrip("/") + "/audio/speech",
            self.s.tts_api_key,
            json={"model": self.s.tts_model, "voice": self.s.tts_voice, "input": text,
                  "response_format": "mp3"},
            timeout=30,
        )
        if not response.content or not response.headers.get("content-type", "").startswith("audio/"):
            raise Unavailable("Speech synthesis returned invalid audio")
        return response.content
