from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.pg.base import Base
from app.db.pg.models import ResolutionTask
from app.db.pg.session import SessionLocal, engine
from app.main import app


client = TestClient(app)


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_lookup_unknown_email_reuses_open_identity_task() -> None:
    reset_db()
    first = client.get("/v1/contacts/lookup", params={"email": "unknown@example.com"})
    second = client.get("/v1/contacts/lookup", params={"email": "unknown@example.com"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["resolution_task_id"] == second.json()["resolution_task_id"]

    db = SessionLocal()
    try:
        tasks = db.scalars(
            select(ResolutionTask).where(
                ResolutionTask.task_type == "identity_resolution",
                ResolutionTask.status == "open",
            )
        ).all()
        assert len(tasks) == 1
        assert tasks[0].payload_json.get("email") == "unknown@example.com"
    finally:
        db.close()
