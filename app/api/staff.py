"""Staff API: inbox, takeover, knowledge, bookings. Every query is scoped to the caller's organization."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.api.deps import ProvidersDep, SessionDep, SettingsDep, StaffDep, staff_property
from app.booking.connectors.fake import inject_fault
from app.booking.registry import connector_for
from app.errors import AppError, NotFound
from app.knowledge.service import KnowledgeService
from app.models import BookingIntent, Conversation, Handoff, Message, Property, Turn

router = APIRouter(prefix="/v1/staff", tags=["staff"])


class ReplyInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class TypingInput(BaseModel):
    typing: bool


class TakeoverInput(BaseModel):
    paused: bool


class FactInput(BaseModel):
    language: str = Field(default="en", max_length=8)
    content: str = Field(min_length=1, max_length=2000)
    approved: bool = True


class DocumentInput(BaseModel):
    source: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=200_000)


class FaultInput(BaseModel):
    fault: str = Field(pattern="^(price_change|timeout_after_success|timeout_before_success|fail_book|sold_out)$")


async def _conversation(session, org, conversation_id) -> Conversation:
    conv = await session.scalar(
        select(Conversation).join(Property, Property.id == Conversation.property_id)
        .where(Conversation.id == conversation_id, Property.org_id == org.id)
    )
    if conv is None:
        raise NotFound("Conversation not found")
    return conv


@router.get("/properties")
async def properties(org: StaffDep, session: SessionDep):
    rows = (await session.scalars(select(Property).where(Property.org_id == org.id).order_by(Property.id))).all()
    return [{"slug": p.slug, "name": p.name, "languages": p.languages, "connector": p.connector.get("type")}
            for p in rows]


@router.get("/properties/{slug}/conversations")
async def conversations(slug: str, org: StaffDep, session: SessionDep):
    prop = await staff_property(session, org, slug)
    rows = (
        await session.scalars(
            select(Conversation).where(Conversation.property_id == prop.id).order_by(Conversation.updated_at.desc()).limit(100)
        )
    ).all()
    out = []
    for c in rows:
        last = await session.scalar(
            select(Message).where(Message.conversation_id == c.id).order_by(Message.id.desc()).limit(1)
        )
        out.append({"id": c.id, "status": c.status, "ai_paused": c.ai_paused, "language": c.language,
                    "channel": c.channel, "updated_at": c.updated_at.isoformat(),
                    "last_message": last.content[:140] if last else ""})
    return out


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: str, org: StaffDep, session: SessionDep):
    conv = await _conversation(session, org, conversation_id)
    messages = (await session.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id))).all()
    turns = {t.message_id: t for t in (await session.scalars(select(Turn).where(Turn.conversation_id == conv.id))).all()}
    handoffs = (await session.scalars(select(Handoff).where(Handoff.conversation_id == conv.id))).all()
    return {
        "id": conv.id, "status": conv.status, "ai_paused": conv.ai_paused, "language": conv.language,
        "handoffs": [{"reason": h.reason, "status": h.status, "created_at": h.created_at.isoformat()} for h in handoffs],
        "messages": [
            {"id": m.id, "sender": m.sender, "content": m.content, "meta": m.meta,
             "audit": ({"sources": turns[m.id].sources, "tools": turns[m.id].tools,
                        "timings": turns[m.id].timings, "model": turns[m.id].model} if m.id in turns else None)}
            for m in messages
        ],
    }


@router.post("/conversations/{conversation_id}/reply", status_code=201)
async def reply(conversation_id: str, data: ReplyInput, request: Request, org: StaffDep, session: SessionDep):
    conv = await _conversation(session, org, conversation_id)
    conv.staff_typing_until = None
    conv.ai_paused, conv.status = True, "HANDOFF"  # a human reply takes over the conversation
    message = Message(conversation_id=conv.id, property_id=conv.property_id, sender="staff", content=data.text)
    session.add(message)
    await session.commit()
    await request.app.state.broker.publish(conv.id, "message")
    return {"id": message.id}


@router.post("/conversations/{conversation_id}/typing", status_code=204)
async def typing(conversation_id: str, data: TypingInput, request: Request, org: StaffDep, session: SessionDep):
    """Staff typing indicator. The inbox sends this every ~2 s while typing; it expires after 5 s.

    Stored on the conversation row so every API instance sees it; guests are notified by push.
    """
    conv = await _conversation(session, org, conversation_id)
    until = datetime.now(timezone.utc) + timedelta(seconds=5) if data.typing else None
    # Core UPDATE that keeps updated_at, so typing doesn't reorder the inbox.
    await session.execute(update(Conversation).where(Conversation.id == conv.id)
                          .values(staff_typing_until=until, updated_at=Conversation.updated_at))
    await session.commit()
    await request.app.state.broker.publish(conv.id, "typing")


@router.post("/conversations/{conversation_id}/takeover")
async def takeover(conversation_id: str, data: TakeoverInput, request: Request, org: StaffDep, session: SessionDep):
    conv = await _conversation(session, org, conversation_id)
    conv.ai_paused = data.paused
    conv.status = "HANDOFF" if data.paused else "OPEN"
    if not data.paused:
        for h in (await session.scalars(select(Handoff).where(Handoff.conversation_id == conv.id))).all():
            h.status = "RESOLVED"
    await session.commit()
    await request.app.state.broker.publish(conv.id, "state")
    return {"ai_paused": conv.ai_paused, "status": conv.status}


@router.get("/properties/{slug}/facts")
async def facts(slug: str, org: StaffDep, session: SessionDep):
    from app.models import HotelFact

    prop = await staff_property(session, org, slug)
    rows = (await session.scalars(select(HotelFact).where(HotelFact.property_id == prop.id).order_by(HotelFact.key))).all()
    return [{"key": f.key, "language": f.language, "content": f.content, "approved": f.approved} for f in rows]


@router.put("/properties/{slug}/facts/{key}")
async def put_fact(slug: str, key: str, data: FactInput, org: StaffDep, session: SessionDep):
    prop = await staff_property(session, org, slug)
    if not key.replace("_", "").isalnum() or len(key) > 80:
        raise AppError("Fact keys use letters, digits, and underscores", status_code=422)
    await KnowledgeService(session, None, prop.id).upsert_fact(key, data.language, data.content, data.approved)
    await session.commit()
    return {"key": key, "language": data.language}


@router.post("/properties/{slug}/documents", status_code=201)
async def add_document(slug: str, data: DocumentInput, org: StaffDep, session: SessionDep, providers: ProvidersDep):
    prop = await staff_property(session, org, slug)
    count = await KnowledgeService(session, providers, prop.id).ingest(data.source, data.text)
    await session.commit()
    return {"chunks": count}


@router.get("/properties/{slug}/bookings")
async def bookings(slug: str, org: StaffDep, session: SessionDep):
    prop = await staff_property(session, org, slug)
    rows = (
        await session.scalars(select(BookingIntent).where(BookingIntent.property_id == prop.id)
                              .order_by(BookingIntent.created_at.desc()).limit(100))
    ).all()
    return [{"id": b.id, "state": b.state, "reference": b.external_ref, "conversation_id": b.conversation_id,
             "detail": b.detail, "created_at": b.created_at.isoformat()} for b in rows]


@router.post("/properties/{slug}/simulator/faults")
async def fault(slug: str, data: FaultInput, org: StaffDep, session: SessionDep, settings: SettingsDep):
    """Development only: script a supplier failure on the fake reservation system."""
    prop = await staff_property(session, org, slug)
    if settings.environment != "development" or prop.connector.get("type") != "fake":
        raise AppError("Simulator faults are only available for fake connectors in development", status_code=403)
    connector_for(prop)  # ensure the simulator store exists
    inject_fault(prop.id, data.fault)
    return {"armed": data.fault}
