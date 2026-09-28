"""trivago via its official MCP server (https://mcp.trivago.com/mcp): live hotel prices aggregated
from booking sites (Booking.com, Trip.com, hotel websites, ...). Free, no API key.

Each result is one hotel with its cheapest current deal and the site offering it (`advertisers`);
the guest completes the booking through the trivago link. These are compare-only (redirect) offers.

trivago's response carries presentation rules for AI assistants: show each accommodation as its own
card (price per stay / per night, stars, guest rating with review count, amenities, "View on
trivago" link), not as a comparison grid. The /shop page follows them. We only parse the JSON data
from the response; its free text is never passed to our model.
"""

import json
import re
from decimal import Decimal, InvalidOperation

from app.shopper.contracts import BookingOutcome, Guest, HotelResult, Recheck, StaySearch, SupplierOffer

MCP_URL = "https://mcp.trivago.com/mcp"
TOOL = "trivago-accommodation-search"
MARKETS = set("AE AR AT AU BE BR CA CH CL CO CZ DE DK EG ES FI FR GB GR HK HU ID IE IL IN IT JP KR MX MY NL NO "
              "NZ PE PH PL PT RO RU SA SE SG TH TR TW UA US VN ZA".split())
CURRENCIES = set("AED ARS AUD BRL CAD CHF CLP CNY COP CZK DKK EGP EUR GBP HKD HUF IDR ILS INR JPY KRW MXN MYR NOK "
                 "NZD PEN PHP PLN RON RUB SAR SEK SGD THB TRY TWD UAH USD VND ZAR".split())


def parse_price(text: str | None) -> Decimal | None:
    """'$1,234' / '€331' / 'EGP 12,345' -> Decimal. trivago EN_US uses ',' for thousands."""
    if not text:
        return None
    digits = re.sub(r"[^\d.,]", "", str(text)).replace(",", "")
    try:
        return Decimal(digits) if digits else None
    except InvalidOperation:
        return None


def parse_response(text: str) -> list[dict]:
    """The tool returns an instruction preamble, then JSON {"output": "<json array>", "system_message": ...}."""
    start = text.find("{")
    if start < 0:
        return []
    wrapper, _ = json.JSONDecoder().raw_decode(text[start:])
    output = wrapper.get("output")
    items = json.loads(output) if isinstance(output, str) else output
    return items if isinstance(items, list) else []


async def mcp_call(arguments: dict, timeout: float = 20) -> str:
    """One MCP session per search (connect ~0.4 s; search ~6 s)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(MCP_URL) as streams:
        async with ClientSession(streams[0], streams[1], read_timeout_seconds=timeout) as session:
            await session.initialize()
            result = await session.call_tool(TOOL, arguments)
            if result.is_error:
                raise RuntimeError("trivago search returned an error")
            texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
            return texts[0] if texts else ""


class TrivagoSupplier:
    name = "trivago"
    label = "trivago"
    bookable = False
    simulated = False

    def __init__(self, market: str = "US", call=None):
        self.market = market if market in MARKETS else "US"
        self._call = call or mcp_call  # injectable for tests

    @property
    def configured(self) -> bool:
        return True  # no key needed

    def arguments(self, q: StaySearch) -> dict:
        args = {"query": q.city, "arrival": q.check_in.isoformat(), "departure": q.check_out.isoformat(),
                "adults": q.adults, "rooms": 1, "country": self.market,
                "currency": q.currency if q.currency in CURRENCIES else "USD"}
        if q.children_ages:
            args["children"] = len(q.children_ages)
            args["children_ages"] = "-".join(str(a) for a in q.children_ages)
        if q.refundable_only:
            args["filters"] = {"freeCancellation": True}
        if q.min_stars:
            args["hotel_rating"] = {f"{s}star": True for s in range(q.min_stars, 6)}
        return args

    async def search(self, query: StaySearch) -> list[HotelResult]:
        items = parse_response(await self._call(self.arguments(query)))
        results = []
        for h in items:
            total = parse_price(h.get("price_per_stay"))
            if total is None:
                continue
            try:
                rating = float(h["review_rating"]) if h.get("review_rating") else None
            except (TypeError, ValueError):
                rating = None
            reviews = int(re.sub(r"\D", "", str(h.get("review_count") or "")) or 0) or None
            source = (h.get("advertisers") or "trivago").strip()
            offer = SupplierOffer(
                supplier=self.name, source=source, via="trivago", kind="redirect",
                supplier_offer_id=str(h.get("accommodation_id", "")), room_name="Best current deal",
                refundable=True if query.refundable_only else None, total=total,
                currency=h.get("currency") or query.currency, link=h.get("accommodation_url"))
            results.append(HotelResult(
                hotel_key=f"trivago:{h.get('accommodation_id')}", name=h.get("accommodation_name", "Hotel"),
                address=" · ".join(x for x in (h.get("country_city"), h.get("distance")) if x),
                stars=h.get("hotel_rating") or None, rating=rating, reviews=reviews,
                latitude=h.get("latitude"), longitude=h.get("longitude"), image=h.get("main_image"),
                amenities=[a.strip() for a in (h.get("top_amenities") or "").split(",") if a.strip()],
                provider="trivago", offers=[offer]))
        return results

    async def recheck(self, offer: SupplierOffer, query: StaySearch) -> Recheck:
        return Recheck(available=False, detail="Compare-only: book on the site through the trivago link")

    async def book(self, offer: SupplierOffer, recheck: Recheck, query: StaySearch, guest: Guest,
                   idempotency_key: str) -> BookingOutcome:
        return BookingOutcome(status="FAILED", detail="Compare-only: book on the site through the trivago link")
