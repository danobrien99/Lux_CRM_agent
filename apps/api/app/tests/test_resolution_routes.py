from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.v1.routes import resolution as resolution_route
from app.main import app


client = TestClient(app)


def test_resolve_task_accept_action(monkeypatch) -> None:
    calls: list[dict] = []

    class _Task:
        task_id = "task-1"
        status = "resolved"

    def _fake_resolve(db, task_id, action, edited_value_json, audit_update):  # noqa: ANN001
        calls.append(
            {
                "task_id": task_id,
                "action": action,
                "edited_value_json": edited_value_json,
                "audit_update": audit_update,
            }
        )
        return _Task()

    monkeypatch.setattr(resolution_route, "resolve_resolution_task", _fake_resolve)

    response = client.post(
        "/v1/resolution/tasks/task-1/resolve",
        json={"action": "accept_proposed"},
    )

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-1", "status": "resolved"}
    assert calls and calls[0]["task_id"] == "task-1"
    assert calls[0]["action"] == "accept_proposed"
    assert calls[0]["edited_value_json"] is None
    assert calls[0]["audit_update"]["action"] == "accept_proposed"
    assert calls[0]["audit_update"]["resolved_at"]


def test_resolve_task_edit_and_accept_action(monkeypatch) -> None:
    class _Task:
        task_id = "task-2"
        status = "resolved"

    received: dict = {}

    def _fake_resolve(db, task_id, action, edited_value_json, audit_update):  # noqa: ANN001
        received.update(
            {
                "task_id": task_id,
                "action": action,
                "edited_value_json": edited_value_json,
                "audit_update": audit_update,
            }
        )
        return _Task()

    monkeypatch.setattr(resolution_route, "resolve_resolution_task", _fake_resolve)

    response = client.post(
        "/v1/resolution/tasks/task-2/resolve",
        json={"action": "edit_and_accept", "edited_value_json": {"company": "Acme"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    assert received["action"] == "edit_and_accept"
    assert received["edited_value_json"] == {"company": "Acme"}


def test_resolve_task_reject_action(monkeypatch) -> None:
    class _Task:
        task_id = "task-3"
        status = "dismissed"

    monkeypatch.setattr(
        resolution_route,
        "resolve_resolution_task",
        lambda *args, **kwargs: _Task(),
    )

    response = client.post(
        "/v1/resolution/tasks/task-3/resolve",
        json={"action": "reject_proposed"},
    )

    assert response.status_code == 200
    assert response.json() == {"task_id": "task-3", "status": "dismissed"}


def test_resolve_task_returns_404_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(resolution_route, "resolve_resolution_task", lambda *args, **kwargs: None)

    response = client.post(
        "/v1/resolution/tasks/missing/resolve",
        json={"action": "reject_proposed"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found"
