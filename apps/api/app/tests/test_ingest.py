from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.pg.base import Base
from app.db.pg.session import engine
from app.main import app


client = TestClient(app)


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_interaction_ingest_is_idempotent(monkeypatch) -> None:
    reset_db()
    monkeypatch.setattr("app.api.v1.routes.ingest.enqueue_job", lambda *_args, **_kwargs: "fake-job-id")
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


def test_interaction_ingest_can_requeue_duplicate_with_header(monkeypatch) -> None:
    reset_db()
    enqueued: list[tuple[str, str]] = []

    def _fake_enqueue(job_name: str, interaction_id: str) -> str:
        enqueued.append((job_name, interaction_id))
        return f"fake-{len(enqueued)}"

    monkeypatch.setattr("app.api.v1.routes.ingest.enqueue_job", _fake_enqueue)

    payload = {
        "source_system": "gmail",
        "event_type": "email_received",
        "external_id": "msg-789",
        "timestamp": "2026-02-10T10:00:00Z",
        "thread_id": "thr-789",
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

    headers: dict[str, str] = {}
    if get_settings().n8n_webhook_secret:
        headers["X-Webhook-Secret"] = get_settings().n8n_webhook_secret

    first = client.post("/v1/ingest/interaction_event", json=payload, headers=headers)
    second_headers = dict(headers)
    second_headers["X-Reprocess-Duplicates"] = "true"
    second = client.post("/v1/ingest/interaction_event", json=payload, headers=second_headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["interaction_id"] == second.json()["interaction_id"]
    assert first.json()["status"] == "enqueued"
    assert second.json()["status"] == "requeued"
    assert enqueued == [
        ("process_interaction", first.json()["interaction_id"]),
        ("process_interaction", second.json()["interaction_id"]),
    ]


def test_interaction_ingest_accepts_slack_chat_message(monkeypatch) -> None:
    reset_db()
    monkeypatch.setattr("app.api.v1.routes.ingest.enqueue_job", lambda *_args, **_kwargs: "fake-job-id")
    payload = {
        "source_system": "slack",
        "event_type": "chat_message",
        "external_id": "slack-msg-1",
        "timestamp": "2026-02-10T10:00:00Z",
        "thread_id": "slack-thread-1",
        "direction": "in",
        "subject": "Slack follow-up",
        "participants": {
            "from": [{"email": "teammate@example.com", "name": "Teammate"}],
            "to": [{"email": "owner@example.com", "name": "Owner"}],
            "cc": [],
        },
        "body_plain": "Let's discuss proposal timing in Slack.",
        "attachments": [],
    }

    headers: dict[str, str] = {}
    if get_settings().n8n_webhook_secret:
        headers["X-Webhook-Secret"] = get_settings().n8n_webhook_secret

    response = client.post("/v1/ingest/interaction_event", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "enqueued"
