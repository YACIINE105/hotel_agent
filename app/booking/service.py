"""Quote -> confirm -> submit -> reconcile. External calls never run inside a held lock."""

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.booking.connectors.base import ReservationConnector
from app.booking.contracts import (
    AvailabilityQuery,
    BookingState,
    Capability,
    GuestDetails,
    Offer,
    Quote,
    Reservation,
    SupplierError,
    SupplierTimeout,
    check_transition,
)
from app.errors import AppError, Conflict, NotFound
from app.models import BookingIntent, Effect, Handoff, Property, QuoteRecord

QUOTE_TTL = timedelta(minutes=15)


def _aware(dt: datetime) -> datetime:
    # SQLite drops tzinfo; values are always written in UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class BookingService:
    def __init__(self, session: AsyncSession, prop: Property, connector: ReservationConnector):
        self.session, self.prop, self.connector = session, prop, connector

    def _require(self, cap: Capability):
        if cap not in self.connector.capabilities:
            raise AppError(f"This hotel's reservation system does not support {cap.lower()}", status_code=422)

    async def search(self, query: AvailabilityQuery) -> list[Offer]:
        self._require(Capability.SEARCH)
        return await self.connector.search(query)

    async def _save_quote(self, conversation_id: str, quote: Quote) -> QuoteRecord:
        row = QuoteRecord(
            property_id=self.prop.id,
            conversation_id=conversation_id,
            data=quote.model_dump(mode="json"),
            terms_hash=quote.terms_hash(),
            expires_at=quote.expires_at,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_quote(self, conversation_id: str, offer_id: str, guest: GuestDetails) -> QuoteRecord:
        self._require(Capability.QUOTE)
        offer = await self.connector.price(offer_id)
        if offer is None:
            raise Conflict("That offer is no longer available; please search again")
        quote = Quote(offer=offer, guest=guest, expires_at=datetime.now(timezone.utc) + QUOTE_TTL)
        row = await self._save_quote(conversation_id, quote)
        await self.session.commit()
        return row

    async def _quote(self, conversation_id: str, quote_id: str) -> QuoteRecord:
        row = await self.session.scalar(
            select(QuoteRecord).where(
                QuoteRecord.id == quote_id,
                QuoteRecord.property_id == self.prop.id,
                QuoteRecord.conversation_id == conversation_id,
            )
        )
        if row is None:
            raise NotFound("Quote not found")
        return row

    async def _set_state(self, intent: BookingIntent, state: BookingState, **fields) -> None:
        check_transition(BookingState(intent.state), state)
        intent.state = state
        for k, v in fields.items():
            setattr(intent, k, v)
        await self.session.commit()

    async def _effect(self, intent: BookingIntent, kind: str, status: str, attempt=1, **response) -> None:
        self.session.add(
            Effect(intent_id=intent.id, property_id=self.prop.id, kind=kind,
                   status=status, attempt=attempt, response=response)
        )
        await self.session.commit()

    def result(self, intent: BookingIntent, new_quote: QuoteRecord | None = None) -> dict:
        out = {"intent_id": intent.id, "state": intent.state, "reference": intent.external_ref,
               "detail": intent.detail, "quote_id": intent.quote_id}
        if new_quote is not None:
            out["new_quote"] = quote_view(new_quote)
        return out

    async def confirm(self, conversation_id: str, quote_id: str) -> dict:
        """Called only from an explicit guest confirmation, never by the model."""
        self._require(Capability.BOOK)
        record = await self._quote(conversation_id, quote_id)
        existing = await self.session.scalar(select(BookingIntent).where(BookingIntent.quote_id == quote_id))
        if existing:
            return self.result(existing)  # double click / retry: same outcome, no second booking
        if record.superseded_by:
            raise Conflict("These terms were replaced; please confirm the updated quote")
        if _aware(record.expires_at) < datetime.now(timezone.utc):
            raise Conflict("This quote has expired; please request a new one")

        intent = BookingIntent(
            property_id=self.prop.id,
            conversation_id=conversation_id,
            quote_id=quote_id,
            state=BookingState.SUBMITTING,
            idempotency_key=secrets.token_hex(16),
        )
        self.session.add(intent)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(select(BookingIntent).where(BookingIntent.quote_id == quote_id))
            return self.result(existing)

        quote = Quote.model_validate(record.data)
        fresh = await self.connector.price(quote.offer.offer_id)
        if fresh is None:
            await self._set_state(intent, BookingState.FAILED, detail="The room is no longer available.")
            return self.result(intent)
        repriced = Quote(offer=fresh, guest=quote.guest, expires_at=datetime.now(timezone.utc) + QUOTE_TTL)
        if repriced.terms_hash() != record.terms_hash:
            new = await self._save_quote(conversation_id, repriced)
            record.superseded_by = new.id
            await self._set_state(intent, BookingState.PRICE_CHANGED,
                                  detail="The price or terms changed. Please review and confirm again.")
            return self.result(intent, new)

        await self._effect(intent, "BOOK", "STARTED")
        try:
            res = await self.connector.book(quote, intent.idempotency_key)
            await self._effect(intent, "BOOK", "SUCCEEDED", reference=res.reference)
            await self._set_state(intent, BookingState.CONFIRMED, external_ref=res.reference)
        except SupplierError as exc:
            await self._effect(intent, "BOOK", "FAILED", detail=str(exc))
            await self._set_state(intent, BookingState.FAILED, detail="The hotel system could not complete the booking.")
        except (SupplierTimeout, Exception):
            await self._effect(intent, "BOOK", "UNKNOWN")
            await self._set_state(intent, BookingState.UNKNOWN, detail="Checking the booking status with the hotel system.")
            await self.reconcile(intent, quote)
        return self.result(intent)

    async def reconcile(self, intent: BookingIntent, quote: Quote) -> None:
        """Look up before retrying; retry only with the same idempotency key."""
        try:
            found = await self.connector.find_by_idempotency_key(intent.idempotency_key)
            await self._effect(intent, "LOOKUP", "SUCCEEDED", reference=found.reference if found else None)
            if found:
                await self._set_state(intent, BookingState.CONFIRMED, external_ref=found.reference, detail="")
                return
            res = await self.connector.book(quote, intent.idempotency_key)
            await self._effect(intent, "BOOK", "SUCCEEDED", attempt=2, reference=res.reference)
            await self._set_state(intent, BookingState.CONFIRMED, external_ref=res.reference, detail="")
        except SupplierError as exc:
            await self._effect(intent, "BOOK", "FAILED", attempt=2, detail=str(exc))
            await self._set_state(intent, BookingState.FAILED, detail="The hotel system could not complete the booking.")
        except Exception:
            await self._effect(intent, "LOOKUP", "UNKNOWN")
            self.session.add(Handoff(conversation_id=intent.conversation_id, property_id=self.prop.id,
                                     reason=f"Booking outcome unknown for intent {intent.id}"))
            await self._set_state(intent, BookingState.STAFF_REVIEW,
                                  detail="We could not confirm the booking status. A staff member will verify it.")

    async def lookup(self, reference: str, last_name: str) -> Reservation | None:
        self._require(Capability.LOOKUP)
        return await self.connector.lookup(reference, last_name)


def quote_view(row: QuoteRecord) -> dict:
    """Structured card data for the UI. Money is sent as strings, never re-generated by the model."""
    q = row.data
    o = q["offer"]
    return {
        "quote_id": row.id,
        "room_name": o["room_name"],
        "rate_plan": o["rate_plan"],
        "check_in": o["query"]["check_in"],
        "check_out": o["query"]["check_out"],
        "adults": o["query"]["adults"],
        "children_ages": o["query"]["children_ages"],
        "meal_plan": o["meal_plan"],
        "currency": o["currency"],
        "total": o["total"],
        "taxes_included": o["taxes_included"],
        "pay_at_property_fees": o["pay_at_property_fees"],
        "payment_timing": o["payment_timing"],
        "cancellation": o["cancellation"],
        "guest": {"first_name": q["guest"]["first_name"], "last_name": q["guest"]["last_name"],
                  "email": q["guest"]["email"]},
        "expires_at": q["expires_at"],
    }


def offer_view(o: Offer) -> dict:
    return {
        "offer_id": o.offer_id,
        "room_name": o.room_name,
        "room_size": o.room_size,
        "rate_plan": o.rate_plan,
        "max_occupancy": o.max_occupancy,
        "meal_plan": o.meal_plan,
        "currency": o.currency,
        "total": str(o.total),
        "pay_at_property_fees": str(o.pay_at_property_fees),
        "payment_timing": o.payment_timing,
        "refundable": o.cancellation.refundable,
        "cancellation": o.cancellation.description,
        "rooms_left": o.rooms_left,
        "nights": o.query.nights,
    }
