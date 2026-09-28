"""Structured booking steps submitted from widget forms instead of chat text.

They reuse the agent's validated tools and write the same history the model would, so the
conversation stays consistent if the guest continues by chat.
"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import language as lang
from app.agent.tools import ToolExecutor, ToolOutcome
from app.booking.registry import connector_for
from app.booking.service import BookingService
from app.errors import AppError
from app.models import Conversation, Message, Property

OFFERS_SHOWN = {
    "en": "Here are the rooms available for your dates.",
    "ar": "هذه الغرف المتاحة في التواريخ التي اخترتها.",
    "fr": "Voici les chambres disponibles pour vos dates.",
}
NO_ROOMS = {
    "en": "Sorry, nothing is available for those dates and guests. Try other dates?",
    "ar": "عذراً، لا تتوفر غرف لهذه التواريخ وعدد الضيوف. هل تود تجربة تواريخ أخرى؟",
    "fr": "Désolé, rien n'est disponible pour ces dates. Voulez-vous essayer d'autres dates ?",
}


async def _known_offers(session: AsyncSession, conv: Conversation) -> set[str]:
    rows = (await session.scalars(select(Message.meta).where(Message.conversation_id == conv.id))).all()
    return {o["offer_id"] for meta in rows for o in (meta or {}).get("offers", [])}


async def run_form_step(session: AsyncSession, prop: Property, conv: Conversation, tool: str, args: dict,
                        guest_text: str) -> tuple[ToolOutcome, str]:
    executor = ToolExecutor(BookingService(session, prop, connector_for(prop)), conv)
    executor.offers = await _known_offers(session, conv)
    executor.guest_text = json.dumps(args)  # the guest typed these details into the form
    outcome = await executor.run(tool, json.dumps(args))
    if "error" in outcome.result:
        raise AppError(outcome.result["error"], status_code=422, code="invalid_booking_step")

    language = conv.language or prop.languages[0]
    if tool == "search_availability":
        reply = lang.localized(OFFERS_SHOWN if outcome.result.get("offers") else NO_ROOMS, language)
    else:
        reply = lang.localized(lang.QUOTE_READY, language)
    meta: dict = {"source": "form", "tool_trace": [{
        "id": f"form_{tool}", "name": tool, "arguments": json.dumps(args),
        "result": json.dumps(outcome.result, ensure_ascii=False)[:6000]}]}
    for event in outcome.events:
        if event["type"] == "offers":
            meta["offers"] = event["offers"]
        elif event["type"] == "quote":
            meta["quote_id"] = event["quote"]["quote_id"]
    session.add(Message(conversation_id=conv.id, property_id=prop.id, sender="guest", content=guest_text,
                        meta={"source": "form"}))
    session.add(Message(conversation_id=conv.id, property_id=prop.id, sender="ai", content=reply, meta=meta))
    await session.commit()
    return outcome, reply
