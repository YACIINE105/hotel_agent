"""Guest-facing API used by the embeddable widget and any custom front end."""

import asyncio
import json
import random
from datetime import date, datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.forms import run_form_step
from app.agent.orchestrator import TurnRunner
from app.api.deps import ProvidersDep, SessionDep, SettingsDep, bearer, guest_context, property_by_slug
from app.booking.registry import connector_for
from app.booking.service import BookingService
from app.models import Conversation, Message
from app.ratelimit import client_ip
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


class AvailabilityInput(BaseModel):
    check_in: date
    check_out: date
    adults: int = Field(ge=1, le=10)
    children_ages: list[int] = Field(default_factory=list, max_length=6)


class QuoteInput(BaseModel):
    offer_id: str = Field(min_length=3, max_length=120)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    special_requests: str | None = Field(default=None, max_length=500)


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; values are always written in UTC.
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def staff_typing(conv: Conversation) -> bool:
    until = _aware(conv.staff_typing_until)
    return until is not None and until > datetime.now(timezone.utc)


@router.get("/properties/{slug}/widget-config")
async def widget_config(slug: str, session: SessionDep):
    prop = await property_by_slug(session, slug)
    return {"name": prop.name, "languages": prop.languages, "currency": prop.currency, "timezone": prop.timezone}


@router.post("/properties/{slug}/sessions", status_code=201)
async def start_session(slug: str, data: SessionInput, request: Request, session: SessionDep, settings: SettingsDep):
    await request.app.state.limiter.hit("session_ip", client_ip(request, settings.trust_forwarded_for))
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
    # Validate before streaming so auth failures and rate limits are ordinary HTTP errors.
    async with request.app.state.sessionmaker() as session:
        await guest_context(session, settings, conversation_id, bearer(authorization))
    await request.app.state.limiter.hit("message_conv", conversation_id)
    await request.app.state.limiter.hit("message_ip", client_ip(request, settings.trust_forwarded_for))

    async def events():
        async with request.app.state.sessionmaker() as session:
            prop, conv = await guest_context(session, settings, conversation_id, bearer(authorization))
            runner = TurnRunner(session, providers, settings, prop, conv, request.app.state.shop_suppliers)
            async for event in runner.run(data.text, data.request_id, data.language):
                yield sse(event)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, request: Request, session: SessionDep, settings: SettingsDep,
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
        "staff_typing": staff_typing(conv),
        "messages": [{"id": m.id, "sender": m.sender, "content": m.content, "created_at": m.created_at.isoformat()}
                     for m in rows],
    }


@router.post("/conversations/{conversation_id}/bookings/confirm")
async def confirm_booking(conversation_id: str, data: ConfirmInput, request: Request, session: SessionDep,
                          settings: SettingsDep, authorization: Auth = None):
    """The only path that books: an explicit guest action bound to a quote in this conversation."""
    prop, conv = await guest_context(session, settings, conversation_id, bearer(authorization))
    await request.app.state.limiter.hit("booking_conv", conv.id)
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


@router.post("/conversations/{conversation_id}/availability")
async def search_availability(conversation_id: str, data: AvailabilityInput, request: Request, session: SessionDep,
                              settings: SettingsDep, authorization: Auth = None):
    """Booking form step 1: dates and guests -> live offers, without a model call."""
    prop, conv = await guest_context(session, settings, conversation_id, bearer(authorization))
    await request.app.state.limiter.hit("booking_conv", conv.id)
    ages = ", ".join(str(a) for a in data.children_ages)
    text = f"{data.check_in} → {data.check_out} · {data.adults} adult(s)" + (f" · children aged {ages}" if ages else "")
    outcome, reply = await run_form_step(session, prop, conv, "search_availability", data.model_dump(mode="json"), text)
    return {"reply": reply, "offers": outcome.result.get("offers", [])}


@router.post("/conversations/{conversation_id}/quotes")
async def create_quote(conversation_id: str, data: QuoteInput, request: Request, session: SessionDep, settings: SettingsDep,
                       authorization: Auth = None):
    """Booking form step 2: chosen offer + guest details -> booking summary to confirm."""
    prop, conv = await guest_context(session, settings, conversation_id, bearer(authorization))
    await request.app.state.limiter.hit("booking_conv", conv.id)
    text = f"{data.first_name} {data.last_name} · {data.email}"
    outcome, reply = await run_form_step(session, prop, conv, "prepare_booking",
                                         data.model_dump(exclude_none=True), text)
    return {"reply": reply, "quote": outcome.result["quote"]}


KEEPALIVE_SECONDS = 20  # comment line only: keeps proxies from closing idle streams, no DB work
SAFETY_REFRESH_SECONDS = (90, 150)  # random range: re-read the DB in case a notification was missed


@router.get("/conversations/{conversation_id}/events")
async def live_events(conversation_id: str, request: Request, settings: SettingsDep, token: str = "", after: int = 0):
    """Push stream (SSE) of staff activity: replies, typing, and AI pause state.

    Replaces 2-second polling: an idle open widget costs one connection and no database queries.
    The token is a query parameter because browser EventSource cannot send headers.
    """
    async with request.app.state.sessionmaker() as session:
        await guest_context(session, settings, conversation_id, token)
    broker = request.app.state.broker

    async def snapshot(last_id: int) -> tuple[list[dict], dict]:
        async with request.app.state.sessionmaker() as session:
            conv = await session.get(Conversation, conversation_id)
            rows = (await session.scalars(
                select(Message).where(Message.conversation_id == conversation_id, Message.id > last_id,
                                      Message.sender == "staff").order_by(Message.id).limit(50))).all()
            state = {"ai_paused": conv.ai_paused, "staff_typing": staff_typing(conv),
                     "typing_until": _aware(conv.staff_typing_until)}
            return [{"id": m.id, "sender": m.sender, "content": m.content} for m in rows], state

    async def stream():
        loop = asyncio.get_running_loop()
        queue = broker.subscribe(conversation_id)
        last_id, sent_state = after, {}
        try:
            yield "retry: 3000\n\n"
            while True:
                messages, state = await snapshot(last_id)
                for m in messages:
                    last_id = max(last_id, m["id"])
                    yield f"event: message\ndata: {json.dumps(m, ensure_ascii=False)}\n\n"
                public = {"ai_paused": state["ai_paused"], "staff_typing": state["staff_typing"] and not messages}
                if public != sent_state:
                    sent_state = public
                    yield f"event: state\ndata: {json.dumps(public)}\n\n"
                # Sleep until a notification, the typing indicator expiring, or the (jittered) safety
                # refresh. Keep-alives in between cost no database query, so thousands of idle
                # streams don't refresh in lockstep.
                deadline = loop.time() + random.uniform(*SAFETY_REFRESH_SECONDS)
                if state["staff_typing"]:
                    typing_left = (state["typing_until"] - datetime.now(timezone.utc)).total_seconds() + 0.1
                    deadline = min(deadline, loop.time() + max(0.2, typing_left))
                while True:
                    wait = min(KEEPALIVE_SECONDS, deadline - loop.time())
                    if wait <= 0:
                        break
                    try:
                        await asyncio.wait_for(queue.get(), wait)
                        break
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": keep-alive\n\n"
        finally:
            broker.unsubscribe(conversation_id, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
