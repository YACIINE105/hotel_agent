"""Travel shopper: fan-out search, merge, rank by lowest total, re-check, book the best of a set.

Safety rules shared with the hotel agent:
- Prices come only from suppliers, re-checked right before booking.
- Only an explicit guest confirmation books (no model tool can).
- One booking per quote; the supplier call carries an idempotency key.
- Compare-only (redirect) offers are never "booked" here: the guest continues on that site.
"""

import asyncio
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError, Conflict, NotFound
from app.models import Handoff, Property, ShopBooking, ShopQuote, ShopSearch
from app.shopper.contracts import Guest, HotelResult, Recheck, ShopTerms, StaySearch, SupplierOffer

QUOTE_TTL = timedelta(minutes=10)
GENERIC = re.compile(r"\b(the|hotel|hotels|resort|resorts|and|spa|suites?|inn|by|&)\b")


def merge_key(name: str) -> str:
    """Same hotel from different sites: compare names without filler words and punctuation."""
    return re.sub(r"[\W_]+", "", GENERIC.sub(" ", name.casefold()))


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ShopService:
    def __init__(self, session: AsyncSession, prop: Property, suppliers: list, timeout: float = 12):
        self.session, self.prop, self.timeout = session, prop, timeout
        self.suppliers = {s.name: s for s in suppliers if s.configured}

    # --- search -------------------------------------------------------------------------------

    async def _one(self, supplier, query: StaySearch) -> tuple[list[HotelResult], dict]:
        t0 = time.perf_counter()
        status = {"supplier": supplier.name, "label": supplier.label, "bookable": supplier.bookable,
                  "simulated": supplier.simulated}
        try:
            results = await asyncio.wait_for(supplier.search(query), self.timeout)
            status |= {"status": "ok", "hotels": len(results)}
        except asyncio.TimeoutError:
            results, status["status"] = [], "timeout"
        except Exception as exc:  # noqa: BLE001 - one failing site must not break the search
            results, status["status"], status["error"] = [], "error", type(exc).__name__
        status["ms"] = int((time.perf_counter() - t0) * 1000)
        return results, status

    async def search(self, conversation_id: str, query: StaySearch) -> ShopSearch:
        if not self.suppliers:
            raise AppError("No hotel suppliers are configured", status_code=503)
        outcomes = await asyncio.gather(*(self._one(s, query) for s in self.suppliers.values()))
        merged: dict[str, dict] = {}
        for results, _ in outcomes:
            for hotel in results:
                key = merge_key(hotel.name)
                entry = merged.setdefault(key, {**hotel.model_dump(mode="json", exclude={"offers"}), "offers": []})
                for field in ("stars", "rating", "reviews", "image", "address", "latitude", "longitude"):
                    if entry.get(field) in (None, "") and getattr(hotel, field) not in (None, ""):
                        entry[field] = getattr(hotel, field)
                entry["offers"] += [o.model_dump(mode="json") for o in hotel.offers]

        search = ShopSearch(property_id=self.prop.id, conversation_id=conversation_id,
                            query=query.model_dump(mode="json"), results=[], suppliers=[s for _, s in outcomes])
        self.session.add(search)
        await self.session.flush()

        hotels, n = [], 0
        for entry in merged.values():
            offers = [o for o in entry["offers"]
                      if not query.max_price_per_night or Decimal(o["total"]) / query.nights <= query.max_price_per_night]
            if query.min_stars and (entry.get("stars") or 0) < query.min_stars:
                continue
            if not offers:
                continue
            offers.sort(key=lambda o: Decimal(o["total"]))
            for o in offers:
                n += 1
                o["offer_id"] = f"{search.id[:6]}-{n}"
            totals = [Decimal(o["total"]) for o in offers]
            entry.update(offers=offers, best_total=str(totals[0]), best_source=offers[0]["source"],
                         sites=len({o["source"] for o in offers}),
                         saving=str(max(totals) - totals[0]), per_night=str((totals[0] / query.nights).quantize(Decimal("0.01"))),
                         bookable=any(o["kind"] == "bookable" for o in offers))
            hotels.append(entry)
        hotels.sort(key=lambda h: (Decimal(h["best_total"]), -(h.get("rating") or 0)))
        for i, h in enumerate(hotels, 1):
            h["hotel_id"] = f"H{i}"
        search.results = hotels
        await self.session.commit()
        return search

    # --- lookups ------------------------------------------------------------------------------

    async def _searches(self, conversation_id: str) -> list[ShopSearch]:
        return list((await self.session.scalars(
            select(ShopSearch).where(ShopSearch.conversation_id == conversation_id,
                                     ShopSearch.property_id == self.prop.id)
            .order_by(ShopSearch.created_at.desc()).limit(10))).all())

    async def latest_search(self, conversation_id: str) -> ShopSearch | None:
        found = await self._searches(conversation_id)
        return found[0] if found else None

    async def find_offer(self, conversation_id: str, offer_id: str) -> tuple[ShopSearch, dict, dict]:
        for search in await self._searches(conversation_id):
            for hotel in search.results:
                for offer in hotel["offers"]:
                    if offer["offer_id"] == offer_id:
                        return search, hotel, offer
        raise NotFound(f"Offer {offer_id} not found in this conversation; search again")

    # --- quote: re-check a set of offers and keep the cheapest still available -----------------

    async def quote_best_of(self, conversation_id: str, offer_ids: list[str], guest: Guest) -> ShopQuote:
        if not offer_ids:
            raise AppError("Choose at least one offer", status_code=422)
        candidates = [await self.find_offer(conversation_id, oid) for oid in dict.fromkeys(offer_ids)]
        compared, checks = [], []
        for search, hotel, offer in candidates:
            if offer["kind"] != "bookable":
                compared.append({"offer_id": offer["offer_id"], "hotel": hotel["name"], "source": offer["source"],
                                 "status": "compare_only", "link": offer.get("link"), "total": offer["total"]})
                continue
            supplier = self.suppliers.get(offer["supplier"])
            if supplier is None:
                compared.append({"offer_id": offer["offer_id"], "hotel": hotel["name"], "source": offer["source"],
                                 "status": "supplier_unavailable"})
                continue
            query = StaySearch.model_validate(search.query)
            checks.append((search, hotel, offer, query, supplier))

        async def recheck(item):
            search, hotel, offer, query, supplier = item
            try:
                return await asyncio.wait_for(supplier.recheck(SupplierOffer.model_validate(
                    {k: v for k, v in offer.items() if k != "offer_id"}), query), self.timeout)
            except Exception as exc:  # noqa: BLE001
                return Recheck(available=False, detail=f"Re-check failed ({type(exc).__name__})")

        rechecks = await asyncio.gather(*(recheck(c) for c in checks))
        best = None
        for (search, hotel, offer, query, supplier), rc in zip(checks, rechecks, strict=True):
            row = {"offer_id": offer["offer_id"], "hotel": hotel["name"], "source": offer["source"],
                   "searched_total": offer["total"], "status": "available" if rc.available else "unavailable",
                   "total": str(rc.total) if rc.total is not None else None, "detail": rc.detail}
            compared.append(row)
            if rc.available and (best is None or rc.total < best[4].total):
                best = (search, hotel, offer, query, rc)
        if best is None:
            raise Conflict("None of the chosen offers can be booked right now: " +
                           "; ".join(f"{c['hotel']} ({c['source']}): {c['status']}" for c in compared))
        search, hotel, offer, query, rc = best
        terms = ShopTerms(hotel_name=hotel["name"], source=offer["source"], room_name=offer["room_name"],
                          board=rc.board or offer.get("board") or "", refundable=rc.refundable,
                          check_in=query.check_in, check_out=query.check_out, adults=query.adults,
                          children_ages=query.children_ages, total=rc.total, currency=rc.currency or offer["currency"],
                          guest=guest, rechecked_at=datetime.now(timezone.utc))
        quote = ShopQuote(property_id=self.prop.id, conversation_id=conversation_id, search_id=search.id,
                          offer=offer, recheck=rc.model_dump(mode="json"), terms=terms.model_dump(mode="json"),
                          terms_hash=terms.terms_hash(), compared=compared,
                          expires_at=datetime.now(timezone.utc) + QUOTE_TTL)
        self.session.add(quote)
        await self.session.commit()
        return quote

    # --- confirm ------------------------------------------------------------------------------

    async def confirm(self, conversation_id: str, quote_id: str) -> dict:
        quote = await self.session.scalar(select(ShopQuote).where(
            ShopQuote.id == quote_id, ShopQuote.conversation_id == conversation_id,
            ShopQuote.property_id == self.prop.id))
        if quote is None:
            raise NotFound("Quote not found")
        existing = await self.session.scalar(select(ShopBooking).where(ShopBooking.quote_id == quote_id))
        if existing:
            return booking_view(existing)  # double click / retry: same outcome, no second booking
        if _aware(quote.expires_at) < datetime.now(timezone.utc):
            raise Conflict("This price has expired; please get a fresh quote")
        supplier = self.suppliers.get(quote.offer["supplier"])
        if supplier is None:
            raise AppError("That supplier is not available right now", status_code=503)
        booking = ShopBooking(property_id=self.prop.id, conversation_id=conversation_id, quote_id=quote_id,
                              state="SUBMITTING", idempotency_key=secrets.token_hex(16), supplier=supplier.name)
        self.session.add(booking)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return booking_view(await self.session.scalar(select(ShopBooking).where(ShopBooking.quote_id == quote_id)))

        search = await self.session.get(ShopSearch, quote.search_id)
        query = StaySearch.model_validate(search.query)
        offer = SupplierOffer.model_validate({k: v for k, v in quote.offer.items() if k != "offer_id"})
        terms = ShopTerms.model_validate(quote.terms)
        try:
            outcome = await asyncio.wait_for(
                supplier.book(offer, Recheck.model_validate(quote.recheck), query, terms.guest, booking.idempotency_key),
                60)
        except Exception:  # noqa: BLE001 - outcome unknown: never tell the guest it failed or succeeded
            outcome = None
        if outcome is None or outcome.status == "UNKNOWN":
            booking.state = "UNKNOWN"
            booking.detail = "We could not confirm the booking status. Our team will verify it and contact you."
            self.session.add(Handoff(conversation_id=conversation_id, property_id=self.prop.id,
                                     reason=f"Shopper booking outcome unknown ({supplier.label}, quote {quote_id})"))
        else:
            booking.state = outcome.status
            booking.supplier_reference = outcome.supplier_reference
            booking.hotel_confirmation = outcome.hotel_confirmation
            booking.detail = outcome.detail
        await self.session.commit()
        return booking_view(booking)


def booking_view(b: ShopBooking) -> dict:
    return {"booking_id": b.id, "state": b.state, "reference": b.supplier_reference,
            "hotel_confirmation": b.hotel_confirmation, "supplier": b.supplier, "detail": b.detail,
            "quote_id": b.quote_id}


def quote_view(q: ShopQuote) -> dict:
    t = q.terms
    return {"quote_id": q.id, "hotel_name": t["hotel_name"], "source": t["source"], "room_name": t["room_name"],
            "board": t["board"], "refundable": t["refundable"], "check_in": t["check_in"], "check_out": t["check_out"],
            "adults": t["adults"], "children_ages": t["children_ages"], "total": t["total"], "currency": t["currency"],
            "guest": {k: t["guest"][k] for k in ("first_name", "last_name", "email")},
            "searched_total": q.offer["total"], "simulated": q.offer.get("simulated", False),
            "compared": q.compared, "expires_at": q.expires_at.isoformat()}


def search_view(s: ShopSearch, limit: int = 12) -> dict:
    return {"search_id": s.id, "query": s.query, "suppliers": s.suppliers,
            "hotels": s.results[:limit], "total_hotels": len(s.results)}
