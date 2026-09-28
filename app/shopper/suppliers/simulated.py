"""Simulated marketplace for offline demos and tests.

Fictional hotels only (so no invented prices are attached to real properties), sold by fictional
sites with different price levels, coverage and cancellation terms. Prices are deterministic per
(hotel, site, dates) so tests are stable; a small deterministic drift on re-check and occasional
sell-outs exercise the "re-check before booking" logic.
"""

import hashlib
import secrets
from decimal import Decimal

from app.shopper.contracts import BookingOutcome, Guest, HotelResult, Recheck, StaySearch, SupplierOffer

CENT = Decimal("0.01")

CITIES = {
    ("hurghada", "EG"): ["Coral Lagoon Resort", "Palm Marina Hotel", "Red Sea Pearl Suites", "Sunset Dunes Inn",
                         "Blue Reef Beach Club", "Giftun View Hotel", "Desert Rose Boutique", "Marina Lights Hotel"],
    ("sharm el sheikh", "EG"): ["Naama Bay Palms", "Tiran Horizon Resort", "Coral Garden Lodge", "Sinai Star Hotel",
                                "Shark Bay Retreat", "Old Market Inn"],
    ("cairo", "EG"): ["Nile Corniche Hotel", "Giza View Suites", "Zamalek Garden Inn", "Downtown Heritage Hotel",
                      "Heliopolis Grand", "Khan Courtyard Hotel"],
    ("dubai", "AE"): ["Creek Pearl Hotel", "Marina Sky Tower", "Desert Oasis Resort", "Jumeirah Sands Inn",
                      "Old Souk Boutique", "Palm Crescent Suites"],
    ("istanbul", "TR"): ["Bosphorus Terrace Hotel", "Sultanahmet Garden Inn", "Galata Loft Suites", "Taksim Central Hotel",
                         "Golden Horn Residence", "Kadikoy Seaside Hotel"],
    ("algiers", "DZ"): ["Casbah Heights Hotel", "Bay of Algiers Suites", "Hydra Garden Inn", "Sablettes Beach Hotel",
                        "Didouche Central Hotel", "Botanical Garden Lodge"],
}

# name, price factor, coverage (share of hotels listed), refundable share, bookable
SITES = [
    ("sim_simstay", "SimStay", Decimal("1.00"), 0.95, 0.7, True),
    ("sim_demotrip", "DemoTrip", Decimal("0.96"), 0.85, 0.4, True),
    ("sim_mockbooker", "MockBooker", Decimal("1.04"), 0.9, 0.9, True),
    ("sim_pricewatch", "PriceWatch (compare only)", Decimal("0.93"), 0.6, 0.5, False),
]
ROOMS = [("Standard Double Room", "Room only", Decimal("62")), ("Superior Room", "Breakfast included", Decimal("85")),
         ("Deluxe Sea View", "Half board", Decimal("128")), ("Family Suite", "Breakfast included", Decimal("165"))]


def _h(*parts) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big")


def catalog(query: StaySearch) -> list[dict]:
    names = CITIES.get((query.city.strip().casefold(), query.country_code.upper()), [])
    hotels = []
    for i, name in enumerate(names):
        stars = 3 + _h(name, "stars") % 3
        hotels.append({
            "key": f"sim:{query.country_code}:{name}".casefold(), "name": name, "stars": stars,
            "rating": round(7.0 + (_h(name, "rating") % 26) / 10, 1), "reviews": 80 + _h(name, "rev") % 3000,
            "address": f"{query.city.title()} (fictional demo hotel)",
            "quality": Decimal(1) + Decimal(stars - 3) * Decimal("0.35") + Decimal(i % 3) / 10,
        })
    return hotels


