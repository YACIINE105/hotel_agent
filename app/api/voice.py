"""Voice channel over WebSocket: ASR -> streamed agent turn -> sequenced TTS.

Client -> server: binary audio frames for one utterance, then {"type": "utterance_end", "mime": ...};
{"type": "text", "text": ...} for typed input; {"type": "interrupt"} to barge in.
Server -> client: transcript, agent events (status/delta/sentence/offers/quote/...), audio, audio_end, turn_end.
"""

import asyncio
import contextlib
import json
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.agent import language as lang
from app.agent.orchestrator import TurnRunner
from app.api.deps import guest_context
from app.errors import AppError, Unavailable
from app.voice.pipeline import SequencedSpeaker

router = APIRouter(prefix="/v1", tags=["voice"])
MAX_UTTERANCE_BYTES = 5 * 1024 * 1024
AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav", "audio/mpeg"}


@router.websocket("/conversations/{conversation_id}/voice")
async def voice(ws: WebSocket, conversation_id: str, token: str = ""):
    app = ws.app
    settings, providers = app.state.settings, app.state.providers
    async with app.state.sessionmaker() as session:
        try:
            prop, conv = await guest_context(session, settings, conversation_id, token)
            languages, conv_language = list(prop.languages), conv.language
        except AppError:
            await ws.close(code=4403)
            return
    await ws.accept()

    lock = asyncio.Lock()

    async def send(message: dict) -> None:
        async with lock:
            await ws.send_json(message)

    turn: asyncio.Task | None = None
    audio = bytearray()

    async def run_turn(text: str) -> None:
        speaker = SequencedSpeaker(providers.speak, send, settings.tts_prefetch)
        try:
            async with app.state.sessionmaker() as session:
                prop, conv = await guest_context(session, settings, conversation_id, token)
                runner = TurnRunner(session, providers, settings, prop, conv)
                async for event in runner.run(text, str(uuid4())):
                    await send(event)
                    # run() sets conv.language (detected or chosen) before its first event.
                    if conv.language not in settings.tts_language_set:
                        continue
                    if event["type"] == "sentence":
                        speaker.say(event["text"])
                    elif event["type"] in ("status", "paused", "error"):
                        speaker.say(event.get("text") or event.get("detail", ""), kind=event["type"])
            await speaker.finish()
        except asyncio.CancelledError:
            await speaker.cancel()
            raise
        except Exception:
            await speaker.cancel()
            await send({"type": "error", "detail": "The voice turn failed"})
        await send({"type": "turn_end"})

    async def cancel_turn() -> None:
        nonlocal turn
        if turn and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await turn
            await send({"type": "interrupted"})
        turn = None

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                audio.extend(message["bytes"])
                if len(audio) > MAX_UTTERANCE_BYTES:
                    audio.clear()
                    await send({"type": "error", "detail": "Utterance too long"})
                continue
            try:
                data = json.loads(message.get("text") or "{}")
            except ValueError:
                continue
            kind = data.get("type")
            if kind == "interrupt":
                await cancel_turn()
                audio.clear()
            elif kind == "utterance_end":
                payload, mime = bytes(audio), str(data.get("mime", "audio/webm")).split(";")[0]
                audio.clear()
                if not payload or mime not in AUDIO_TYPES:
                    await send({"type": "error", "detail": "No usable audio received"})
                    continue
                await cancel_turn()  # a new utterance always replaces the current answer
                try:
                    text = await providers.transcribe(payload, "utterance." + mime.split("/")[1], mime)
                except Unavailable:
                    await send({"type": "error", "detail": "Speech recognition is unavailable"})
                    continue
                if not lang.plausible_transcript(text, languages):
                    # Likely noise or echo: do not bother the model, ask the guest to repeat.
                    await send({"type": "transcript", "text": text, "rejected": True})
                    await send({"type": "error", "detail": lang.localized(lang.NOT_UNDERSTOOD, conv_language)})
                    await send({"type": "turn_end"})
                    continue
                await send({"type": "transcript", "text": text})
                turn = asyncio.create_task(run_turn(text))
            elif kind == "text" and str(data.get("text", "")).strip():
                await cancel_turn()
                turn = asyncio.create_task(run_turn(str(data["text"])[:2000]))
    except WebSocketDisconnect:
        pass
    finally:
        if turn and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await turn
