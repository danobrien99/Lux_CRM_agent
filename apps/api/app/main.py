from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.routes import admin, cases, contacts, drafts, health, ingest, news, resolution, scores
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.pg.base import Base
from app.db.pg import models as _models  # noqa: F401
from app.db.pg.session import engine

configure_logging()
settings = get_settings()
app = FastAPI(title=settings.app_name)
allowed_origins = [origin.strip() for origin in settings.cors_allow_origins.split(",") if origin.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _ensure_pgvector_extension() -> None:
    if engine.url.get_backend_name() != "postgresql":
        return
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


@app.on_event("startup")
def on_startup() -> None:
    _ensure_pgvector_extension()
    Base.metadata.create_all(bind=engine)


app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(ingest.router, prefix=settings.api_prefix)
app.include_router(contacts.router, prefix=settings.api_prefix)
app.include_router(scores.router, prefix=settings.api_prefix)
app.include_router(news.router, prefix=settings.api_prefix)
app.include_router(drafts.router, prefix=settings.api_prefix)
app.include_router(resolution.router, prefix=settings.api_prefix)
app.include_router(admin.router, prefix=settings.api_prefix)
app.include_router(cases.router, prefix=settings.api_prefix)
