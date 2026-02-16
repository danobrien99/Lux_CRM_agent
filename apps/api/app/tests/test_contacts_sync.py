from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.pg.base import Base
from app.db.pg.models import ContactCache, Draft, ResolutionTask
from app.db.pg.session import SessionLocal, engine
from app.main import app


client = TestClient(app)


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_contacts_sync_push_handles_duplicate_primary_email(monkeypatch) -> None:
    reset_db()
    monkeypatch.setattr("app.services.contacts_registry.sync.merge_contact", lambda *_args, **_kwargs: None)

    payload = {
        "mode": "push",
        "rows": [
            {
                "contact_id": "contact-001",
                "primary_email": "duplicate@example.com",
                "display_name": "First Name",
            },
            {
                "contact_id": "contact-002",
                "primary_email": "duplicate@example.com",
                "display_name": "Second Name",
            },
        ],
    }

    headers: dict[str, str] = {}
    if get_settings().n8n_webhook_secret:
        headers["X-Webhook-Secret"] = get_settings().n8n_webhook_secret

    response = client.post("/v1/contacts/sync", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["mode"] == "push"
    assert response.json()["upserted"] == 2

    db = SessionLocal()
    try:
        contacts = db.scalars(select(ContactCache).where(ContactCache.primary_email == "duplicate@example.com")).all()
        assert len(contacts) == 1
        assert contacts[0].contact_id == "contact-001"
        assert contacts[0].display_name == "Second Name"
    finally:
        db.close()


def test_contacts_sync_push_derives_display_name_and_forwards_company(monkeypatch) -> None:
    reset_db()
    merged_payloads: list[dict] = []
    monkeypatch.setattr("app.services.contacts_registry.sync.merge_contact", lambda payload: merged_payloads.append(payload))

    payload = {
        "mode": "push",
        "rows": [
            {
                "contact_id": "contact-123",
                "primary_email": "person@example.com",
                "first_name": "Jamie",
                "last_name": "Nguyen",
                "company": "Acme Corp",
            }
        ],
    }

    headers: dict[str, str] = {}
    if get_settings().n8n_webhook_secret:
        headers["X-Webhook-Secret"] = get_settings().n8n_webhook_secret

    response = client.post("/v1/contacts/sync", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["mode"] == "push"
    assert response.json()["upserted"] == 1

    db = SessionLocal()
    try:
        contact = db.scalar(select(ContactCache).where(ContactCache.contact_id == "contact-123"))
        assert contact is not None
        assert contact.display_name == "Jamie Nguyen"
    finally:
        db.close()

    assert len(merged_payloads) == 1
    assert merged_payloads[0]["company"] == "Acme Corp"
    assert merged_payloads[0]["display_name"] == "Jamie Nguyen"


def test_delete_contact_removes_pg_records_and_calls_graph_cleanup(monkeypatch) -> None:
    reset_db()
    deleted_contact_ids: list[str] = []
    monkeypatch.setattr("app.api.v1.routes.contacts.delete_contact_graph", lambda contact_id: deleted_contact_ids.append(contact_id))

    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id="contact-delete-1",
                primary_email="delete-me@example.com",
                display_name="Delete Me",
            )
        )
        db.add(
            Draft(
                contact_id="contact-delete-1",
                prompt_json={"objective": "test"},
                draft_text="hello",
                citations_json=[],
                tone_band="neutral",
                status="proposed",
            )
        )
        db.add(
            ResolutionTask(
                contact_id="contact-delete-1",
                task_type="employment_discrepancy",
                proposed_claim_id="proposed-1",
                current_claim_id=None,
                payload_json={"key": "value"},
                status="open",
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.delete("/v1/contacts/contact-delete-1")
    assert response.status_code == 200
    assert response.json()["deleted"] is True

    db = SessionLocal()
    try:
        assert db.scalar(select(ContactCache).where(ContactCache.contact_id == "contact-delete-1")) is None
        assert db.scalar(select(Draft).where(Draft.contact_id == "contact-delete-1")) is None
        assert db.scalar(select(ResolutionTask).where(ResolutionTask.contact_id == "contact-delete-1")) is None
    finally:
        db.close()

    assert deleted_contact_ids == ["contact-delete-1"]
