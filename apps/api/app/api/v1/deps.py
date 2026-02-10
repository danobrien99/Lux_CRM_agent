from __future__ import annotations

from collections.abc import Generator

from app.core.config import Settings, get_settings
from app.db.pg.session import SessionLocal


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_settings_dep() -> Settings:
    return get_settings()
