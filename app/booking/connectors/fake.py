"""Simulated reservation system with scriptable failures, used until a real sandbox exists.

Pricing and rules live in FakeReservationConnector; state lives in a store:
- MemoryStore: one process (unit tests).
- DbStore: the app database, so several API processes/instances share inventory and bookings.
  Booking checks capacity and inserts under a lock (Postgres advisory lock, or a per-process lock
  on SQLite), so two workers can't both sell the last room.
"""

import asyncio
import secrets
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from app.booking.contracts import (
    AvailabilityQuery,
    BookingResult,
    BookingState,
    CancellationPolicy,
    Capability,
    Offer,
    Quote,
    Reservation,
    SupplierError,
    SupplierTimeout,
)

DEFAULT_ROOMS = [
    {"code": "STD", "name": "Standard Double Room", "max": 2, "rate": "95.00", "count": 5,
     "meal": "Breakfast included"},
    {"code": "SEA", "name": "Sea View Double Room", "max": 3, "rate": "140.00", "count": 2,
     "meal": "Breakfast included"},
    {"code": "FAM", "name": "Family Suite", "max": 4, "rate": "210.00", "count": 1,
     "meal": "Breakfast included"},
]
CITY_TAX_PER_ADULT_NIGHT = Decimal("2.50")
VAT_RATE = Decimal("0.10")
CENT = Decimal("0.01")
FAULTS = ("price_change", "timeout_after_success", "timeout_before_success", "fail_book", "sold_out")


def _nights(check_in: date, check_out: date) -> list[date]:
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]


# --- stores -------------------------------------------------------------------------------

@dataclass
class MemoryStore:
    sold: dict[tuple[str, date], int] = field(default_factory=dict)
    reservations: dict[str, dict] = field(default_factory=dict)
    by_key: dict[str, str] = field(default_factory=dict)
    # Faults fire once each so tests can script a sequence of events.
    faults: set[str] = field(default_factory=set)
    rate_bump: Decimal = Decimal(0)  # persistent price change applied by the price_change fault

    async def sold_by_night(self, room: str, check_in: date, check_out: date) -> dict[date, int]:
        return {d: self.sold.get((room, d), 0) for d in _nights(check_in, check_out)}

    async def take_fault(self, name: str) -> bool:
        if name in self.faults:
            self.faults.discard(name)
            return True
        return False

    async def add_fault(self, name: str) -> None:
        self.faults.add(name)

    async def get_bump(self) -> Decimal:
        return self.rate_bump

    async def add_bump(self, amount: Decimal) -> None:
        self.rate_bump += amount

    async def ref_by_key(self, key: str) -> str | None:
        return self.by_key.get(key)

    async def book(self, room: dict, quote: Quote, key: str) -> str:
        q = quote.offer.query
        nights = _nights(q.check_in, q.check_out)
        if min(room["count"] - self.sold.get((room["code"], d), 0) for d in nights) <= 0:
            raise SupplierError("Room is no longer available")
        for d in nights:
            self.sold[(room["code"], d)] = self.sold.get((room["code"], d), 0) + 1
        ref = "FK-" + secrets.token_hex(4).upper()
        self.reservations[ref] = {"quote": quote.model_dump(mode="json"), "status": "CONFIRMED"}
        self.by_key[key] = ref
        return ref

    async def get(self, ref: str) -> dict | None:
        return self.reservations.get(ref)