class SimulatedSupplier:
    simulated = True

    def __init__(self, name: str, label: str, factor: Decimal, coverage: float, refundable_share: float, bookable: bool):
        self.name, self.label, self.factor = name, label, factor
        self.coverage, self.refundable_share, self.bookable = coverage, refundable_share, bookable
        self.bookings: dict[str, str] = {}  # idempotency key -> reference

    @property
    def configured(self) -> bool:
        return True

    def _price(self, hotel: dict, room_base: Decimal, query: StaySearch, drift: int = 0) -> Decimal:
        season = Decimal(90 + _h(query.check_in.isoformat(), hotel["key"]) % 25) / 100
        noise = Decimal(94 + _h(self.name, hotel["key"], query.check_in, room_base) % 13) / 100
        occupancy = Decimal(1) + Decimal(max(0, query.adults - 2)) * Decimal("0.35") + \
            Decimal(len([a for a in query.children_ages if a >= 6])) * Decimal("0.2")
        per_night = room_base * hotel["quality"] * season * noise * self.factor * occupancy
        return (per_night * query.nights * (Decimal(100 + drift) / 100)).quantize(CENT)

    def _offers(self, hotel: dict, query: StaySearch) -> list[SupplierOffer]:
        offers = []
        guests = query.adults + len(query.children_ages)
        for code, (room, board, base) in enumerate(ROOMS):
            if guests > 2 and room in ("Standard Double Room", "Superior Room"):
                continue
            refundable = (_h(self.name, hotel["key"], room) % 100) / 100 < self.refundable_share
            if query.refundable_only and not refundable:
                continue
            total = self._price(hotel, base, query)
            offers.append(SupplierOffer(
                supplier=self.name, source=self.label, kind="bookable" if self.bookable else "redirect",
                supplier_offer_id=f"{hotel['key']}#{code}", room_name=room, board=board, refundable=refundable,
                free_cancellation_until=(query.check_in.isoformat() + " 12:00") if refundable else None,
                total=total, currency=query.currency,
                link=None if self.bookable else f"https://example.com/pricewatch/{_h(hotel['key']) % 10**8}",
                simulated=True))
        return offers

    async def search(self, query: StaySearch) -> list[HotelResult]:
        results = []
        for hotel in catalog(query):
            if (_h(self.name, hotel["key"]) % 100) / 100 >= self.coverage:
                continue  # this site doesn't list this hotel
            offers = self._offers(hotel, query)
            if offers:
                results.append(HotelResult(hotel_key=hotel["key"], name=hotel["name"], address=hotel["address"],
                                           stars=hotel["stars"], rating=hotel["rating"], reviews=hotel["reviews"],
                                           offers=offers))
        return results

    async def recheck(self, offer: SupplierOffer, query: StaySearch) -> Recheck:
        key, _, code = offer.supplier_offer_id.partition("#")
        hotel = next((h for h in catalog(query) if h["key"] == key), None)
        if hotel is None:
            return Recheck(available=False, detail="Hotel not found")
        roll = _h(self.name, offer.supplier_offer_id, query.check_in, "recheck") % 100
        if roll < 8:
            return Recheck(available=False, detail="Sold out since the search")
        drift = 5 if roll < 20 else 0  # sometimes the price moves up a little
        room, board, base = ROOMS[int(code)]
        total = self._price(hotel, base, query, drift)
        return Recheck(available=True, total=total, currency=query.currency, refundable=offer.refundable,
                       board=board, supplier_ref=f"pre-{secrets.token_hex(4)}")

    async def book(self, offer: SupplierOffer, recheck: Recheck, query: StaySearch, guest: Guest,
                   idempotency_key: str) -> BookingOutcome:
        if not self.bookable:
            return BookingOutcome(status="FAILED", detail="This source is compare-only; continue on its site")
        if idempotency_key in self.bookings:
            return BookingOutcome(status="CONFIRMED", supplier_reference=self.bookings[idempotency_key])
        ref = f"{self.label[:3].upper()}-{secrets.token_hex(4).upper()}"
        self.bookings[idempotency_key] = ref
        return BookingOutcome(status="CONFIRMED", supplier_reference=ref, hotel_confirmation=f"H{_h(ref) % 10**6:06d}")


def simulated_suppliers() -> list[SimulatedSupplier]:
    return [SimulatedSupplier(*site) for site in SITES]
