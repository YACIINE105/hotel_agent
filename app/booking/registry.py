from app.booking.connectors.base import ReservationConnector
from app.booking.connectors.fake import FakeReservationConnector
from app.errors import AppError
from app.models import Property


def connector_for(prop: Property) -> ReservationConnector:
    kind = (prop.connector or {}).get("type", "fake")
    if kind == "fake":
        return FakeReservationConnector(prop.id, prop.timezone, prop.currency, prop.connector)
    # Real adapters (apaleo, mews, siteminder_mcp) register here once sandbox access exists.
    raise AppError(f"Reservation connector '{kind}' is not available", status_code=501)
