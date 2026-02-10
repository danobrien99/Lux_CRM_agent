from __future__ import annotations

from fastapi.testclient import TestClient

from app.db.pg.base import Base
from app.db.pg.session import engine
from app.main import app


client = TestClient(app)


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_interaction_ingest_is_idempotent() -> None:
    reset_db()
    payload = {
        "source_system": "gmail",
        "event_type": "email_received",
        "external_id": "msg-123",
        "timestamp": "2026-02-10T10:00:00Z",
        "thread_id": "thr-123",
        "direction": "in",
        "subject": "Hello",
        "participants": {
            "from": [{"email": "a@example.com", "name": "A"}],
            "to": [{"email": "b@example.com", "name": "B"}],
            "cc": [],
        },
        "body_plain": "Quick update on timeline",
        "attachments": [],
    }

    first = client.post("/v1/ingest/interaction_event", json=payload)
    second = client.post("/v1/ingest/interaction_event", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["interaction_id"] == second.json()["interaction_id"]
    assert second.json()["status"] in {"duplicate", "enqueued"}
