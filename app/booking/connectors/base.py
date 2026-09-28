from typing import Protocol

from app.booking.contracts import (
    AvailabilityQuery,
    BookingResult,
    Capability,
    GuestDetails,
    Offer,
    Quote,
    Reservation,
)


class ReservationConnector(Protocol):
    """Implemented once per reservation system (fake, Apaleo, Mews, SiteMinder MCP, ...).

    `book` must be idempotent for the same key. It raises SupplierTimeout when the outcome
    is unknown and SupplierError when nothing was created.
    """

    capabilities: frozenset[Capability]

    async def search(self, query: AvailabilityQuery) -> list[Offer]: ...

    async def price(self, offer_id: str) -> Offer | None:
        """Re-price an offer from live inventory; None when no longer sellable."""
        ...

    async def book(self, quote: Quote, idempotency_key: str) -> BookingResult: ...

    async def find_by_idempotency_key(self, idempotency_key: str) -> Reservation | None: ...

    async def lookup(self, reference: str, last_name: str) -> Reservation | None: ...
