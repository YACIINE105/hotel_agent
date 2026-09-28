from typing import Protocol

from app.shopper.contracts import BookingOutcome, Guest, HotelResult, Recheck, StaySearch, SupplierOffer


class HotelSupplier(Protocol):
    """One source of hotel offers.

    name: adapter id; label: shown to guests; bookable: we can reserve through its API.
    configured: credentials present (unconfigured suppliers are skipped).
    """

    name: str
    label: str
    bookable: bool
    simulated: bool

    @property
    def configured(self) -> bool: ...

    async def search(self, query: StaySearch) -> list[HotelResult]: ...

    async def recheck(self, offer: SupplierOffer, query: StaySearch) -> Recheck:
        """Fresh price/availability right before booking (LiteAPI: prebook)."""
        ...

    async def book(self, offer: SupplierOffer, recheck: Recheck, query: StaySearch, guest: Guest,
                   idempotency_key: str) -> BookingOutcome: ...
