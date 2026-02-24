from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.v1.routes import cases as cases_route
from app.db.pg.base import Base
from app.db.pg.models import ContactCache
from app.db.pg.session import SessionLocal, engine
from app.main import app


client = TestClient(app)


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_list_case_opportunities_route_shape(monkeypatch) -> None:
    monkeypatch.setattr(
        cases_route,
        "list_case_opportunities_v2",
        lambda status="open": [
            {
                "case_id": "case_opp:1",
                "title": "Test Opportunity",
                "company_name": "Acme",
                "thread_id": "thread-1",
                "status": status,
                "entity_status": "provisional",
                "interaction_id": "int-1",
                "promotion_reason": "auto",
                "gate_results": {},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ],
    )

    response = client.get("/v1/cases/opportunities", params={"status": "open"})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["case_id"] == "case_opp:1"


def test_promote_case_contact_creates_contact_cache(monkeypatch) -> None:
    reset_db()
    monkeypatch.setattr(
        cases_route,
        "promote_case_contact_v2",
        lambda *_args, **_kwargs: {
            "case_id": "case_contact:1",
            "status": "promoted",
            "entity_status": "canonical",
            "promoted_id": "contact:auto:123",
            "email": "newperson@example.com",
            "display_name": "New Person",
        },
    )

    response = client.post(
        "/v1/cases/contacts/case_contact:1/promote",
        json={"promotion_reason": "approved", "gate_results": {"manual": True}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["promoted_id"] == "contact:auto:123"

    db = SessionLocal()
    try:
        row = db.scalar(select(ContactCache).where(ContactCache.contact_id == "contact:auto:123"))
        assert row is not None
        assert row.primary_email == "newperson@example.com"
    finally:
        db.close()


def test_promote_case_contact_blocked_does_not_create_contact_cache(monkeypatch) -> None:
    reset_db()
    monkeypatch.setattr(
        cases_route,
        "promote_case_contact_v2",
        lambda *_args, **_kwargs: {
            "case_id": "case_contact:blocked",
            "status": "open",
            "entity_status": "provisional",
            "promoted_id": None,
            "email": "blocked@example.com",
            "display_name": "Blocked Contact",
        },
    )

    response = client.post(
        "/v1/cases/contacts/case_contact:blocked/promote",
        json={"promotion_reason": "approved", "gate_results": {"manual": True}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "open"
    assert payload["entity_status"] == "provisional"
    assert payload["promoted_id"] is None

    db = SessionLocal()
    try:
        row = db.scalar(select(ContactCache).where(ContactCache.primary_email == "blocked@example.com"))
        assert row is None
    finally:
        db.close()
