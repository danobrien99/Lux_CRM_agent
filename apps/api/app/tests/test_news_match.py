from __future__ import annotations

from app.db.pg.base import Base
from app.db.pg.models import ContactCache
from app.db.pg.session import SessionLocal, engine
from app.services.news.match_contacts import match_contacts_for_news


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_news_match_returns_ranked_contacts() -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id="c-1",
                primary_email="alex@example.com",
                display_name="Alex Energy",
                owner_user_id="owner-1",
            )
        )
        db.commit()
        matches = match_contacts_for_news(db, "Energy market expansion and macro policy", max_results=5)
        assert len(matches) == 1
        assert matches[0]["contact_id"] == "c-1"
    finally:
        db.close()
