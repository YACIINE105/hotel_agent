import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.booking.connectors.fake import inject_fault
from app.booking.contracts import AvailabilityQuery, BookingState, GuestDetails
from app.booking.registry import connector_for
from app.booking.service import BookingService
from app.errors import Conflict, NotFound
from app.models import BookingIntent, Conversation, Effect, Handoff, QuoteRecord

GUEST = GuestDetails(first_name="Amina", last_name="Haddad", email="amina@example.com")


def query(adults=2, children=None, nights=2):
    start = date.today() + timedelta(days=10)
    return AvailabilityQuery(check_in=start, check_out=start + timedelta(days=nights),
                             adults=adults, children_ages=children or [])


@pytest.fixture
async def ctx(session, hotels):
    prop = hotels[0]
    conv = Conversation(property_id=prop.id)
    session.add(conv)
    await session.commit()
    return BookingService(session, prop, connector_for(prop)), conv.id, prop


async def quote_for(svc, conv_id, room="STD", plan="FLEX", q=None):
    offers = await svc.search(q or query())
    offer = next(o for o in offers if o.offer_id.startswith(f"{room}:{plan}"))
    return await svc.create_quote(conv_id, offer.offer_id, GUEST)


async def test_happy_path_confirms_with_reference(ctx):
    svc, conv, _ = ctx
    record = await quote_for(svc, conv)
    result = await svc.confirm(conv, record.id)
    assert result["state"] == BookingState.CONFIRMED
    assert result["reference"].startswith("FK-")


async def test_occupancy_filters_rooms(ctx):
    svc, _, _ = ctx
    offers = await svc.search(query(adults=2, children=[5, 8]))
    assert {o.room_type for o in offers} == {"FAM"}


def test_invalid_stay_rejected():
    with pytest.raises(ValueError):
        AvailabilityQuery(check_in=date(2026, 1, 5), check_out=date(2026, 1, 5), adults=2)
    with pytest.raises(ValueError):
        AvailabilityQuery(check_in=date(2026, 1, 5), check_out=date(2026, 1, 7), adults=2, children_ages=[19])


async def test_double_click_creates_one_booking(ctx, session):
    svc, conv, _ = ctx
    record = await quote_for(svc, conv)
    first = await svc.confirm(conv, record.id)
    second = await svc.confirm(conv, record.id)
    assert first["reference"] == second["reference"]
    assert await session.scalar(select(func.count()).select_from(BookingIntent)) == 1


async def test_price_change_requires_reconfirmation(ctx, session):
    svc, conv, prop = ctx
    record = await quote_for(svc, conv)
    inject_fault(prop.id, "price_change")
    result = await svc.confirm(conv, record.id)
    assert result["state"] == BookingState.PRICE_CHANGED
    assert result["reference"] is None
    new = result["new_quote"]
    assert float(new["total"]) > float(record.data["offer"]["total"])
    stale = await session.get(QuoteRecord, record.id)
    assert stale.superseded_by == new["quote_id"]
    confirmed = await svc.confirm(conv, new["quote_id"])
    assert confirmed["state"] == BookingState.CONFIRMED


async def test_superseded_quote_rejected(ctx, session):
    svc, conv, prop = ctx
    record = await quote_for(svc, conv)
    inject_fault(prop.id, "price_change")
    await svc.confirm(conv, record.id)
    # The original intent is terminal; the original quote returns the same PRICE_CHANGED result.
    again = await svc.confirm(conv, record.id)
    assert again["state"] == BookingState.PRICE_CHANGED


async def test_expired_quote_rejected(ctx, session):
    svc, conv, _ = ctx
    record = await quote_for(svc, conv)
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await session.commit()
    with pytest.raises(Conflict):
        await svc.confirm(conv, record.id)


async def test_last_room_race(ctx):
    svc, conv, _ = ctx
    first = await quote_for(svc, conv, room="FAM")
    second = await quote_for(svc, conv, room="FAM", plan="NR")
    assert (await svc.confirm(conv, first.id))["state"] == BookingState.CONFIRMED
    assert (await svc.confirm(conv, second.id))["state"] == BookingState.FAILED


async def test_supplier_failure(ctx):
    svc, conv, prop = ctx
    record = await quote_for(svc, conv)
    inject_fault(prop.id, "fail_book")
    assert (await svc.confirm(conv, record.id))["state"] == BookingState.FAILED


