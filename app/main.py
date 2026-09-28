import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import guest, staff, voice
from app.config import Settings, get_settings
from app.db import Base, make_engine, make_sessionmaker
from app.errors import AppError, handle_app_error
from app.providers import Providers
from app.seed import seed

WEB = Path(__file__).parent / "web"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.environment != "development" and settings.session_secret == "dev-only-change-me":
        raise RuntimeError("SESSION_SECRET must be set outside development")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(settings.database_url)
        if settings.environment == "development":
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        app.state.sessionmaker = make_sessionmaker(engine)
        http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=5),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=120),
        )
        app.state.providers = Providers(settings, http)
        if settings.seed_demo:
            async with app.state.sessionmaker() as session:
                await seed(session)
        yield
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

    @app.get("/health")
    async def health():
        return {"status": "ok", "llm_configured": settings.llm_configured}

    @app.get("/", include_in_schema=False)
    async def demo():
        return FileResponse(WEB / "demo.html")

    @app.get("/inbox", include_in_schema=False)
    async def inbox():
        return FileResponse(WEB / "inbox.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app


app = create_app()