class DbStore:
    # Keyed by event loop too: an asyncio.Lock must not be shared between loops.
    _locks: dict[tuple[int, int], asyncio.Lock] = defaultdict(asyncio.Lock)

    def __init__(self, sessionmaker, property_id: int):
        self.sm, self.property_id = sessionmaker, property_id

    async def sold_by_night(self, room: str, check_in: date, check_out: date) -> dict[date, int]:
        from app.models import SimReservation

        async with self.sm() as s:
            rows = (await s.execute(select(SimReservation.check_in, SimReservation.check_out).where(
                SimReservation.property_id == self.property_id, SimReservation.room_code == room,
                SimReservation.check_in < check_out, SimReservation.check_out > check_in))).all()
        counts = {d: 0 for d in _nights(check_in, check_out)}
        for ci, co in rows:
            for d in _nights(ci, co):
                if d in counts:
                    counts[d] += 1
        return counts

    async def _flag(self, s, name):
        from app.models import SimFlag

        return await s.get(SimFlag, (self.property_id, name))

    async def take_fault(self, name: str) -> bool:
        from app.models import SimFlag

        async with self.sm() as s:
            result = await s.execute(delete(SimFlag).where(SimFlag.property_id == self.property_id,
                                                           SimFlag.name == f"fault:{name}"))
            await s.commit()
            return result.rowcount > 0  # one delete wins, even across processes

    async def add_fault(self, name: str) -> None:
        from app.models import SimFlag

        async with self.sm() as s:
            if not await self._flag(s, f"fault:{name}"):
                s.add(SimFlag(property_id=self.property_id, name=f"fault:{name}", value="1"))
                await s.commit()

    async def get_bump(self) -> Decimal:
        async with self.sm() as s:
            flag = await self._flag(s, "rate_bump")
            return Decimal(flag.value) if flag else Decimal(0)

    async def add_bump(self, amount: Decimal) -> None:
        from app.models import SimFlag

        async with self.sm() as s:
            flag = await self._flag(s, "rate_bump")
            if flag:
                flag.value = str(Decimal(flag.value) + amount)
            else:
                s.add(SimFlag(property_id=self.property_id, name="rate_bump", value=str(amount)))
            await s.commit()

    async def ref_by_key(self, key: str) -> str | None:
        from app.models import SimReservation

        async with self.sm() as s:
            return await s.scalar(select(SimReservation.reference).where(SimReservation.idempotency_key == key))

    async def book(self, room: dict, quote: Quote, key: str) -> str:
        from app.models import SimReservation

        q = quote.offer.query
        async with self._locks[(id(asyncio.get_running_loop()), self.property_id)]:  # same process
            async with self.sm() as s:
                if s.bind.dialect.name == "postgresql":  # every process and instance
                    lock_id = zlib.crc32(f"sim:{self.property_id}:{room['code']}".encode())
                    await s.execute(select(func.pg_advisory_xact_lock(lock_id)))
                rows = (await s.execute(select(SimReservation.check_in, SimReservation.check_out).where(
                    SimReservation.property_id == self.property_id, SimReservation.room_code == room["code"],
                    SimReservation.check_in < q.check_out, SimReservation.check_out > q.check_in))).all()
                for d in _nights(q.check_in, q.check_out):
                    if sum(1 for ci, co in rows if ci <= d < co) >= room["count"]:
                        raise SupplierError("Room is no longer available")
                ref = "FK-" + secrets.token_hex(4).upper()
                s.add(SimReservation(reference=ref, property_id=self.property_id, idempotency_key=key,
                                     room_code=room["code"], check_in=q.check_in, check_out=q.check_out,
                                     quote=quote.model_dump(mode="json"), status="CONFIRMED"))
                await s.commit()
                return ref

    async def get(self, ref: str) -> dict | None:
        from app.models import SimReservation

        async with self.sm() as s:
            row = await s.get(SimReservation, ref)
            if row is None or row.property_id != self.property_id:
                return None
            return {"quote": row.quote, "status": row.status}


_MEMORY: dict[int, MemoryStore] = {}
_DB_SESSIONMAKER = None


def use_database(sessionmaker) -> None:
    """Called at app startup: keep simulator state in the database (shared by all workers)."""
    global _DB_SESSIONMAKER
    _DB_SESSIONMAKER = sessionmaker


def reset_fake_store() -> None:
    global _DB_SESSIONMAKER
    _MEMORY.clear()
    _DB_SESSIONMAKER = None


def store_for(property_id: int):
    if _DB_SESSIONMAKER is not None:
        return DbStore(_DB_SESSIONMAKER, property_id)
    return _MEMORY.setdefault(property_id, MemoryStore())


def inject_fault(property_id: int, fault: str) -> None:
    """Test helper for the in-memory store. The app uses `await store.add_fault(...)`."""
    _MEMORY.setdefault(property_id, MemoryStore()).faults.add(fault)


# --- connector ----------------------------------------------------------------------------

