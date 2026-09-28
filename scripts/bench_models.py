"""Time-to-first-token and tool-call correctness per model, using the real agent prompt.

    uv run python scripts/bench_models.py model1 model2:off ...   (":off"/":low" sets reasoning)
"""

import asyncio
import json
import pathlib
import sys
import time
from datetime import date, timedelta

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agent.tools import tool_definitions
from app.booking.connectors.fake import FakeReservationConnector
from app.config import get_settings
from app.providers import Providers

PROMPTS = {
    "faq": "What time is breakfast and is parking free?",
    "tool": f"Room for 2 adults from {date.today() + timedelta(days=20)} to {date.today() + timedelta(days=22)}?",
}
SYSTEM = ("You are the front desk of Atlas Bay Hotel. Be brief, plain text. Facts: breakfast 06:30-10:30 included; "
          "free on-site parking. Use search_availability for availability; never invent prices. Today is "
          + date.today().isoformat())


async def one(providers, prompt):
    t0 = time.perf_counter()
    first, text, tools = None, "", []
    async for kind, payload in providers.stream_chat(
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            tool_definitions(FakeReservationConnector.capabilities)):
        first = first or time.perf_counter() - t0
        if kind == "text":
            text += payload
        else:
            tools = [c["name"] for c in payload]
    return first, time.perf_counter() - t0, text.strip()[:70], tools


async def main():
    base = get_settings()
    async with httpx.AsyncClient() as client:
        for spec in sys.argv[1:]:
            model, _, reasoning = spec.partition(":")
            providers = Providers(base.model_copy(update={"llm_model": model, "llm_reasoning": reasoning}), client)
            for name, prompt in PROMPTS.items():
                runs = []
                for _ in range(2):
                    try:
                        runs.append(await one(providers, prompt))
                    except Exception as exc:  # report and continue with the next model
                        runs.append((None, None, f"ERROR {exc}", []))
                best = min((r for r in runs if r[0]), default=runs[-1], key=lambda r: r[0])
                ok = ("search_availability" in best[3]) if name == "tool" else bool(best[2])
                print(f"{spec:42} {name:4} first={best[0] and round(best[0], 2)}s total={best[1] and round(best[1], 2)}s "
                      f"ok={ok} tools={best[3]} text={best[2]!r}")


asyncio.run(main())
