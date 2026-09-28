"""Which hotel suppliers the travel shopper uses: real ones when keys are set, simulated for demos."""

import httpx

from app.config import Settings
from app.shopper.suppliers.google_hotels import GoogleHotelsSupplier
from app.shopper.suppliers.liteapi import LiteApiSupplier
from app.shopper.suppliers.simulated import simulated_suppliers


def build_suppliers(settings: Settings, client: httpx.AsyncClient) -> list:
    suppliers: list = []
    if settings.liteapi_key:
        suppliers.append(LiteApiSupplier(settings.liteapi_key, client, settings.liteapi_nationality,
                                         sandbox=settings.liteapi_sandbox))
    if settings.serpapi_key:
        suppliers.append(GoogleHotelsSupplier(settings.serpapi_key, client))
    if settings.shop_simulated_suppliers:
        suppliers += simulated_suppliers()
    return suppliers


def supplier_status(suppliers: list) -> list[dict]:
    """For the page: what is connected. Booking.com Demand and Expedia Rapid need partner approval."""
    connected = {s.name for s in suppliers if s.configured}
    rows = [{"name": s.name, "label": s.label, "bookable": s.bookable, "simulated": s.simulated, "connected": True}
            for s in suppliers if s.configured]
    for name, label, bookable, note in (
        ("liteapi", "LiteAPI (2M+ hotels, bookable)", True, "set LITEAPI_KEY (free sandbox)"),
        ("google_hotels", "Google Hotels: Booking.com, Expedia, Hotels.com prices", False, "set SERPAPI_KEY"),
        ("booking_demand", "Booking.com Demand API", True, "requires Booking.com affiliate approval"),
        ("expedia_rapid", "Expedia Rapid API", True, "requires Expedia partner approval"),
    ):
        if name not in connected:
            rows.append({"name": name, "label": label, "bookable": bookable, "simulated": False,
                         "connected": False, "note": note})
    return rows
