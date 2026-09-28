"""Persistent records. Every business row carries property_id; queries must filter on it."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # SHA-256 of the staff API key; OIDC replaces this later.
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Property(Base):
    __tablename__ = "properties"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en"])
    # {"type": "fake", ...connector options}; credentials are resolved server-side only.
    connector: Mapped[dict] = mapped_column(JSON, default=lambda: {"type": "fake"})
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class HotelFact(Base):
    """Approved, structured hotel information. The fast, reliable knowledge path."""

    __tablename__ = "hotel_facts"
    __table_args__ = (UniqueConstraint("property_id", "key", "language"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(8), default="en")
    content: Mapped[str] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str] = mapped_column(String(200), default="")


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="web")
    language: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN | HANDOFF | CLOSED
    ai_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # Shared across API instances (was process memory): staff typing indicator expiry.
    staff_typing_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    sender: Mapped[str] = mapped_column(String(10))  # guest | ai | staff | system
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Turn(Base):
    """Audit of one AI turn: sources, tool calls, model, latency."""

    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("conversation_id", "request_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"))
    model: Mapped[str] = mapped_column(String(200), default="")
    sources: Mapped[list] = mapped_column(JSON, default=list)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QuoteRecord(Base):
    __tablename__ = "quotes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    data: Mapped[dict] = mapped_column(JSON)  # serialized contracts.Quote
    terms_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BookingIntent(Base):
    __tablename__ = "booking_intents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    # One intent per quote: a double click cannot create two bookings.
    quote_id: Mapped[str] = mapped_column(ForeignKey("quotes.id"), unique=True)
    state: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    external_ref: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Effect(Base):
    """Ledger of every external side effect attempt, for reconciliation and audit."""

    __tablename__ = "effects"
    id: Mapped[int] = mapped_column(primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("booking_intents.id"), index=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # BOOK | LOOKUP | CANCEL
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20))  # STARTED | SUCCEEDED | FAILED | UNKNOWN
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Handoff(Base):
    __tablename__ = "handoffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
