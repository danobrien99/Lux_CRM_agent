from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.pg.base import Base
from app.db.pg.models import ContactCache, Interaction
from app.db.pg.session import SessionLocal, engine
from app.main import app


client = TestClient(app)


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_backfill_contact_status_reports_processed_contacts() -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id="contact-processed",
                primary_email="processed@example.com",
                display_name="Processed Person",
            )
        )
        db.add(
            ContactCache(
                contact_id="contact-new",
                primary_email="new@example.com",
                display_name="New Person",
            )
        )
        db.add(
            Interaction(
                source_system="gmail",
                external_id="ext-processed",
                type="email",
                timestamp=datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc),
                direction="in",
                subject="Processed thread",
                thread_id="thr-1",
                participants_json={"from": [], "to": [], "cc": []},
                contact_ids_json=["contact-processed"],
                status="processed",
            )
        )
        db.add(
            Interaction(
                source_system="gmail",
                external_id="ext-new",
                type="email",
                timestamp=datetime(2026, 2, 15, 10, 10, tzinfo=timezone.utc),
                direction="in",
                subject="New thread",
                thread_id="thr-2",
                participants_json={"from": [], "to": [], "cc": []},
                contact_ids_json=["contact-new"],
                status="new",
            )
        )
        db.commit()
    finally:
        db.close()

    headers: dict[str, str] = {}
    if get_settings().n8n_webhook_secret:
        headers["X-Webhook-Secret"] = get_settings().n8n_webhook_secret

    response = client.get("/v1/admin/backfill_contact_status", headers=headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_contact_count"] == 2
    assert payload["processed_contact_count"] == 1
    assert payload["processed_contact_ids"] == ["contact-processed"]
    assert payload["processed_primary_emails"] == ["processed@example.com"]
