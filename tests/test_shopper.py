import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import Conversation, Organization, Property, ShopBooking
from app.shopper.contracts import Guest, HotelResult, Recheck, StaySearch, SupplierOffer
from app.shopper.service import ShopService, merge_key
from app.shopper.suppliers.simulated import simulated_suppliers

GUEST = Guest(first_name="Amina", last_name="Haddad", email="amina@example.com")


def query(**kw):
    start = date.today() + timedelta(days=30)
    return StaySearch(**({"city": "Hurghada", "country_code": "EG", "check_in": start,
                          "check_out": start + timedelta(days=3), "adults": 2} | kw))


@pytest.fixture
async def shop(session):
    org = Organization(name="Travel", api_key_hash="1" * 64)
    session.add(org)
    await session.flush()
    prop = Property(org_id=org.id, slug="travel", name="Travel Shopper", connector={"type": "shopper"})
    session.add(prop)
    await session.flush()
    conv = Conversation(property_id=prop.id)
    session.add(conv)
    await session.commit()
    return ShopService(session, prop, simulated_suppliers()), conv.id


async def test_search_merges_sites_and_ranks_by_lowest_total(shop):
    svc, conv = shop
    s = await svc.search(conv, query())
    hotels = s.results
    assert len(hotels) >= 6
    totals = [Decimal(h["best_total"]) for h in hotels]
    assert totals == sorted(totals)  # cheapest hotel first
    best = hotels[0]
    assert best["sites"] >= 2 and Decimal(best["saving"]) >= 0
    assert best["offers"][0]["source"] == best["best_source"]  # offers cheapest first within a hotel
    assert {st["status"] for st in s.suppliers} == {"ok"}
    assert all(o["simulated"] for h in hotels for o in h["offers"])


async def test_filters_price_stars_refundable(shop):
    svc, conv = shop
    s = await svc.search(conv, query(max_price_per_night=Decimal("150"), min_stars=4, refundable_only=True))
    for h in s.results:
        assert h["stars"] >= 4
        for o in h["offers"]:
            assert Decimal(o["total"]) / 3 <= 150 and o["refundable"] is True


async def test_unknown_city_returns_no_hotels(shop):
    svc, conv = shop
    assert (await svc.search(conv, query(city="Atlantis", country_code="XX"))).results == []


async def test_best_of_rechecks_all_and_picks_cheapest_available(shop):
    svc, conv = shop
    s = await svc.search(conv, query())
    bookable = [o["offer_id"] for h in s.results[:4] for o in h["offers"] if o["kind"] == "bookable"][:6]
    q = await svc.quote_best_of(conv, bookable, GUEST)
    available = [c for c in q.compared if c["status"] == "available"]
    assert Decimal(q.terms["total"]) == min(Decimal(c["total"]) for c in available)
    assert len(q.compared) == len(bookable)


async def test_compare_only_offers_are_never_booked(shop):
    svc, conv = shop
    s = await svc.search(conv, query())
    redirect = next(o["offer_id"] for h in s.results for o in h["offers"] if o["kind"] == "redirect")
    with pytest.raises(Exception) as exc:
        await svc.quote_best_of(conv, [redirect], GUEST)
    assert "compare_only" in str(exc.value)


async def test_confirm_books_once_even_on_double_click(shop, session):
    svc, conv = shop
    s = await svc.search(conv, query())
    # Several candidates: the simulator deterministically sells some out on re-check (by date).
    offers = [o["offer_id"] for h in s.results for o in h["offers"] if o["kind"] == "bookable"][:6]
    q = await svc.quote_best_of(conv, offers, GUEST)
    first = await svc.confirm(conv, q.id)
    second = await svc.confirm(conv, q.id)
    assert first["state"] == "CONFIRMED" and first["reference"] == second["reference"]
    from sqlalchemy import func, select
    assert await session.scalar(select(func.count()).select_from(ShopBooking)) == 1


async def test_offer_from_another_conversation_is_rejected(shop, session):
    svc, conv = shop
    s = await svc.search(conv, query())
    other = Conversation(property_id=svc.prop.id)
    session.add(other)
    await session.commit()
    offer = s.results[0]["offers"][0]["offer_id"]
    with pytest.raises(Exception):
        await svc.quote_best_of(other.id, [offer], GUEST)


class BrokenSupplier:
    name, label, bookable, simulated, configured = "broken", "Broken", True, False, True

    async def search(self, q):
        raise RuntimeError("site down")


class SlowSupplier:
    name, label, bookable, simulated, configured = "slow", "Slow", True, False, True

    async def search(self, q):
        import asyncio
        await asyncio.sleep(5)


