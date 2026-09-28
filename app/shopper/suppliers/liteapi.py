"""LiteAPI (Nuitée): real bookable inventory over 2M+ hotels, free sandbox.

Docs: https://docs.liteapi.travel (search: POST /hotels/rates; prebook/book: book.liteapi.travel).
Enable with LITEAPI_KEY (sandbox key from the LiteAPI dashboard). In the sandbox, bookings use the
ACC_CREDIT_CARD method and nothing is charged. Production bookings need a payment integration
(LiteAPI payment SDK) before going live; until then this adapter refuses non-sandbox booking.
"""

from decimal import Decimal

import httpx

from app.shopper.contracts import BookingOutcome, Guest, HotelResult, Recheck, StaySearch, SupplierOffer

SEARCH_URL = "https://api.liteapi.travel/v3.0"
BOOK_URL = "https://book.liteapi.travel/v3.0"


class LiteApiSupplier:
    name = "liteapi"
    label = "LiteAPI"
    bookable = True
    simulated = False

    def __init__(self, api_key: str, client: httpx.AsyncClient, nationality: str = "US", sandbox: bool = True,
                 timeout: float = 12):
        self.api_key, self.client, self.nationality = api_key, client, nationality
        self.sandbox, self.timeout = sandbox, timeout

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"X-API-Key": self.api_key, "accept": "application/json", "content-type": "application/json"}

    def _occupancy(self, q: StaySearch) -> list[dict]:
        occ = {"adults": q.adults}
        if q.children_ages:
            occ["children"] = q.children_ages
        return [occ]

    async def search(self, query: StaySearch) -> list[HotelResult]:
        body = {
            "occupancies": self._occupancy(query), "currency": query.currency,
            "guestNationality": self.nationality, "checkin": query.check_in.isoformat(),
            "checkout": query.check_out.isoformat(), "cityName": query.city, "countryCode": query.country_code,
            "timeout": 8, "maxRatesPerHotel": 3, "limit": 40, "includeHotelData": True,
        }
        if query.refundable_only:
            body["refundableRatesOnly"] = True
        if query.min_stars:
            body["starRating"] = list(range(query.min_stars, 6))
        r = await self.client.post(f"{SEARCH_URL}/hotels/rates", json=body, headers=self._headers(),
                                   timeout=self.timeout)
        r.raise_for_status()
        payload = r.json()
        meta = {h.get("id"): h for h in payload.get("hotels", [])}
        results = []
        for item in payload.get("data", []):
            hid = item.get("hotelId")
            info = meta.get(hid, {})
            offers = []
            for room_type in item.get("roomTypes", []):
                rates = room_type.get("rates") or []
                if not rates:
                    continue
                rate = rates[0]
                totals = (rate.get("retailRate") or {}).get("total") or []
                if not totals:
                    continue
                cancel = rate.get("cancellationPolicies") or {}
                deadlines = [p.get("cancelTime") for p in cancel.get("cancelPolicyInfos", []) if p.get("cancelTime")]
                refundable = cancel.get("refundableTag") == "RFN"
                offers.append(SupplierOffer(
                    supplier=self.name, source=self.label, kind="bookable",
                    supplier_offer_id=room_type.get("offerId", ""), room_name=rate.get("name", "Room"),
                    board=rate.get("boardName", ""), refundable=refundable,
                    free_cancellation_until=min(deadlines) if refundable and deadlines else None,
                    total=Decimal(str(totals[0]["amount"])), currency=totals[0].get("currency", query.currency)))
            if offers:
                results.append(HotelResult(
                    hotel_key=f"liteapi:{hid}", name=info.get("name") or hid, address=info.get("address") or "",
                    stars=info.get("stars"), rating=info.get("rating"), image=info.get("main_photo") or info.get("thumbnail"),
                    offers=offers))
        return results

    async def recheck(self, offer: SupplierOffer, query: StaySearch) -> Recheck:
        r = await self.client.post(f"{BOOK_URL}/rates/prebook", headers=self._headers(), timeout=self.timeout,
                                   json={"offerId": offer.supplier_offer_id, "usePaymentSdk": False})
        if r.status_code >= 400:
            return Recheck(available=False, detail=f"Prebook refused (HTTP {r.status_code})")
        data = r.json().get("data") or {}
        if not data.get("prebookId"):
            return Recheck(available=False, detail="No prebook id returned")
        return Recheck(available=True, total=Decimal(str(data["price"])), currency=data.get("currency"),
                       refundable=offer.refundable if not data.get("cancellationChanged") else None,
                       board=offer.board, supplier_ref=data["prebookId"],
                       detail="Terms changed at prebook" if data.get("cancellationChanged") or data.get("boardChanged") else "")

    async def book(self, offer: SupplierOffer, recheck: Recheck, query: StaySearch, guest: Guest,
                   idempotency_key: str) -> BookingOutcome:
        if not self.sandbox:
            return BookingOutcome(status="FAILED", detail="Live LiteAPI booking needs a payment integration")
        person = {"firstName": guest.first_name, "lastName": guest.last_name, "email": guest.email}
        body = {"prebookId": recheck.supplier_ref, "holder": {**person, "phone": guest.phone or ""},
                "guests": [{"occupancyNumber": 1, **person}], "payment": {"method": "ACC_CREDIT_CARD"},
                "clientReference": idempotency_key}
        try:
            r = await self.client.post(f"{BOOK_URL}/rates/book", json=body, headers=self._headers(), timeout=40)
        except (httpx.TimeoutException, httpx.TransportError):
            return BookingOutcome(status="UNKNOWN", detail="No answer from LiteAPI; checking the booking status")
        if r.status_code >= 500:
            return BookingOutcome(status="UNKNOWN", detail=f"LiteAPI error {r.status_code}")
        data = r.json().get("data") or {}
        if r.status_code >= 400 or data.get("status") != "CONFIRMED":
            error = r.json().get("error") or {}
            return BookingOutcome(status="FAILED", detail=str(error.get("description") or error or data.get("status")))
        return BookingOutcome(status="CONFIRMED", supplier_reference=data.get("bookingId"),
                              hotel_confirmation=data.get("hotelConfirmationCode"))
