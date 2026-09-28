"""Change notifications for live guest updates (staff replies, typing, takeover).

Only "conversation X changed" signals travel through the broker; each subscriber then reads the
database. That keeps payloads tiny (Postgres NOTIFY is limited to 8000 bytes) and makes missed
signals harmless: the stream also refreshes periodically.

- InMemoryBroker: one API process.
- PostgresBroker: LISTEN/NOTIFY, so every API instance/worker hears every change.
"""

import asyncio
import contextlib
import json
import logging
from collections import defaultdict

log = logging.getLogger("hotel_agent.realtime")
CHANNEL = "hotel_agent_events"


class InMemoryBroker:
    def __init__(self):
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def _deliver(self, conversation_id: str, kind: str) -> None:
        for q in list(self._subs.get(conversation_id, ())):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(kind)

    async def publish(self, conversation_id: str, kind: str) -> None:
        self._deliver(conversation_id, kind)

    def subscribe(self, conversation_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._subs[conversation_id].add(q)
        return q

    def unsubscribe(self, conversation_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(conversation_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(conversation_id, None)

    @property
    def subscriber_count(self) -> int:
        return sum(len(s) for s in self._subs.values())


class PostgresBroker(InMemoryBroker):
    """Fan-out across processes with LISTEN/NOTIFY on one dedicated connection per process."""

    def __init__(self, dsn: str):
        super().__init__()
        self.dsn = dsn
        self._conn = None

    async def start(self) -> None:
        import asyncpg

        self._conn = await asyncpg.connect(**_asyncpg_kwargs(self.dsn))
        await self._conn.add_listener(CHANNEL, self._on_notify)
        log.info("realtime: listening on Postgres channel %s", CHANNEL)

    async def stop(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        try:
            data = json.loads(payload)
            self._deliver(data["c"], data["k"])
        except (ValueError, KeyError, TypeError):
            log.warning("realtime: ignored malformed notification")

    async def publish(self, conversation_id: str, kind: str) -> None:
        payload = json.dumps({"c": conversation_id, "k": kind})
        try:
            await self._conn.execute("SELECT pg_notify($1, $2)", CHANNEL, payload)
        except Exception:  # noqa: BLE001 - never fail a staff action because of a notification
            log.warning("realtime: NOTIFY failed; delivering locally only")
            self._deliver(conversation_id, kind)


def _asyncpg_kwargs(url: str) -> dict:
    """Convert a SQLAlchemy URL (postgresql+asyncpg://user:pw@host:port/db?host=/socket) for asyncpg."""
    from sqlalchemy.engine import make_url

    u = make_url(url)
    kwargs = {"user": u.username, "password": u.password, "database": u.database, "port": u.port}
    kwargs["host"] = u.query.get("host") or u.host
    return {k: v for k, v in kwargs.items() if v}


def make_broker(database_url: str):
    return PostgresBroker(database_url) if database_url.startswith("postgresql") else InMemoryBroker()
