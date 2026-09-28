import pytest

from app.booking.connectors.fake import reset_fake_store
from app.db import Base, make_engine, make_sessionmaker
from app.models import Organization, Property


@pytest.fixture
async def sessionmaker(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
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
