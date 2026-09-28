"""In-memory reservation system with scriptable failures, used until a real sandbox exists."""

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

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


@dataclass
class _Inventory:
    rooms: list[dict]
    sold: dict[tuple[str, date], int] = field(default_factory=dict)
    reservations: dict[str, dict] = field(default_factory=dict)
    by_key: dict[str, str] = field(default_factory=dict)
    # Faults fire once each so tests can script a sequence of events.
    faults: set[str] = field(default_factory=set)
    rate_bump: Decimal = Decimal(0)  # persistent price change applied by the price_change fault


_STORES: dict[int, _Inventory] = {}


def reset_fake_store() -> None:
    _STORES.clear()


def inject_fault(property_id: int, fault: str) -> None:
    """Faults: price_change, timeout_after_success, timeout_before_success, fail_book, sold_out."""
    _STORES[property_id].faults.add(fault)


class FakeReservationConnector:
    capabilities = frozenset(
        {Capability.SEARCH, Capability.QUOTE, Capability.BOOK, Capability.LOOKUP}
    )

    def __init__(self, property_id: int, tz: str, currency: str, options: dict | None = None):
        options = options or {}
        self.tz = ZoneInfo(tz)
        self.currency = currency
        self.store = _STORES.setdefault(
            property_id, _Inventory(rooms=options.get("rooms", DEFAULT_ROOMS))
        )

    def _fault(self, name: str) -> bool:
        if name in self.store.faults:
            self.store.faults.discard(name)
            return True
        return False

    def _nights(self, q: AvailabilityQuery):
        return [q.check_in + timedelta(days=i) for i in range(q.nights)]

    def _free(self, room: dict, q: AvailabilityQuery) -> int:
        return min(room["count"] - self.store.sold.get((room["code"], d), 0) for d in self._nights(q))

    def _offer(self, room: dict, plan: str, q: AvailabilityQuery) -> Offer:
        nightly = Decimal(room["rate"]) + self.store.rate_bump
        if plan == "NR":
            nightly = (nightly * Decimal("0.90")).quantize(CENT)
        total = (nightly * q.nights).quantize(CENT)
        taxes = (total - total / (1 + VAT_RATE)).quantize(CENT)
        city_tax = (CITY_TAX_PER_ADULT_NIGHT * q.adults * q.nights).quantize(CENT)
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
            rooms_left=self._free(room, q),
        )

    async def search(self, query: AvailabilityQuery) -> list[Offer]:
        if self._fault("sold_out"):
            return []
        guests = query.adults + len(query.children_ages)
        offers = []
        for room in self.store.rooms:
            if room["max"] >= guests and self._free(room, query) > 0:
                offers += [self._offer(room, "FLEX", query), self._offer(room, "NR", query)]
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
        room = next((r for r in self.store.rooms if r["code"] == code), None)
        if room is None or plan not in ("FLEX", "NR") or self._free(room, q) <= 0:
            return None
        if room["max"] < q.adults + len(q.children_ages):
            return None
        if self._fault("price_change"):
            self.store.rate_bump += Decimal("10.00")
        return self._offer(room, plan, q)

    async def book(self, quote: Quote, idempotency_key: str) -> BookingResult:
        if idempotency_key in self.store.by_key:  # idempotent replay
            return BookingResult(status=BookingState.CONFIRMED, reference=self.store.by_key[idempotency_key])
        if self._fault("timeout_before_success"):
            raise SupplierTimeout()
        if self._fault("fail_book"):
            raise SupplierError("Supplier rejected the reservation")
        o = quote.offer
        room = next(r for r in self.store.rooms if r["code"] == o.room_type)
        if self._free(room, o.query) <= 0:
            raise SupplierError("Room is no longer available")
        for d in self._nights(o.query):
            self.store.sold[(room["code"], d)] = self.store.sold.get((room["code"], d), 0) + 1
        ref = "FK-" + secrets.token_hex(4).upper()
        self.store.reservations[ref] = {"quote": quote, "status": "CONFIRMED"}
        self.store.by_key[idempotency_key] = ref
        if self._fault("timeout_after_success"):
            raise SupplierTimeout()
        return BookingResult(status=BookingState.CONFIRMED, reference=ref)

    def _reservation(self, ref: str) -> Reservation:
        row = self.store.reservations[ref]
        o = row["quote"].offer
        return Reservation(
            reference=ref,
            room_name=o.room_name,
            check_in=o.query.check_in,
            check_out=o.query.check_out,
            guest_last_name=row["quote"].guest.last_name,
            status=row["status"],
            total=o.total,
            currency=o.currency,
        )

    async def find_by_idempotency_key(self, idempotency_key: str) -> Reservation | None:
        ref = self.store.by_key.get(idempotency_key)
        return self._reservation(ref) if ref else None

    async def lookup(self, reference: str, last_name: str) -> Reservation | None:
        ref = reference.strip().upper()
        if ref not in self.store.reservations:
            return None
        found = self._reservation(ref)
        if found.guest_last_name.casefold() != last_name.strip().casefold():
            return None  # same answer as "not found": no reference enumeration
        return found
