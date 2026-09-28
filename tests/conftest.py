import asyncio
import os
import uuid

import pytest

from app.booking.connectors.fake import reset_fake_store
from app.db import Base, make_engine, make_sessionmaker
from app.models import Organization, Property


PG_URL = os.environ.get("TEST_POSTGRES_URL")  # e.g. from: uv run python scripts/postgres.py


async def _admin(sql: str) -> None:
    import asyncpg

    from app.realtime import _asyncpg_kwargs

    kwargs = _asyncpg_kwargs(PG_URL) | {"database": "postgres"}
    conn = await asyncpg.connect(**kwargs)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture
def db_url(tmp_path):
    """A fresh empty database per test: SQLite file by default, a new Postgres database with
    TEST_POSTGRES_URL set, so the whole suite can run against real Postgres."""
    if not PG_URL:
        yield f"sqlite+aiosqlite:///{tmp_path}/test.db"
        return
    name = "t_" + uuid.uuid4().hex[:12]
    asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    base, _, query = PG_URL.partition("?")
    yield f"{base.rsplit('/', 1)[0]}/{name}" + (f"?{query}" if query else "")
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@pytest.fixture
async def sessionmaker(db_url):
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()
    reset_fake_store()


@pytest.fixture
async def session(sessionmaker):
    async with sessionmaker() as s:
        yield s


@pytest.fixture
async def hotels(session):
    org = Organization(name="Test Group", api_key_hash="0" * 64)
    session.add(org)
    await session.flush()
    a = Property(org_id=org.id, slug="hotel-a", name="Hotel A", timezone="Africa/Algiers",
                 currency="DZD", languages=["en", "ar", "fr"])
    b = Property(org_id=org.id, slug="hotel-b", name="Hotel B", timezone="Europe/Paris",
                 currency="EUR", languages=["en", "fr"])
    session.add_all([a, b])
    await session.commit()
    return a, b


@pytest.fixture(autouse=True)
def _reset_simulator():
    """Apps switch the simulator to their database; never let that leak into the next test."""
    yield
    reset_fake_store()
