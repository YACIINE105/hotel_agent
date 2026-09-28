"""Travel-shopper contracts: search many suppliers, compare, re-check, book.

A supplier is either *bookable* (we can reserve through its API, e.g. LiteAPI) or *redirect*
(a price source such as Google Hotels listing Booking.com / Expedia / Hotels.com offers; the guest
continues on that site). Money is Decimal; every offer says whether it is simulated.
"""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


CITY_ALIASES = {
    "الغردقة": "Hurghada", "الغردقه": "Hurghada", "hurghada": "Hurghada", "hourghada": "Hurghada",
    "شرم الشيخ": "Sharm El Sheikh", "charm el cheikh": "Sharm El Sheikh", "القاهرة": "Cairo", "القاهره": "Cairo",
    "le caire": "Cairo", "دبي": "Dubai", "إسطنبول": "Istanbul", "اسطنبول": "Istanbul", "istanbul": "Istanbul",
    "الجزائر": "Algiers", "الجزائر العاصمة": "Algiers", "alger": "Algiers",
}


class StaySearch(Strict):
    city: str = Field(min_length=2, max_length=80)
    country_code: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2, e.g. EG")
    check_in: date
    check_out: date
    adults: int = Field(default=2, ge=1, le=8)
    children_ages: list[int] = Field(default_factory=list, max_length=6)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    max_price_per_night: Decimal | None = Field(default=None, gt=0)
    min_stars: int | None = Field(default=None, ge=1, le=5)
    refundable_only: bool = False

    @model_validator(mode="after")
    def _check(self):
        nights = (self.check_out - self.check_in).days
        if nights < 1 or nights > 30:
            raise ValueError("Stay must be between 1 and 30 nights")
        if any(a < 0 or a > 17 for a in self.children_ages):
            raise ValueError("Child ages must be between 0 and 17")
        self.country_code = self.country_code.upper()
        self.city = CITY_ALIASES.get(self.city.strip().casefold(), CITY_ALIASES.get(self.city.strip(), self.city.strip()))
        self.currency = self.currency.upper()
        return self

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


class SupplierOffer(Strict):
    supplier: str                    # adapter name, e.g. "liteapi", "google_hotels", "sim_simstay"
    source: str                      # where the guest would book: "LiteAPI", "Booking.com", "SimStay"
    kind: Literal["bookable", "redirect"]
    supplier_offer_id: str           # adapter-specific id (offerId, token, ...)
    room_name: str
    board: str = ""                  # "Room only", "Breakfast included", ...
    refundable: bool | None = None   # None when the source doesn't say
    free_cancellation_until: str | None = None
    total: Decimal
    currency: str
    per_night: Decimal | None = None
    link: str | None = None          # redirect offers: continue on the provider's site
    via: str | None = None           # metasearch that found the deal, e.g. "trivago"
    simulated: bool = False


class HotelResult(Strict):
    hotel_key: str                   # stable merge key across suppliers
    name: str
    address: str = ""
    stars: int | None = None
    rating: float | None = None      # out of 10
    reviews: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    image: str | None = None
    amenities: list[str] = Field(default_factory=list)
    provider: str | None = None      # data provider to credit on the card, e.g. "trivago"
    offers: list[SupplierOffer] = Field(default_factory=list)


class Guest(Strict):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)


class Recheck(Strict):
    """A fresh, bookable price for one offer (LiteAPI prebook, or simulator re-price)."""

    available: bool
    total: Decimal | None = None
    currency: str | None = None
    refundable: bool | None = None
    board: str | None = None
    supplier_ref: str | None = None  # e.g. LiteAPI prebookId; needed to book
    detail: str = ""


class BookingOutcome(Strict):
    status: Literal["CONFIRMED", "FAILED", "UNKNOWN"]
    supplier_reference: str | None = None
    hotel_confirmation: str | None = None
    detail: str = ""


class ShopTerms(Strict):
    hotel_name: str
    source: str
    room_name: str
    board: str
    refundable: bool | None
    check_in: date
    check_out: date
    adults: int
    children_ages: list[int]
    total: Decimal
    currency: str
    guest: Guest
    rechecked_at: datetime

    def terms_hash(self) -> str:
        data = self.model_dump(mode="json")
        data.pop("rechecked_at")
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
