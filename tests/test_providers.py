import httpx
import pytest

from app.config import Settings
from app.errors import Unavailable
from app.providers import Providers

SSE_OK = ('data: {"choices":[{"index":0,"delta":{"content":"Hello."}}]}\n\n'
          'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')


def make(handler, **overrides):
    settings = Settings(_env_file=None, llm_api_key="k", llm_model="m", **overrides)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Providers(settings, client), client


async def test_stream_fails_over_to_the_next_model_copy_before_any_text():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "down":
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text=SSE_OK, headers={"content-type": "text/event-stream"})

    p, c = make(handler, llm_base_urls="http://down/v1,http://up/v1")
    async with c:
        out = [x async for x in p.stream_chat([{"role": "user", "content": "hi"}])]
        assert out == [("text", "Hello.")] and seen[:2] == ["down", "up"]
        # The failed copy is skipped for a while: the next call goes straight to the healthy one.
        seen.clear()
        [x async for x in p.stream_chat([{"role": "user", "content": "hi"}])]
        assert seen == ["up"]


async def test_stream_error_after_text_started_is_not_retried_elsewhere():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, text='data: {"choices":[{"index":0,"delta":{"content":"Hel"}}]}\n\n',
                              headers={"content-type": "text/event-stream"})  # ends without [DONE]

    p, c = make(handler, llm_base_urls="http://a/v1,http://b/v1")
    async with c:
        with pytest.raises(Unavailable):
            [x async for x in p.stream_chat([{"role": "user", "content": "hi"}])]
    assert len(calls) == 1  # the guest already saw text: never splice in a second model's answer


async def test_asr_fails_over_on_server_error():
    def handler(request):
        if request.url.host == "asr1":
            return httpx.Response(503)
        return httpx.Response(200, json={"text": "hello"})

    p, c = make(handler, asr_base_urls="http://asr1/v1,http://asr2/v1")
    async with c:
        assert await p.transcribe(b"x", "a.wav", "audio/wav") == "hello"


async def test_tts_cache_serves_repeated_sentences():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=b"mp3-bytes", headers={"content-type": "audio/mpeg"})

    p, c = make(handler)
    async with c:
        assert await p.speak("Check-in is from 15:00.") == b"mp3-bytes"
        assert await p.speak("Check-in is from 15:00.") == b"mp3-bytes"
        await p.speak("Something else.")
    assert len(calls) == 2
