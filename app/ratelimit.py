"""Fixed-window rate limits so one visitor (or script) can't exhaust model capacity.

- Postgres: counters in `rate_limit_counters` via one UPSERT, shared by all workers and instances.
- SQLite (single process): in-memory counters.
"""

import random
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.errors import AppError


class RateLimited(AppError):
    status_code, code = 429, "rate_limited"

    def __init__(self, retry_after: int):
        super().__init__(f"Too many requests; try again in {retry_after} s")
        self.retry_after = retry_after


# (name, limit, window seconds). Generous for real guests, tight enough to stop a script.
LIMITS = {
    "session_ip": (20, 60),        # new conversations per IP per minute
    "message_conv": (15, 60),      # chat turns per conversation per minute
    "message_ip": (60, 60),        # chat turns per IP per minute (many tabs / conversations)
    "booking_conv": (30, 60),      # availability/quote/confirm calls per conversation per minute
    "voice_conv": (20, 60),        # voice utterances per conversation per minute
}


class MemoryCounters:
    def __init__(self, max_keys: int = 50_000):
        self.counts: OrderedDict[tuple[str, int], int] = OrderedDict()
        self.max_keys = max_keys

    async def incr(self, key: str, window_start: int, window: int) -> int:
        k = (key, window_start)
        self.counts[k] = self.counts.get(k, 0) + 1
        self.counts.move_to_end(k)
        while len(self.counts) > self.max_keys:
            self.counts.popitem(last=False)
        return self.counts[k]


class PostgresCounters:
    def __init__(self, sessionmaker):
        self.sm = sessionmaker

    async def incr(self, key: str, window_start: int, window: int) -> int:
        from app.models import RateLimitCounter

        start = datetime.fromtimestamp(window_start, timezone.utc)
        stmt = (pg_insert(RateLimitCounter).values(key=key, window_start=start, count=1)
                .on_conflict_do_update(index_elements=["key", "window_start"],
                                       set_={"count": RateLimitCounter.count + 1})
                .returning(RateLimitCounter.count))
        async with self.sm() as s:
            count = (await s.execute(stmt)).scalar_one()
            if random.random() < 0.01:  # occasional cleanup of old windows
                await s.execute(delete(RateLimitCounter).where(
                    RateLimitCounter.window_start < datetime.now(timezone.utc) - timedelta(hours=1)))
            await s.commit()
            return count


class RateLimiter:
    def __init__(self, counters, enabled: bool = True):
        self.counters, self.enabled = counters, enabled

    async def hit(self, name: str, subject: str) -> None:
        if not self.enabled:
            return
        limit, window = LIMITS[name]
        now = time.time()
        window_start = int(now // window * window)
        count = await self.counters.incr(f"{name}:{subject}", window_start, window)
        if count > limit:
            raise RateLimited(max(1, int(window_start + window - now) + 1))


def make_limiter(engine, sessionmaker, enabled: bool) -> RateLimiter:
    counters = PostgresCounters(sessionmaker) if engine.dialect.name == "postgresql" else MemoryCounters()
    return RateLimiter(counters, enabled)


def client_ip(request: Request, trust_forwarded: bool) -> str:
    if trust_forwarded:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
