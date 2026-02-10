from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.schemas import NewsItemIn
from app.services.news.match_contacts import match_contacts_for_news

router = APIRouter(prefix="/news", tags=["news"])


@router.post("/match")
def match_news(payload: NewsItemIn, max_results: int = 10, db: Session = Depends(get_db)) -> dict:
    matches = match_contacts_for_news(db, payload.body_plain, max_results=max_results)
    return {
        "title": payload.title,
        "url": payload.url,
        "published_at": payload.published_at,
        "matches": matches,
        "storage": "skipped",
    }
