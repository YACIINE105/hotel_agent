"""Guest-facing API used by the embeddable widget and any custom front end."""

import json
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.orchestrator import TurnRunner
from app.api.deps import ProvidersDep, SessionDep, SettingsDep, bearer, guest_context, property_by_slug
from app.booking.registry import connector_for
from app.booking.service import BookingService
from app.models import Conversation, Message
from app.security import sign_guest_token

router = APIRouter(prefix="/v1", tags=["guest"])
Auth = Annotated[str | None, Header()]

GREETING = {
    "en": "Hello! Welcome to {hotel}. How can I help you today?",
    "ar": "مرحباً بك في {hotel}! كيف يمكنني مساعدتك اليوم؟",
    "fr": "Bonjour et bienvenue à {hotel} ! Comment puis-je vous aider ?",
}


class SessionInput(BaseModel):
    language: str | None = Field(default=None, max_length=8)
    channel: str = Field(default="web", pattern="^(web|voice)$")


class MessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=8, max_length=64)
    language: str | None = Field(default=None, max_length=8)


class ConfirmInput(BaseModel):
    quote_id: str = Field(min_length=8, max_length=36)


@router.get("/properties/{slug}/widget-config")
async def widget_config(slug: str, session: SessionDep):
    prop = await property_by_slug(session, slug)
    return {"name": prop.name, "languages": prop.languages, "currency": prop.currency, "timezone": prop.timezone}


@router.post("/properties/{slug}/sessions", status_code=201)
async def start_session(slug: str, data: SessionInput, session: SessionDep, settings: SettingsDep):
    prop = await property_by_slug(session, slug)
    language = data.language if data.language in prop.languages else prop.languages[0]
    conv = Conversation(property_id=prop.id, channel=data.channel, language=language)
    session.add(conv)
    await session.commit()
    return {
        "conversation_id": conv.id,
        "token": sign_guest_token(settings.session_secret, prop.id, conv.id, settings.guest_session_minutes),
        "greeting": GREETING.get(language, GREETING["en"]).format(hotel=prop.name),
        "language": language,
        "voice_languages": sorted(settings.tts_language_set),
    }


def sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, data: MessageInput, request: Request,
                       settings: SettingsDep, providers: ProvidersDep, authorization: Auth = None):
    # Validate before streaming so auth failures are ordinary HTTP errors.
    async with request.app.state.sessionmaker() as session:
        await guest_context(session, settings, conversation_id, bearer(authorization))

    async def events():
        async with request.app.state.sessionmaker() as session:
            prop, conv = await guest_context(session, settings, conversation_id, bearer(authorization))
            runner = TurnRunner(session, providers, settings, prop, conv)
            async for event in runner.run(data.text, data.request_id, data.language):
                yield sse(event)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, session: SessionDep, settings: SettingsDep,
                        after: int = 0, authorization: Auth = None):
    _, conv = await guest_context(session, settings, conversation_id, bearer(authorization))
    rows = (
        await session.scalars(
            select(Message).where(Message.conversation_id == conv.id, Message.id > after).order_by(Message.id).limit(100)
        )
    ).all()
    return {
        "status": conv.status,
        "ai_paused": conv.ai_paused,
        "messages": [{"id": m.id, "sender": m.sender, "content": m.content, "created_at": m.created_at.isoformat()}
                     for m in rows],
    }


@router.post("/conversations/{conversation_id}/bookings/confirm")
async def confirm_booking(conversation_id: str, data: ConfirmInput, session: SessionDep,
                          settings: SettingsDep, authorization: Auth = None):
    """The only path that books: an explicit guest action bound to a quote in this conversation."""
    prop, conv = await guest_context(session, settings, conversation_id, bearer(authorization))
    service = BookingService(session, prop, connector_for(prop))
    result = await service.confirm(conv.id, data.quote_id)
    note = f"Booking {result['state']}"
    if result["reference"]:
        note += f", confirmation reference {result['reference']}"
    if result.get("detail"):
        note += f". {result['detail']}"
    session.add(Message(conversation_id=conv.id, property_id=prop.id, sender="system", content=note,
                        meta={"booking": {k: v for k, v in result.items() if k != "new_quote"}}))
    await session.commit()
    return result
