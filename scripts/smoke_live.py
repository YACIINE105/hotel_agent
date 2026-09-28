"""Live end-to-end check against running servers (API, LLM, and optionally ASR/TTS).

    uv run python scripts/smoke_live.py [--voice]

Prints latency for each turn: first delta (what the guest sees) and first audio chunk (what they hear).
"""

import asyncio
import base64
import json
import sys
import time
from datetime import date, timedelta

import httpx
import websockets

API = "http://127.0.0.1:8000"
HOTEL = "atlas-bay"


async def session(client, language="en"):
    r = await client.post(f"{API}/v1/properties/{HOTEL}/sessions", json={"language": language})
    r.raise_for_status()
    s = r.json()
    return s["conversation_id"], {"Authorization": f"Bearer {s['token']}"}, s["token"]


async def turn(client, conv, headers, text):
    t0 = time.perf_counter()
    first = None
    events, answer = [], ""
    async with client.stream("POST", f"{API}/v1/conversations/{conv}/messages", json={"text": text},
                             headers=headers, timeout=120) as r:
        r.raise_for_status()
        buf = ""
        async for chunk in r.aiter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                data = json.loads(next(l[6:] for l in block.split("\n") if l.startswith("data: ")))
                events.append(data)
                if data["type"] == "delta":
                    first = first or time.perf_counter() - t0
                    answer += data["text"]
    total = time.perf_counter() - t0
    kinds = sorted({e["type"] for e in events} - {"delta", "sentence"})
    print(f"\nGUEST: {text}\nAGENT: {answer.strip()}\n  events={kinds} first_text={first and round(first, 2)}s total={total:.2f}s")
    return events


async def text_flow():
    async with httpx.AsyncClient(trust_env=False) as client:
        conv, h, _ = await session(client)
        await turn(client, conv, h, "Hi! What time is breakfast, and is parking free?")
        ci = date.today() + timedelta(days=21)
        events = await turn(client, conv, h,
                            f"Do you have a room for 2 adults and one child aged 5 from {ci} to {ci + timedelta(days=2)}?")
        offers = next((e["offers"] for e in events if e["type"] == "offers"), None)
        if not offers:
            print("!! no offers returned")
            return
        pick = offers[0]
        events = await turn(client, conv, h,
                            f"I'll take the {pick['room_name']} {pick['rate_plan']}. Name: Amina Haddad, email amina@example.com")
        quote = next((e["quote"] for e in events if e["type"] == "quote"), None)
        if not quote:
            print("!! no quote card; the model may have asked a follow-up question")
            return
        r = await client.post(f"{API}/v1/conversations/{conv}/bookings/confirm", json={"quote_id": quote["quote_id"]}, headers=h)
        print("CONFIRM:", r.json())
        await turn(client, conv, h, "Thanks! Is my booking done?")
        conv2, h2, _ = await session(client, "ar")
        await turn(client, conv2, h2, "مرحبا، هل يوجد مسبح في الفندق؟")
        conv3, h3, _ = await session(client, "fr")
        await turn(client, conv3, h3, "Bonjour, quelle est l'heure d'arrivée ?")


async def voice_flow():
    async with httpx.AsyncClient(trust_env=False) as client:
        tts = await client.post("http://127.0.0.1:8002/v1/audio/speech", timeout=60, json={
            "model": "oddadmix/Kokoro-7M-Distill", "voice": "af_msa", "response_format": "wav",
            "input": "Hello. Do you have free parking, and what time is check out?"})
        tts.raise_for_status()
        conv, _, token = await session(client)
    async with websockets.connect(f"ws://127.0.0.1:8000/v1/conversations/{conv}/voice?token={token}",
                                  max_size=20_000_000, proxy=None) as ws:
        t0 = time.perf_counter()
        await ws.send(tts.content)
        await ws.send(json.dumps({"type": "utterance_end", "mime": "audio/wav"}))
        marks = {}
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 120))
            now = round(time.perf_counter() - t0, 2)
            if msg["type"] == "transcript":
                marks["transcript"] = now
                print(f"\nTRANSCRIPT ({now}s): {msg['text']}")
            elif msg["type"] == "sentence":
                marks.setdefault("first_sentence", now)
                print(f"  sentence @{now}s: {msg['text']}")
            elif msg["type"] == "audio":
                marks.setdefault("first_audio", now)
                print(f"  audio #{msg['seq']} @{now}s ({len(base64.b64decode(msg['data']))} bytes): {msg['text']}")
            elif msg["type"] in ("turn_end",):
                marks["turn_end"] = now
                break
            elif msg["type"] == "error":
                print("  ERROR", msg)
        print("VOICE LATENCY (s from end of speech):", marks)


async def main():
    await text_flow()
    if "--voice" in sys.argv:
        await voice_flow()


asyncio.run(main())
