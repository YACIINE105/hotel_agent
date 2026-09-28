from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import Forbidden, NotFound
from app.models import Conversation, Organization, Property
from app.providers import Providers
from app.security import hash_api_key, verify_guest_token


def settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def providers_dep(request: Request) -> Providers:
    return request.app.state.providers


async def session_dep(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(settings_dep)]
ProvidersDep = Annotated[Providers, Depends(providers_dep)]
SessionDep = Annotated[AsyncSession, Depends(session_dep)]


async def property_by_slug(session: AsyncSession, slug: str) -> Property:
    prop = await session.scalar(select(Property).where(Property.slug == slug))
    if prop is None:
        raise NotFound("Hotel not found")
    return prop


async def guest_context(session: AsyncSession, settings: Settings, conversation_id: str,
                        token: str | None) -> tuple[Property, Conversation]:
    claims = verify_guest_token(settings.session_secret, token or "")
    if not claims or claims["c"] != conversation_id:
        raise Forbidden("Invalid or expired guest session")
    conv = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id, Conversation.property_id == claims["p"])
    )
    if conv is None:
        raise NotFound("Conversation not found")
    return await session.get(Property, conv.property_id), conv


def bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def staff_org(session: SessionDep, x_api_key: Annotated[str | None, Header()] = None) -> Organization:
    if not x_api_key:
        raise Forbidden("Staff API key required")
    org = await session.scalar(select(Organization).where(Organization.api_key_hash == hash_api_key(x_api_key)))
    if org is None:
        raise Forbidden("Invalid staff API key")
    return org


StaffDep = Annotated[Organization, Depends(staff_org)]


async def staff_property(session: AsyncSession, org: Organization, slug: str) -> Property:
    prop = await property_by_slug(session, slug)
    if prop.org_id != org.id:
        raise NotFound("Hotel not found")  # do not reveal other organizations' hotels
    return prop
