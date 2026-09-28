"""Sequenced speech streaming.

Sentences are synthesized as soon as the model finishes them (up to `prefetch` at once) and
played strictly in order, so the guest hears the first sentence while later ones are still
being generated. Interruption cancels everything that has not been sent.
"""

import asyncio
import base64
from collections.abc import Awaitable, Callable

from app.errors import Unavailable

Send = Callable[[dict], Awaitable[None]]


class SequencedSpeaker:
    def __init__(self, speak: Callable[[str], Awaitable[bytes]], send: Send, prefetch: int = 2):
        self.speak, self.send = speak, send
        self.gate = asyncio.Semaphore(max(1, prefetch))
        self.order: asyncio.Queue = asyncio.Queue()
        self.seq = 0
        self.pending: list[asyncio.Task] = []
        self.sender = asyncio.create_task(self._send_in_order())

    async def _synthesize(self, text: str) -> bytes | None:
        async with self.gate:
            try:
                return await self.speak(text)
            except Unavailable:
                return None  # text was already shown; skip audio for this segment

    def say(self, text: str, kind: str = "speech") -> None:
        task = asyncio.create_task(self._synthesize(text))
        self.pending.append(task)
        self.order.put_nowait((self.seq, text, kind, task))
        self.seq += 1

    async def _send_in_order(self) -> None:
        while True:
            item = await self.order.get()
            if item is None:
                return
            seq, text, kind, task = item
            audio = await task
            if audio:
                await self.send({"type": "audio", "seq": seq, "kind": kind, "text": text,
                                 "mime": "audio/mpeg", "data": base64.b64encode(audio).decode()})

    async def finish(self) -> None:
        self.order.put_nowait(None)
        await self.sender
        await self.send({"type": "audio_end"})

    async def cancel(self) -> None:
        for task in self.pending + [self.sender]:
            task.cancel()
        await asyncio.gather(*self.pending, self.sender, return_exceptions=True)
