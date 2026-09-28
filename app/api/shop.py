"""Travel-shopper API for the /shop page: supplier status, search, best-of quote, confirm.

Form steps are written to the conversation as tool calls, so the agent stays consistent if the
guest continues by chat.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from app.agent import language as lang
from app.api.deps import SessionDep, SettingsDep, bearer, guest_context, property_by_slug
from app.errors import AppError
from app.models import Message
from app.shopper.contracts import Guest, StaySearch
from app.shopper.registry import supplier_status
from app.shopper.service import ShopService, quote_view, search_view
from app.shopper.tools import QUOTE_READY

router = APIRouter(prefix="/v1", tags=["travel shopper"])
Auth = Annotated[str | None, Header()]

RESULTS = {
    "en": "I searched {n} sites: {count} hotels found. The cheapest total is {best}.",
    "ar": "بحثت في {n} مواقع: وجدت {count} فندقاً. أقل سعر إجمالي هو {best}.",
    "fr": "J'ai cherché sur {n} sites : {count} hôtels trouvés. Le total le moins cher est {best}.",
}
NONE_FOUND = {
    "en": "I couldn't find hotels for that search. Try other dates or a nearby city.",
    "ar": "لم أجد فنادق لهذا البحث. جرّب تواريخ أخرى أو مدينة قريبة.",
    "fr": "Je n'ai trouvé aucun hôtel pour cette recherche. Essayez d'autres dates ou une ville proche.",
}


class QuoteInput(BaseModel):
    offer_ids: list[str] = Field(min_length=1, max_length=10)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=254)
    phone: str | None = Field(default=None, max_length=40)


class ConfirmInput(BaseModel):
    quote_id: str = Field(min_length=8, max_length=36)


async def _context(request: Request, session, settings, conversation_id: str, authorization: str | None):
    prop, conv = await guest_context(session, settings, conversation_id, bearer(authorization))
    if (prop.connector or {}).get("type") != "shopper":
        raise AppError("This conversation is not a travel-shopper conversation", status_code=400)
    await request.app.state.limiter.hit("booking_conv", conv.id)
    shop = ShopService(session, prop, request.app.state.shop_suppliers, settings.shop_supplier_timeout)
    return prop, conv, shop


async def _record(session, prop, conv, guest_text: str, reply: str, tool: str, args: dict, result: dict, meta: dict):
    trace = [{"id": f"form_{tool}", "name": tool, "arguments": json.dumps(args, default=str),
              "result": json.dumps(result, ensure_ascii=False, default=str)[:6000]}]
    session.add(Message(conversation_id=conv.id, property_id=prop.id, sender="guest", content=guest_text,
                        meta={"source": "form"}))
    session.add(Message(conversation_id=conv.id, property_id=prop.id, sender="ai", content=reply,
                        meta={"source": "form", "tool_trace": trace, **meta}))
    await session.commit()


@router.get("/properties/{slug}/shop/suppliers")
async def suppliers(slug: str, request: Request, session: SessionDep):
    prop = await property_by_slug(session, slug)
    if (prop.connector or {}).get("type") != "shopper":
        raise AppError("Not a travel-shopper property", status_code=404)
    return supplier_status(request.app.state.shop_suppliers)


@router.post("/conversations/{conversation_id}/shop/search")
async def search(conversation_id: str, data: StaySearch, request: Request, session: SessionDep,
                 settings: SettingsDep, authorization: Annotated[str | None, Header()] = None):
    prop, conv, shop = await _context(request, session, settings, conversation_id, authorization)
    result = await shop.search(conv.id, data)
    view = search_view(result, limit=30)
    language = conv.language or prop.languages[0]
    ok_sites = [s for s in result.suppliers if s.get("status") == "ok"]
    if result.results:
        best = result.results[0]
        reply = lang.localized(RESULTS, language).format(
            n=len(ok_sites), count=len(result.results),
            best=f"{best['best_total']} {best['offers'][0]['currency']} ({best['name']}, {best['best_source']})")
    else:
        reply = lang.localized(NONE_FOUND, language)
    guest_text = f"{data.city}, {data.country_code} · {data.check_in} → {data.check_out} · {data.adults} adult(s)" + (
        f" · children {', '.join(map(str, data.children_ages))}" if data.children_ages else "")
    top = [{k: h[k] for k in ("hotel_id", "name", "best_total", "best_source", "sites")} for h in result.results[:5]]
    await _record(session, prop, conv, guest_text, reply, "search_stays", data.model_dump(mode="json"),
                  {"hotels_found": len(result.results), "top": top}, {"shop_search_id": result.id})
    return {"reply": reply, **view}


@router.post("/conversations/{conversation_id}/shop/quote")
async def quote(conversation_id: str, data: QuoteInput, request: Request, session: SessionDep,
                settings: SettingsDep, authorization: Annotated[str | None, Header()] = None):
    prop, conv, shop = await _context(request, session, settings, conversation_id, authorization)
    guest = Guest.model_validate(data.model_dump(exclude={"offer_ids"}, exclude_none=True))
    q = await shop.quote_best_of(conv.id, data.offer_ids, guest)
    view = quote_view(q)
    reply = lang.localized(QUOTE_READY, conv.language or prop.languages[0])
    await _record(session, prop, conv, f"{guest.first_name} {guest.last_name} · {guest.email} · "
                  f"{len(data.offer_ids)} offer(s)", reply, "prepare_booking", data.model_dump(),
                  {"quote": {k: view[k] for k in ("hotel_name", "source", "total", "currency")}},
                  {"shop_quote_id": q.id})
    return {"reply": reply, "quote": view}


@router.post("/conversations/{conversation_id}/shop/confirm")
async def confirm(conversation_id: str, data: ConfirmInput, request: Request, session: SessionDep,
                  settings: SettingsDep, authorization: Annotated[str | None, Header()] = None):
    """The only path that books: an explicit guest action bound to a re-checked quote."""
    prop, conv, shop = await _context(request, session, settings, conversation_id, authorization)
    result = await shop.confirm(conv.id, data.quote_id)
    note = f"Booking {result['state']}" + (f", reference {result['reference']}" if result["reference"] else "") + (
        f". {result['detail']}" if result.get("detail") else "")
    session.add(Message(conversation_id=conv.id, property_id=prop.id, sender="system", content=note,
                        meta={"shop_booking": result}))
    await session.commit()
    return result
