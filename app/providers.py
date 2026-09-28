"""OpenAI-compatible adapters for the text model, vLLM ASR, and Kokoro TTS.

One pooled httpx client per process keeps connections warm; errors never expose secrets.
"""

import json
import logging
import math
import time
from collections import OrderedDict
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


class EndpointPool:
    """Round-robin over copies of a model server; a failed copy is skipped for a short time."""

    def __init__(self, urls: list[str], cooldown: float = 15.0):
        self.urls = [u.rstrip("/") for u in urls if u.strip()]
        self.cooldown = cooldown
        self._next = 0
        self._down_until: dict[str, float] = {}

    def order(self) -> list[str]:
        """All endpoints, starting from the next in rotation; healthy ones first."""
        n = len(self.urls)
        start = self._next % n
        self._next += 1
        rotated = self.urls[start:] + self.urls[:start]
        now = time.monotonic()
        return [u for u in rotated if self._down_until.get(u, 0) <= now] + \
               [u for u in rotated if self._down_until.get(u, 0) > now]

    def failed(self, url: str) -> None:
        self._down_until[url] = time.monotonic() + self.cooldown


def _pool(single: str, many: str) -> EndpointPool:
    return EndpointPool([u.strip() for u in many.split(",")] if many.strip() else [single])


class Providers:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.s, self.client = settings, client
        self.llm_pool = _pool(settings.llm_base_url, settings.llm_base_urls)
        self.asr_pool = _pool(settings.asr_base_url, settings.asr_base_urls)
        self.tts_pool = _pool(settings.tts_base_url, settings.tts_base_urls)
        self._tts_cache: OrderedDict[str, bytes] = OrderedDict()
        self._tts_cache_bytes = 0

    @staticmethod
    def _headers(key: str) -> dict:
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def _post(self, pool: EndpointPool, path: str, key: str, **kwargs) -> httpx.Response:
        """POST to the first working copy; connection errors and 5xx fail over to the next."""
        last: Exception | None = None
        for base in pool.order():
            try:
                response = await self.client.post(base + path, headers=self._headers(key), **kwargs)
                if response.status_code >= 500:
                    pool.failed(base)
                    last = httpx.HTTPStatusError("server error", request=response.request, response=response)
                    continue
                response.raise_for_status()
                return response
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                pool.failed(base)
                last = exc
            except httpx.HTTPError as exc:
                raise Unavailable("External provider request failed", upstream_status=await _explain(exc)) from exc
        raise Unavailable("External provider request failed", upstream_status=await _explain(last)) from last

    async def stream_chat(self, messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[tuple]:
        """Yields ("text", str), then ("tool_calls", [...]) if the model called tools."""
        if not self.s.llm_configured:
            raise Unavailable("The text model is not configured")
        body = {
            "model": self.s.llm_model,
            "messages": messages,
            "stream": True,
            "temperature": self.s.llm_temperature,
            "max_tokens": self.s.llm_max_tokens,
        }
        if tools:
            body["tools"] = tools
        body.update(self.s.llm_extra_body)
        if self.s.llm_reasoning == "off":
            body["reasoning"] = {"enabled": False}
        elif self.s.llm_reasoning:
            body["reasoning"] = {"effort": self.s.llm_reasoning}
        calls: dict[int, dict] = {}
        finished = False
        started = False
        endpoints = self.llm_pool.order()
        for attempt, base in enumerate(endpoints):
            try:
                async with self.client.stream(
                    "POST",
                    base + "/chat/completions",
                    headers=self._headers(self.s.llm_api_key),
                    json=body,
                    timeout=self.s.llm_timeout_seconds,
                ) as response:
                    if response.status_code >= 500 and attempt + 1 < len(endpoints):
                        self.llm_pool.failed(base)  # another copy may be healthy
                        continue
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
                                started = True
                                yield ("text", delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                                slot["id"] = tc.get("id") or slot["id"]
                                fn = tc.get("function") or {}
                                slot["name"] += fn.get("name") or ""
                                slot["arguments"] += fn.get("arguments") or ""
                            if choice.get("finish_reason") == "length":
                                # Hit the reply limit: keep what was written (it was already streamed).
                                log.info("model reply reached max_tokens=%s", self.s.llm_max_tokens)
                            if choice.get("finish_reason"):
                                finished = True
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # Nothing reached the guest yet: safe to try the next copy of the model server.
                self.llm_pool.failed(base)
                if started or attempt + 1 == len(endpoints):
                    raise Unavailable("The text model stream failed", upstream_status=await _explain(exc)) from exc
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
            self.llm_pool, "/embeddings",
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

    async def transcribe(self, audio: bytes, filename: str, content_type: str, language: str | None = None) -> str:
        data = {"model": self.s.asr_model}
        if language:
            data["language"] = language  # ISO 639-1, e.g. "ar"
        response = await self._post(
            self.asr_pool, "/audio/transcriptions",
            self.s.asr_api_key,
            data=data,
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
        """Synthesize speech. Repeated sentences (greetings, status lines, common answers) are
        served from an in-memory LRU cache, so they cost no TTS time."""
        key = f"{self.s.tts_model}|{self.s.tts_voice}|{text}"
        cached = self._tts_cache.get(key)
        if cached is not None:
            self._tts_cache.move_to_end(key)
            return cached
        response = await self._post(
            self.tts_pool, "/audio/speech",
            self.s.tts_api_key,
            json={"model": self.s.tts_model, "voice": self.s.tts_voice, "input": text,
                  "response_format": "mp3"},
            timeout=30,
        )
        if not response.content or not response.headers.get("content-type", "").startswith("audio/"):
            raise Unavailable("Speech synthesis returned invalid audio")
        audio = response.content
        limit = self.s.tts_cache_mb * 1024 * 1024
        if len(text) <= 300 and len(audio) < limit // 10:
            self._tts_cache[key] = audio
            self._tts_cache_bytes += len(audio)
            while self._tts_cache_bytes > limit:
                _, old = self._tts_cache.popitem(last=False)
                self._tts_cache_bytes -= len(old)
        return audio
