"""Local OpenAI-compatible speech endpoint for the pinned Kokoro 7M student."""

import asyncio
import importlib.util
import io
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import lameenc
import numpy as np
import soundfile as sf
import torch
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

MODEL = "oddadmix/Kokoro-7M-Distill"
REVISION = "9357717e6499717b769ab65de61797aa7c501c86"
RATE = 24000
model_root = Path(
    os.environ.get(
        "KOKORO_MODEL_DIR", str(Path.home() / ".cache/answerly/kokoro-7m" / REVISION)
    )
)


@asynccontextmanager
async def lifespan(app):
    # Separate CPU environment keeps Qwen's GPU allocation unchanged.
    torch.set_num_threads(int(os.environ.get("KOKORO_THREADS", "4")))
    try:
        import espeakng_loader
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        EspeakWrapper.set_library(espeakng_loader.get_library_path())
    except ImportError:
        pass
    spec = importlib.util.spec_from_file_location(
        "answerly_kokoro_loader", model_root / "load_model.py"
    )
    loader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loader)
    app.state.model, app.state.pipeline, app.state.voice = await run_in_threadpool(
        loader.load, device="cpu"
    )
    app.state.lock = asyncio.Semaphore(1)
    yield


app = FastAPI(title="Answerly Kokoro TTS", lifespan=lifespan)


class SpeechInput(BaseModel):
    model: Literal["oddadmix/Kokoro-7M-Distill"] = MODEL
    input: str = Field(min_length=1, max_length=10000)
    voice: Literal["af_msa"] = "af_msa"
    response_format: Literal["mp3", "wav"] = "mp3"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    model_config = ConfigDict(extra="forbid")


def authorized(authorization: str | None = Header(default=None)):
    key = os.environ.get("TTS_SERVER_API_KEY", "")
    if key:
        import secrets

        if not authorization or not secrets.compare_digest(
            authorization, "Bearer " + key
        ):
            raise HTTPException(401, "Invalid TTS credentials")


def synthesize(data):
    if not data.input.strip():
        raise HTTPException(422, "Speech text must not be blank")
    segments = []
    samples = 0
    with torch.inference_mode():
        for _, _, audio in app.state.pipeline(
            data.input, voice=app.state.voice, speed=data.speed
        ):
            if audio is not None:
                values = (
                    audio.detach().cpu().numpy()
                    if isinstance(audio, torch.Tensor)
                    else np.asarray(audio)
                )
                samples += values.size
                if samples > RATE * 180:
                    raise HTTPException(
                        422, "Speech exceeds three minutes; split the input"
                    )
                segments.append(values)
    if not segments:
        raise HTTPException(422, "No speech produced for this text")
    audio = np.concatenate(segments)
    if data.response_format == "wav":
        buffer = io.BytesIO()
        sf.write(buffer, audio, RATE, format="WAV", subtype="PCM_16")
        return buffer.getvalue(), "audio/wav"
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(128)
    encoder.set_in_sample_rate(RATE)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return bytes(encoder.encode(pcm) + encoder.flush()), "audio/mpeg"


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model": MODEL,
        "voice": "af_msa",
        "device": "cpu",
        "sample_rate": RATE,
    }


@app.get("/v1/models", dependencies=[Depends(authorized)])
async def models():
    return {
        "object": "list",
        "data": [{"id": MODEL, "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/audio/speech", dependencies=[Depends(authorized)])
async def speech(data: SpeechInput):
    async with app.state.lock:
        audio, mime = await run_in_threadpool(synthesize, data)
    return Response(audio, media_type=mime, headers={"Cache-Control": "no-store"})