async def test_one_failing_or_slow_site_does_not_break_the_search(shop):
    svc, conv = shop
    svc = ShopService(svc.session, svc.prop, simulated_suppliers() + [BrokenSupplier(), SlowSupplier()], timeout=0.5)
    s = await svc.search(conv, query())
    status = {st["supplier"]: st["status"] for st in s.suppliers}
    assert status["broken"] == "error" and status["slow"] == "timeout" and s.results


def test_merge_key_matches_same_hotel_across_sites():
    assert merge_key("The Coral Lagoon Resort & Spa") == merge_key("Coral Lagoon")
    assert merge_key("Palm Marina Hotel") != merge_key("Marina Lights Hotel")


def test_search_validation():
    with pytest.raises(ValueError):
        query(check_out=date.today() + timedelta(days=30))  # zero nights
    assert query(country_code="eg").country_code == "EG"


# --- real adapters against their documented response formats (mocked HTTP) ---------------------

import httpx  # noqa: E402

from app.shopper.suppliers.google_hotels import GoogleHotelsSupplier  # noqa: E402
from app.shopper.suppliers.liteapi import LiteApiSupplier  # noqa: E402

LITE_RATES = {
    "data": [{"hotelId": "lp1897", "roomTypes": [{"offerId": "OFFER-1", "rates": [{
        "name": "Standard King Room", "boardName": "Room Only", "maxOccupancy": 2,
        "retailRate": {"total": [{"amount": 163.66, "currency": "USD"}]},
        "cancellationPolicies": {"refundableTag": "RFN",
                                 "cancelPolicyInfos": [{"cancelTime": "2026-10-25 02:00:00", "amount": 163.66}]}}]}]}],
    "hotels": [{"id": "lp1897", "name": "Hotel Example", "address": "1 Sea Road", "rating": 8.5, "stars": 4,
                "main_photo": "https://img/1.jpg"}],
}


async def test_liteapi_search_prebook_and_sandbox_book():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.url.host, request.url.path, request.headers.get("X-API-Key")))
        body = request.content and __import__("json").loads(request.content)
        if request.url.path.endswith("/hotels/rates"):
            assert body["cityName"] == "Hurghada" and body["countryCode"] == "EG"
            assert body["occupancies"] == [{"adults": 2, "children": [7]}]
            return httpx.Response(200, json=LITE_RATES)
        if request.url.path.endswith("/rates/prebook"):
            assert body == {"offerId": "OFFER-1", "usePaymentSdk": False}
            return httpx.Response(200, json={"data": {"prebookId": "PB-9", "price": 170.10, "currency": "USD",
                                                      "cancellationChanged": False, "boardChanged": False}})
        if request.url.path.endswith("/rates/book"):
            assert body["prebookId"] == "PB-9" and body["payment"] == {"method": "ACC_CREDIT_CARD"}
            assert body["clientReference"] == "idem-1" and body["guests"][0]["lastName"] == "Haddad"
            return httpx.Response(200, json={"data": {"bookingId": "BK-1", "status": "CONFIRMED",
                                                      "hotelConfirmationCode": "HC-77"}})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sup = LiteApiSupplier("sand-key", client)
        q = query(children_ages=[7])
        hotels = await sup.search(q)
        assert hotels[0].name == "Hotel Example" and hotels[0].stars == 4
        offer = hotels[0].offers[0]
        assert offer.total == Decimal("163.66") and offer.refundable and offer.kind == "bookable"
        rc = await sup.recheck(offer, q)
        assert rc.available and rc.total == Decimal("170.1") and rc.supplier_ref == "PB-9"
        out = await sup.book(offer, rc, q, GUEST, "idem-1")
        assert out.status == "CONFIRMED" and out.supplier_reference == "BK-1" and out.hotel_confirmation == "HC-77"
    assert {h for h, _, _ in seen} == {"api.liteapi.travel", "book.liteapi.travel"}
    assert all(k == "sand-key" for _, _, k in seen)


async def test_liteapi_timeout_on_book_is_unknown_not_failed():
    def handler(request):
        raise httpx.ReadTimeout("slow")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sup = LiteApiSupplier("k", client)
        offer = SupplierOffer(supplier="liteapi", source="LiteAPI", kind="bookable", supplier_offer_id="O",
                              room_name="R", total=Decimal("1"), currency="USD")
        out = await sup.book(offer, Recheck(available=True, supplier_ref="PB"), query(), GUEST, "k1")
        assert out.status == "UNKNOWN"  # never report failure when the supplier may have booked