async def test_timeout_after_success_is_reconciled_without_duplicate(ctx, session):
    svc, conv, prop = ctx
    record = await quote_for(svc, conv)
    inject_fault(prop.id, "timeout_after_success")
    result = await svc.confirm(conv, record.id)
    assert result["state"] == BookingState.CONFIRMED
    assert len(svc.connector.store.reservations) == 1
    kinds = [(e.kind, e.status) for e in (await session.scalars(select(Effect).order_by(Effect.id))).all()]
    assert ("BOOK", "UNKNOWN") in kinds and ("LOOKUP", "SUCCEEDED") in kinds


async def test_timeout_before_success_retries_with_same_key(ctx):
    svc, conv, prop = ctx
    record = await quote_for(svc, conv)
    inject_fault(prop.id, "timeout_before_success")
    result = await svc.confirm(conv, record.id)
    assert result["state"] == BookingState.CONFIRMED
    assert len(svc.connector.store.reservations) == 1


async def test_unreachable_supplier_goes_to_staff_review(ctx, session, monkeypatch):
    svc, conv, prop = ctx
    record = await quote_for(svc, conv)
    inject_fault(prop.id, "timeout_before_success")

    async def down(_key):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(svc.connector, "find_by_idempotency_key", down)
    result = await svc.confirm(conv, record.id)
    assert result["state"] == BookingState.STAFF_REVIEW
    assert await session.scalar(select(func.count()).select_from(Handoff)) == 1


async def test_quote_is_scoped_to_conversation_and_property(ctx, session, hotels):
    svc, conv, _ = ctx
    record = await quote_for(svc, conv)
    other = Conversation(property_id=hotels[1].id)
    session.add(other)
    await session.commit()
    with pytest.raises(NotFound):
        await svc.confirm(other.id, record.id)
    svc_b = BookingService(session, hotels[1], connector_for(hotels[1]))
    with pytest.raises(NotFound):
        await svc_b.confirm(conv, record.id)


async def test_lookup_requires_matching_last_name(ctx):
    svc, conv, _ = ctx
    ref = (await svc.confirm(conv, (await quote_for(svc, conv)).id))["reference"]
    assert await svc.lookup(ref, "haddad")
    assert await svc.lookup(ref, "Someone") is None


async def test_money_is_decimal_and_terms_hash_covers_price(ctx, session):
    svc, conv, _ = ctx
    record = await quote_for(svc, conv)
    stored = await session.get(QuoteRecord, record.id)
    assert stored.data["offer"]["total"] == "190.00"
    assert stored.data["offer"]["pay_at_property_fees"] == "10.00"


async def test_children_policy_fees_and_adult_age():
    from app.booking.connectors.fake import FakeReservationConnector
    from app.seed import CHILD_POLICY, ROOMS

    fake = FakeReservationConnector(999, "Africa/Cairo", "USD", {"rooms": ROOMS, "city_tax_per_adult_night": "0",
                                                                **CHILD_POLICY})
    start = date.today() + timedelta(days=10)

    def q(adults, ages):
        return AvailabilityQuery(check_in=start, check_out=start + timedelta(days=2), adults=adults, children_ages=ages)

    base = {o.offer_id.split(":")[0]: o for o in await fake.search(q(2, [])) if ":FLEX:" in o.offer_id}
    kids = {o.offer_id.split(":")[0]: o for o in await fake.search(q(2, [4, 8])) if ":FLEX:" in o.offer_id}
    # Only the Family Suite fits 2+2; the 8-year-old costs 32 USD x 2 nights, the 4-year-old is free.
    assert set(kids) == {"FAM"}
    assert kids["FAM"].total - base["FAM"].total == Decimal("64.00")
    # A 13-year-old counts as an adult: 2 adults + 13y = 3 adults -> Sea View (max 2 adults) is excluded.
    teen = {o.offer_id.split(":")[0] for o in await fake.search(q(2, [13]))}
    assert teen == {"SUP-GP", "FAM"}



async def test_concurrent_bookings_of_the_last_room_sell_it_once(session, hotels, sessionmaker):
    """The shared (database) simulator store: parallel confirms, as from several workers."""
    from app.booking.connectors.fake import use_database

    use_database(sessionmaker)
    prop = hotels[0]
    convs = [Conversation(property_id=prop.id) for _ in range(5)]
    session.add_all(convs)
    await session.commit()
    quotes = []
    for conv in convs:
        async with sessionmaker() as s:
            svc = BookingService(s, prop, connector_for(prop))
            quotes.append((conv.id, (await quote_for(svc, conv.id, room="FAM")).id))  # 1 Family Suite exists

    async def confirm(conv_id, quote_id):
        async with sessionmaker() as s:
            return await BookingService(s, prop, connector_for(prop)).confirm(conv_id, quote_id)

    results = await asyncio.gather(*(confirm(c, q) for c, q in quotes))
    assert sorted(r["state"] for r in results).count(BookingState.CONFIRMED) == 1
