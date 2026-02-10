from __future__ import annotations

from fastapi import FastAPI

from app.api.v1.routes import admin, contacts, drafts, health, ingest, news, resolution, scores
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.pg.base import Base
from app.db.pg import models as _models  # noqa: F401
from app.db.pg.session import engine

configure_logging()
settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(ingest.router, prefix=settings.api_prefix)
app.include_router(contacts.router, prefix=settings.api_prefix)
app.include_router(scores.router, prefix=settings.api_prefix)
app.include_router(news.router, prefix=settings.api_prefix)
app.include_router(drafts.router, prefix=settings.api_prefix)
app.include_router(resolution.router, prefix=settings.api_prefix)
app.include_router(admin.router, prefix=settings.api_prefix)