async def test_google_hotels_lists_each_booking_site_price_with_link():
    def handler(request: httpx.Request):
        params = dict(request.url.params)
        if "property_token" in params:
            return httpx.Response(200, json={"featured_prices": [
                {"source": "Booking.com", "link": "https://booking.example/1", "total_rate": {"extracted_lowest": 300},
                 "rooms": [{"name": "Double Room"}]}],
                "prices": [
                {"source": "Expedia", "link": "https://expedia.example/1", "total_rate": {"extracted_lowest": 285},
                 "free_cancellation": True},
                {"source": "Booking.com", "link": "https://booking.example/2", "total_rate": {"extracted_lowest": 310}}]})
        assert params["engine"] == "google_hotels" and params["sort_by"] == "3"
        return httpx.Response(200, json={"properties": [
            {"name": "Sea Hotel", "property_token": "TOK", "extracted_hotel_class": 4, "overall_rating": 4.3,
             "reviews": 900, "gps_coordinates": {"latitude": 27.2, "longitude": 33.8}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        hotels = await GoogleHotelsSupplier("serp", client).search(query())
    offers = {o.source: o for o in hotels[0].offers}
    assert offers["Booking.com"].total == Decimal("300.00")  # cheapest per site kept
    assert offers["Expedia"].total == Decimal("285.00") and offers["Expedia"].refundable is True
    assert all(o.kind == "redirect" and o.link for o in offers.values())
    assert hotels[0].rating == 8.6  # 4.3/5 -> out of 10


async def test_saving_compares_the_same_room_type(shop):
    svc, conv = shop
    s = await svc.search(conv, query())
    for h in s.results:
        best = h["offers"][0]
        same = [Decimal(o["total"]) for o in h["offers"] if o["room_name"] == best["room_name"]]
        assert Decimal(h["saving"]) == max(same) - Decimal(best["total"])
        if h["bookable"]:
            assert h["best_bookable"]["total"] == next(o["total"] for o in h["offers"] if o["kind"] == "bookable")


# --- trivago (official MCP) against a real saved response ----------------------------------------

import pathlib  # noqa: E402

from app.shopper.suppliers.trivago import TrivagoSupplier, parse_price, parse_response  # noqa: E402

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "trivago_search.txt").read_text()


def test_trivago_price_parsing():
    assert parse_price("$1,234") == Decimal("1234")
    assert parse_price("€331") == Decimal("331")
    assert parse_price("EGP 12,345") == Decimal("12345")
    assert parse_price(None) is None and parse_price("n/a") is None


def test_trivago_response_parsing_ignores_the_instruction_text():
    items = parse_response(FIXTURE)
    assert len(items) == 4 and items[0]["accommodation_name"]
    assert "system_message" not in json.dumps(items)  # only the data array is used


async def test_trivago_search_maps_query_and_results():
    sent = {}

    async def fake_call(arguments):
        sent.update(arguments)
        return FIXTURE

    sup = TrivagoSupplier(market="EG", call=fake_call)
    q = query(children_ages=[7, 5], refundable_only=True, min_stars=4, currency="EUR")
    hotels = await sup.search(q)
    assert sent["query"] == "Hurghada" and sent["arrival"] == q.check_in.isoformat()
    assert sent["children"] == 2 and sent["children_ages"] == "7-5" and sent["country"] == "EG"
    assert sent["filters"] == {"freeCancellation": True} and sent["hotel_rating"] == {"4star": True, "5star": True}
    raw = parse_response(FIXTURE)
    first = hotels[0]
    assert first.name == raw[0]["accommodation_name"] and first.provider == "trivago"
    offer = first.offers[0]
    assert offer.kind == "redirect" and offer.via == "trivago" and offer.source == raw[0]["advertisers"]
    assert offer.total == parse_price(raw[0]["price_per_stay"]) and offer.link.startswith("https://")
    assert offer.simulated is False and first.amenities


async def test_live_search_service_with_trivago_adds_booking_and_expedia_links(session):
    org = Organization(name="T", api_key_hash="2" * 64)
    session.add(org)
    await session.flush()
    prop = Property(org_id=org.id, slug="t", name="T", connector={"type": "shopper"})
    session.add(prop)
    await session.flush()
    conv = Conversation(property_id=prop.id)
    session.add(conv)
    await session.commit()

    async def fake_call(arguments):
        return FIXTURE

    svc = ShopService(session, prop, [TrivagoSupplier(call=fake_call)])
    s = await svc.search(conv.id, query())
    labels = {link["label"] for link in s.results[0]["check_links"]}
    assert labels == {"Booking.com", "Expedia", "Google Hotels"}
    booking = next(l["url"] for l in s.results[0]["check_links"] if l["label"] == "Booking.com")
    assert "booking.com/searchresults.html" in booking and "checkin=" in booking
    assert all(not o["simulated"] for h in s.results for o in h["offers"])


async def test_trivago_offers_cannot_be_booked_in_app(session, shop):
    svc, conv = shop

    async def fake_call(arguments):
        return FIXTURE

    svc = ShopService(svc.session, svc.prop, [TrivagoSupplier(call=fake_call)])
    s = await svc.search(conv, query())
    with pytest.raises(Exception) as exc:
        await svc.quote_best_of(conv, [s.results[0]["offers"][0]["offer_id"]], GUEST)
    assert "compare_only" in str(exc.value)
