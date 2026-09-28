"""Typed tools offered to the model. The model proposes; this module validates and authorizes.

Confirming a booking is deliberately not a tool: it needs an explicit guest action.
"""

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.agent.language import HANDED_OFF, QUOTE_READY
from app.booking.contracts import AvailabilityQuery, Capability, GuestDetails
from app.booking.service import BookingService, offer_view, quote_view
from app.errors import AppError
from app.models import Conversation, Handoff

SCHEMAS = {
    "search_availability": {
        "requires": Capability.SEARCH,
        "description": "Search live room availability and prices for specific dates and guests.",
        "parameters": {
            "type": "object",
            "properties": {
                "check_in": {"type": "string", "description": "YYYY-MM-DD"},
                "check_out": {"type": "string", "description": "YYYY-MM-DD"},
                "adults": {"type": "integer", "minimum": 1},
                "children_ages": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["check_in", "check_out", "adults"],
        },
    },
    "prepare_booking": {
        "requires": Capability.QUOTE,
        "description": "Show the guest a booking summary with a Confirm button for one offer. "
        "Does not book; the guest must press Confirm.",
        "parameters": {
            "type": "object",
            "properties": {
                "offer_id": {"type": "string"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "email": {"type": "string"},
                "phone": {"type": "string"},
                "special_requests": {"type": "string"},
            },
            "required": ["offer_id", "first_name", "last_name", "email"],
        },
    },
    "find_reservation": {
        "requires": Capability.LOOKUP,
        "description": "Find an existing reservation by booking reference and the guest's last name.",
        "parameters": {
            "type": "object",
            "properties": {"reference": {"type": "string"}, "last_name": {"type": "string"}},
            "required": ["reference", "last_name"],
        },
    },
    "handoff_to_staff": {
        "requires": None,
        "description": "Transfer the conversation to hotel staff when the guest asks for a person, "
        "or the request needs staff (complaints, changes, exceptions, unknown answers).",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
}


BOOKING_INTENT = re.compile(
    r"\b(rooms?|book(ing|ed)?|reserv\w*|availability|vacanc\w*|price|rates?|cost|nights?|tonight|"
    r"tomorrow|weekend|check.?in date|chambres?|réserv\w*|disponibilités?|prix|tarifs?|nuits?|séjour|ce soir|demain)\b"
    r"|غرف|حجز|احجز|سعر|أسعار|ليلة|ليال|الليلة|غدا"
    r"|\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}",
    re.IGNORECASE,
)
HUMAN_INTENT = re.compile(
    r"\b(human|person|someone|staff|manager|agent|reception(ist)?|complain\w*|speak to|talk to|refund|cancel\w*|"
    r"change my|modify|personne|humain|responsable|plainte|annul\w*|modifier|conseiller)\b"
    r"|موظف|شخص|مدير|إنسان|شكوى|أشتكي|إلغاء|الغاء|تعديل|استرداد",
    re.IGNORECASE,
)
# An explicit request for a person: handed off at once, without asking the model.
EXPLICIT_HUMAN = re.compile(
    r"\b(talk|speak|chat) (to|with) (a |an |the )?(human|person|someone|staff|member of staff|manager|agent|"
    r"reception(ist)?|real person)|\b(member of staff|staff member|real person|human agent)\b"
    r"|\b(parler (à|a) (un|une|le|la) (personne|humain|conseiller|responsable|réception))"
    r"|(أكلم|اكلم|أتحدث مع|اتحدث مع|التحدث مع|أريد|اريد|عاوز|بدي|نحب)\s*(أحد\s*)?(موظف|الموظفين|شخص|مدير|الاستقبال)",
    re.IGNORECASE,
)
RESERVATION_INTENT = re.compile(r"\b(reference|confirmation|my booking|ma réservation|FK-)|رقم الحجز|حجزي", re.IGNORECASE)


def check_guest_details_given(email: str, guest_text: str) -> str | None:
    """Small models invent names and emails to complete a tool call. The email must appear in what
    the guest actually typed (or submitted in a form) in this conversation."""
    if email.casefold() not in guest_text.casefold():
        return ("The guest has not given these details. Ask the guest for their first name, last name "
                "and email, and use exactly what they write.")
    return None


def tool_definitions(capabilities: frozenset[Capability], text: str = "", booking_context: bool = True) -> list[dict]:
    """Offer only the tools this turn could need.

    Small models call search_availability for any question ("is parking free?"). Booking tools
    are offered only when the message or conversation shows booking intent; this also keeps the
    prompt shorter.
    """
    wanted = set()
    # Small models hand off on routine questions, which pauses the AI; offer it only when asked.
    if HUMAN_INTENT.search(text):
        wanted.add("handoff_to_staff")
    if booking_context or BOOKING_INTENT.search(text):
        wanted |= {"search_availability", "prepare_booking", "find_reservation"}
    if RESERVATION_INTENT.search(text):
        wanted.add("find_reservation")
    return [
        {"type": "function", "function": {"name": name, "description": s["description"], "parameters": s["parameters"]}}
        for name, s in SCHEMAS.items()
        if name in wanted and (s["requires"] is None or s["requires"] in capabilities)
    ]


@dataclass
class ToolOutcome:
    result: dict  # returned to the model
    events: list[dict] = field(default_factory=list)  # sent to the guest UI
    # Localized fixed reply that ends the turn: critical steps don't depend on model wording.
    final_reply: dict | None = None


class ToolExecutor:
    def __init__(self, booking: BookingService, conversation: Conversation):
        self.booking, self.conversation = booking, conversation
        self.offers: set[str] = set()  # only offers found in this conversation may be quoted
        self.guest_text = ""  # everything the guest typed in this conversation (set by the orchestrator)

    async def run(self, name: str, raw_args: str) -> ToolOutcome:
        spec = SCHEMAS.get(name)
        if spec is None or (spec["requires"] and spec["requires"] not in self.booking.connector.capabilities):
            return ToolOutcome({"error": f"Tool {name} is not available"})
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError()
        except ValueError:
            return ToolOutcome({"error": "Arguments must be a JSON object"})
        try:
            return await getattr(self, name)(args)
        except ValidationError as exc:
            errors = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
            return ToolOutcome({"error": f"Invalid arguments: {errors}"})
        except AppError as exc:
            return ToolOutcome({"error": exc.detail})

    async def search_availability(self, args: dict) -> ToolOutcome:
        query = AvailabilityQuery.model_validate({k: args.get(k) for k in ("check_in", "check_out", "adults")}
                                                 | {"children_ages": args.get("children_ages") or []})
        offers = await self.booking.search(query)
        self.offers |= {o.offer_id for o in offers}
        views = [offer_view(o) for o in offers[:8]]
        if not views:
            return ToolOutcome({"offers": [], "note": "No rooms available for these dates and guests."})
        return ToolOutcome(
            {"offers": views, "note": "Offers are displayed to the guest as cards."},
            [{"type": "offers", "offers": views}],
        )

    async def prepare_booking(self, args: dict) -> ToolOutcome:
        offer_id = str(args.get("offer_id", ""))
        if offer_id not in self.offers:
            return ToolOutcome({"error": "Unknown offer_id; search availability first and use an offer_id from the results."})
        guest = GuestDetails.model_validate(
            {k: args[k] for k in ("first_name", "last_name", "email", "phone", "special_requests") if args.get(k)}
        )
        problem = check_guest_details_given(guest.email, self.guest_text)
        if problem:
            return ToolOutcome({"error": problem})
        record = await self.booking.create_quote(self.conversation.id, offer_id, guest)
        view = quote_view(record)
        return ToolOutcome(
            {"quote": view, "note": "The summary with a Confirm button is shown. Ask the guest to review "
             "and press Confirm. The booking is NOT made yet."},
            [{"type": "quote", "quote": view}],
            final_reply=QUOTE_READY,
        )

    async def find_reservation(self, args: dict) -> ToolOutcome:
        ref, last = str(args.get("reference", ""))[:40], str(args.get("last_name", ""))[:80]
        found = await self.booking.lookup(ref, last)
        if not found:
            return ToolOutcome({"found": False, "note": "No reservation matches that reference and last name."})
        data = found.model_dump(mode="json")
        return ToolOutcome({"found": True, "reservation": data}, [{"type": "reservation", "reservation": data}])

    async def handoff_to_staff(self, args: dict) -> ToolOutcome:
        session = self.booking.session
        session.add(Handoff(conversation_id=self.conversation.id, property_id=self.conversation.property_id,
                            reason=str(args.get("reason", ""))[:500]))
        self.conversation.status = "HANDOFF"
        self.conversation.ai_paused = True
        await session.commit()
        return ToolOutcome({"handed_off": True, "note": "Tell the guest a team member will reply here soon."},
                           [{"type": "handoff"}], final_reply=HANDED_OFF)
