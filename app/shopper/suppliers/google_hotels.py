"""Google Hotels prices via SerpApi: compare Booking.com, Expedia, Hotels.com, the hotel's own site...

Docs: https://serpapi.com/google-hotels-api and /google-hotels-property-details. Enable with
SERPAPI_KEY. These are *redirect* offers: we show the price and link; the guest books on that site.
We never scrape OTA websites directly (their terms forbid it); this is a licensed metasearch API.
"""

from decimal import Decimal, InvalidOperation

import httpx

from app.shopper.contracts import BookingOutcome, Guest, HotelResult, Recheck, StaySearch, SupplierOffer
from app.shopper.deeplinks import site_search_link

URL = "https://serpapi.com/search.json"


def _amount(value) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


class GoogleHotelsSupplier:
    name = "google_hotels"
    label = "Google Hotels"
    bookable = False
    simulated = False

    def __init__(self, api_key: str, client: httpx.AsyncClient, details_for_top: int = 0, timeout: float = 15):
        self.api_key, self.client, self.details_for_top, self.timeout = api_key, client, details_for_top, timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _params(self, query: StaySearch) -> dict:
        p = {"engine": "google_hotels", "check_in_date": query.check_in.isoformat(),
             "check_out_date": query.check_out.isoformat(), "adults": query.adults, "currency": query.currency,
             "gl": query.country_code.lower(), "hl": "en", "api_key": self.api_key}
        if query.children_ages:
            p["children"] = len(query.children_ages)
            p["children_ages"] = ",".join(str(max(1, a)) for a in query.children_ages)
        return p

    async def search(self, query: StaySearch) -> list[HotelResult]:
        """One API call: each property's list-level `prices` already has every site's nightly rate.
        Property-details calls (extra credits each) are optional via details_for_top."""
        params = {**self._params(query), "q": f"hotels in {query.city}", "sort_by": 3}
        if query.min_stars:
            params["hotel_class"] = ",".join(str(s) for s in range(query.min_stars, 6))
        r = await self.client.get(URL, params=params, timeout=self.timeout)
        r.raise_for_status()
        properties = r.json().get("properties", [])[:25]
        results = []
        for i, prop in enumerate(properties):
            name = prop.get("name", "Hotel")
            offers = []
            if i < self.details_for_top and prop.get("property_token"):
                offers = await self._site_offers(prop["property_token"], query)
            if not offers:
                for price in prop.get("prices", []):
                    nightly = _amount((price.get("rate_per_night") or {}).get("extracted_lowest"))
                    if not nightly:
                        continue
                    offer = self._offer(price.get("source", "?"), "Best available room", nightly * query.nights,
                                        query, site_search_link(price.get("source", ""), name, query) or prop.get("link"))
                    offer.per_night = nightly
                    offer.refundable = price.get("free_cancellation")
                    offers.append(offer)
            if offers:
                gps = prop.get("gps_coordinates") or {}
                results.append(HotelResult(
                    hotel_key=f"google:{prop.get('property_token') or name}", name=name,
                    stars=prop.get("extracted_hotel_class"), rating=(prop.get("overall_rating") or 0) * 2 or None,
                    reviews=prop.get("reviews"), latitude=gps.get("latitude"), longitude=gps.get("longitude"),
                    image=((prop.get("images") or [{}])[0]).get("thumbnail"), amenities=prop.get("amenities") or [],
                    offers=offers))
        return results

    async def _site_offers(self, token: str, query: StaySearch) -> list[SupplierOffer]:
        r = await self.client.get(URL, params={**self._params(query), "q": "hotel", "property_token": token},
                                  timeout=self.timeout)
        if r.status_code >= 400:
            return []
        data = r.json()
        offers = []
        for item in (data.get("featured_prices") or []) + (data.get("prices") or []):
            total = _amount((item.get("total_rate") or {}).get("extracted_lowest"))
            if total is None:
                nightly = _amount((item.get("rate_per_night") or {}).get("extracted_lowest"))
                total = nightly * query.nights if nightly else None
            if total is None:
                continue
            room = ((item.get("rooms") or [{}])[0]).get("name") or "Best available room"
            offer = self._offer(item.get("source", "?"), room, total, query, item.get("link"))
            offer.refundable = item.get("free_cancellation")
            offers.append(offer)
        # Keep the cheapest offer per site.
        best: dict[str, SupplierOffer] = {}
        for o in offers:
            if o.source not in best or o.total < best[o.source].total:
                best[o.source] = o
        return list(best.values())

    def _offer(self, source: str, room: str, total: Decimal, query: StaySearch, link: str | None) -> SupplierOffer:
        return SupplierOffer(supplier=self.name, source=source, kind="redirect", supplier_offer_id=link or source,
                             room_name=room, total=total.quantize(Decimal("0.01")), currency=query.currency, link=link,
                             via="Google Hotels", per_night=(total / query.nights).quantize(Decimal("0.01")))

    async def recheck(self, offer: SupplierOffer, query: StaySearch) -> Recheck:
        return Recheck(available=False, detail="Compare-only source: book on the provider's site")

    async def book(self, offer: SupplierOffer, recheck: Recheck, query: StaySearch, guest: Guest,
                   idempotency_key: str) -> BookingOutcome:
        return BookingOutcome(status="FAILED", detail="Compare-only source: continue on the provider's site")
