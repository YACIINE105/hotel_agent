"""Provider-independent booking contracts. Money is Decimal; dates are property-local."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class Capability(StrEnum):
    SEARCH = "SEARCH"
    QUOTE = "QUOTE"
    BOOK = "BOOK"
    LOOKUP = "LOOKUP"
    CANCEL = "CANCEL"


class BookingState(StrEnum):
    SUBMITTING = "SUBMITTING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    PRICE_CHANGED = "PRICE_CHANGED"
    UNKNOWN = "UNKNOWN"
    STAFF_REVIEW = "STAFF_REVIEW"


TRANSITIONS: dict[BookingState, set[BookingState]] = {
    BookingState.SUBMITTING: {
        BookingState.CONFIRMED,
        BookingState.FAILED,
        BookingState.PRICE_CHANGED,
        BookingState.UNKNOWN,
    },
    BookingState.UNKNOWN: {BookingState.CONFIRMED, BookingState.FAILED, BookingState.STAFF_REVIEW},
    BookingState.CONFIRMED: set(),
    BookingState.FAILED: set(),
    BookingState.PRICE_CHANGED: set(),
    BookingState.STAFF_REVIEW: {BookingState.CONFIRMED, BookingState.FAILED},
}


def check_transition(current: BookingState, new: BookingState) -> None:
    if new not in TRANSITIONS[current]:
        raise ValueError(f"Illegal booking transition {current} -> {new}")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AvailabilityQuery(Strict):
    check_in: date
    check_out: date
    adults: int = Field(ge=1, le=10)
    children_ages: list[int] = Field(default_factory=list, max_length=6)

    @field_validator("children_ages")
    @classmethod
    def _ages(cls, v):
        if any(a < 0 or a > 17 for a in v):
            raise ValueError("Child ages must be between 0 and 17")
        return v

    @model_validator(mode="after")
    def _dates(self):
        nights = (self.check_out - self.check_in).days
        if nights < 1 or nights > 30:
            raise ValueError("Stay must be between 1 and 30 nights")
        return self

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


class CancellationPolicy(Strict):
    refundable: bool
    free_until: datetime | None = None  # property-local deadline, timezone-aware
    description: str


class Offer(Strict):
    offer_id: str
    room_type: str
    room_name: str
    rate_plan: str
    query: AvailabilityQuery
    max_occupancy: int
    meal_plan: str
    currency: str
    total: Decimal
    taxes_included: Decimal
    pay_at_property_fees: Decimal = Decimal("0")
    payment_timing: str  # e.g. "Pay at the property", "Deposit now"
    cancellation: CancellationPolicy
    rooms_left: int | None = None


class GuestDetails(Strict):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    special_requests: str = Field(default="", max_length=500)


class Quote(Strict):
    offer: Offer
    guest: GuestDetails
    expires_at: datetime

    def terms_hash(self) -> str:
        """Hash of every term the guest agrees to; any change requires reconfirmation."""
        o = self.offer
        terms = {
            "room": o.room_type,
            "rate": o.rate_plan,
            "q": o.query.model_dump(mode="json"),
            "currency": o.currency,
            "total": str(o.total),
            "fees": str(o.pay_at_property_fees),
            "meal": o.meal_plan,
            "payment": o.payment_timing,
            "cancel": o.cancellation.model_dump(mode="json"),
            "guest": self.guest.model_dump(mode="json"),
        }
        return hashlib.sha256(json.dumps(terms, sort_keys=True).encode()).hexdigest()


class BookingResult(Strict):
    status: BookingState  # CONFIRMED | FAILED only; UNKNOWN is raised as SupplierTimeout
    reference: str | None = None
    detail: str = ""


class Reservation(Strict):
    reference: str
    room_name: str
    check_in: date
    check_out: date
    guest_last_name: str
    status: str
    total: Decimal
    currency: str


class SupplierTimeout(Exception):
    """The provider may or may not have acted. Reconcile before any retry."""


class SupplierError(Exception):
    """Definite failure; nothing was created."""