class FakeReservationConnector:
    capabilities = frozenset(
        {Capability.SEARCH, Capability.QUOTE, Capability.BOOK, Capability.LOOKUP}
    )

    def __init__(self, property_id: int, tz: str, currency: str, options: dict | None = None):
        options = options or {}
        self.tz = ZoneInfo(tz)
        self.currency = currency
        self.rooms = options.get("rooms", DEFAULT_ROOMS)
        self.store = store_for(property_id)
        self.city_tax = Decimal(str(options.get("city_tax_per_adult_night", CITY_TAX_PER_ADULT_NIGHT)))
        # Optional children policy, e.g. free under 6, a nightly fee until 12, adults from 12.
        self.child_free_under = options.get("child_free_under")
        self.child_adult_from = options.get("child_adult_from", 18)
        self.child_fee = Decimal(str(options.get("child_fee_per_night", "0")))

    def _adults(self, q: AvailabilityQuery) -> int:
        return q.adults + sum(1 for a in q.children_ages if a >= self.child_adult_from)

    def _child_fees(self, q: AvailabilityQuery) -> Decimal:
        if self.child_free_under is None:
            return Decimal(0)
        paying = sum(1 for a in q.children_ages if self.child_free_under <= a < self.child_adult_from)
        return (self.child_fee * paying * q.nights).quantize(CENT)

    def _fits(self, room: dict, q: AvailabilityQuery) -> bool:
        guests = q.adults + len(q.children_ages)
        return room["max"] >= guests and room.get("max_adults", room["max"]) >= self._adults(q)

    async def _free(self, room: dict, q: AvailabilityQuery) -> int:
        sold = await self.store.sold_by_night(room["code"], q.check_in, q.check_out)
        return min(room["count"] - n for n in sold.values())

    def _offer(self, room: dict, plan: str, q: AvailabilityQuery, bump: Decimal, free: int) -> Offer:
        nightly = Decimal(room["rate"]) + bump
        if plan == "NR":
            nightly = (nightly * Decimal("0.90")).quantize(CENT)
        total = (nightly * q.nights).quantize(CENT) + self._child_fees(q)
        taxes = (total - total / (1 + VAT_RATE)).quantize(CENT)
        city_tax = (self.city_tax * q.adults * q.nights).quantize(CENT)
        if plan == "FLEX":
            deadline = datetime.combine(q.check_in - timedelta(days=2), time(15, 0), self.tz)
            cancel = CancellationPolicy(
                refundable=True,
                free_until=deadline,
                description="Free cancellation until 15:00 two days before arrival; "
                "after that the first night is charged.",
            )
            payment = "Pay at the property"
        else:
            cancel = CancellationPolicy(
                refundable=False, description="Non-refundable: the full amount is charged if cancelled."
            )
            payment = "Full prepayment at booking"
        ages = "-".join(str(a) for a in q.children_ages) or "none"
        return Offer(
            offer_id=f"{room['code']}:{plan}:{q.check_in}:{q.check_out}:{q.adults}:{ages}",
            room_type=room["code"],
            room_name=room["name"],
            room_size=room.get("size", ""),
            rate_plan="Flexible" if plan == "FLEX" else "Non-refundable",
            query=q,
            max_occupancy=room["max"],
            meal_plan=room["meal"],
            currency=self.currency,
            total=total,
            taxes_included=taxes,
            pay_at_property_fees=city_tax,
            payment_timing=payment,
            cancellation=cancel,
            rooms_left=free,
        )

    async def search(self, query: AvailabilityQuery) -> list[Offer]:
        if await self.store.take_fault("sold_out"):
            return []
        bump = await self.store.get_bump()
        offers = []
        for room in self.rooms:
            if not self._fits(room, query):
                continue
            free = await self._free(room, query)
            if free > 0:
                offers += [self._offer(room, "FLEX", query, bump, free), self._offer(room, "NR", query, bump, free)]
        return offers

    @staticmethod
    def _parse(offer_id: str) -> tuple[str, str, AvailabilityQuery] | None:
        try:
            code, plan, ci, co, adults, ages = offer_id.split(":")
            q = AvailabilityQuery(
                check_in=date.fromisoformat(ci),
                check_out=date.fromisoformat(co),
                adults=int(adults),
                children_ages=[] if ages == "none" else [int(a) for a in ages.split("-")],
            )
            return code, plan, q
        except (ValueError, TypeError):
            return None

    async def price(self, offer_id: str) -> Offer | None:
        parsed = self._parse(offer_id)
        if not parsed:
            return None
        code, plan, q = parsed
        room = next((r for r in self.rooms if r["code"] == code), None)
        if room is None or plan not in ("FLEX", "NR") or not self._fits(room, q):
            return None
        free = await self._free(room, q)
        if free <= 0:
            return None
        if await self.store.take_fault("price_change"):
            await self.store.add_bump(Decimal("10.00"))
        return self._offer(room, plan, q, await self.store.get_bump(), free)

    async def book(self, quote: Quote, idempotency_key: str) -> BookingResult:
        existing = await self.store.ref_by_key(idempotency_key)
        if existing:  # idempotent replay
            return BookingResult(status=BookingState.CONFIRMED, reference=existing)
        if await self.store.take_fault("timeout_before_success"):
            raise SupplierTimeout()
        if await self.store.take_fault("fail_book"):
            raise SupplierError("Supplier rejected the reservation")
        room = next(r for r in self.rooms if r["code"] == quote.offer.room_type)
        ref = await self.store.book(room, quote, idempotency_key)
        if await self.store.take_fault("timeout_after_success"):
            raise SupplierTimeout()
        return BookingResult(status=BookingState.CONFIRMED, reference=ref)

    async def _reservation(self, ref: str) -> Reservation | None:
        row = await self.store.get(ref)
        if row is None:
            return None
        quote = Quote.model_validate(row["quote"])
        o = quote.offer
        return Reservation(
            reference=ref,
            room_name=o.room_name,
            check_in=o.query.check_in,
            check_out=o.query.check_out,
            guest_last_name=quote.guest.last_name,
            status=row["status"],
            total=o.total,
            currency=o.currency,
        )

    async def find_by_idempotency_key(self, idempotency_key: str) -> Reservation | None:
        ref = await self.store.ref_by_key(idempotency_key)
        return await self._reservation(ref) if ref else None

    async def lookup(self, reference: str, last_name: str) -> Reservation | None:
        found = await self._reservation(reference.strip().upper())
        if found is None or found.guest_last_name.casefold() != last_name.strip().casefold():
            return None  # same answer as "not found": no reference enumeration
        return found
