import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api import guest, shop, staff, voice
from app.booking.connectors.fake import use_database
from app.config import Settings, get_settings
from app.db import Base, make_engine, make_sessionmaker
from app.errors import AppError, handle_app_error
from app.providers import Providers
from app.shopper.registry import build_suppliers
from app.ratelimit import make_limiter
from app.realtime import make_broker
from app.seed import seed

WEB = Path(__file__).parent / "web"
NO_CACHE = {"Cache-Control": "no-cache"}  # browsers revalidate, so page updates show up immediately
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.session_secret.strip():
        raise RuntimeError("SESSION_SECRET is empty; set a random value in .env")
    if settings.environment != "development" and settings.session_secret == "dev-only-change-me":
        raise RuntimeError("SESSION_SECRET must be set outside development")
    if not settings.llm_configured:
        logging.getLogger("hotel_agent").warning(
            "No text model configured (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL): replies will use approved-fact "
            "fallbacks only. For the local model set LLM_BASE_URL=http://localhost:8003/v1, LLM_API_KEY=local, "
            "LLM_MODEL=qwen3.5-2b.")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        app.state.sessionmaker = make_sessionmaker(engine)
        use_database(app.state.sessionmaker)  # simulator inventory shared by all workers
        app.state.limiter = make_limiter(engine, app.state.sessionmaker, settings.rate_limits_enabled)
        # Several workers start at once: on Postgres, one at a time creates tables and seeds.
        async with engine.connect() as lock_conn:
            if engine.dialect.name == "postgresql":
                await lock_conn.execute(text("SELECT pg_advisory_lock(7424301)"))
            try:
                if settings.environment == "development":
                    async with engine.begin() as conn:
                        await conn.run_sync(Base.metadata.create_all)
                if settings.seed_demo:
                    async with app.state.sessionmaker() as session:
                        await seed(session)
            finally:
                if engine.dialect.name == "postgresql":
                    await lock_conn.execute(text("SELECT pg_advisory_unlock(7424301)"))
        http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=5),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=120),
        )
        app.state.providers = Providers(settings, http)
        app.state.shop_suppliers = build_suppliers(settings, http)
        app.state.loop = asyncio.get_running_loop()
        app.state.broker = make_broker(settings.database_url)
        await app.state.broker.start()
        yield
        await app.state.broker.stop()
        if client is None:
            await http.aclose()
        await engine.dispose()

    app = FastAPI(title="Hotel Agent", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_exception_handler(AppError, handle_app_error)
    # The widget is embedded on hotel websites; guest endpoints use bearer tokens, not cookies.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"])
    app.include_router(guest.router)
    app.include_router(voice.router)
    app.include_router(staff.router)
    app.include_router(shop.router)

    @app.get("/health")
    async def health():
        return {"status": "ok", "llm_configured": settings.llm_configured}

    @app.get("/", include_in_schema=False)
    async def demo():
        return FileResponse(WEB / "demo.html", headers=NO_CACHE)

    @app.get("/shop", include_in_schema=False)
    async def shop_page():
        return FileResponse(WEB / "shop.html", headers=NO_CACHE)

    @app.get("/inbox", include_in_schema=False)
    async def inbox():
        return FileResponse(WEB / "inbox.html", headers=NO_CACHE)

    @app.middleware("http")
    async def static_no_cache(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


app = create_app()
