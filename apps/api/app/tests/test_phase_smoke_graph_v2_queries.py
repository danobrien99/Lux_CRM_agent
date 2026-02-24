from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.db.neo4j import queries


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


@pytest.mark.phase_smoke
def test_get_contact_context_signals_v2_with_mocked_neo4j(monkeypatch) -> None:
    captured: dict = {}

    @contextmanager
    def _fake_neo4j_session():
        yield object()

    def _fake_session_run(session, query, **params):  # noqa: ANN001
        captured["session"] = session
        captured["query"] = query
        captured["params"] = params
        return _FakeResult(
            [
                {
                    "assertion_id": "a-1",
                    "claim_type": "topic",
                    "predicate": "mentionsTopic",
                    "object_name": "pricing",
                    "confidence": 0.93,
                    "status": "accepted",
                }
            ]
        )

    monkeypatch.setattr(queries, "neo4j_session", _fake_neo4j_session)
    monkeypatch.setattr(queries, "_session_run", _fake_session_run)

    rows = queries.get_contact_context_signals_v2("contact-123", limit=5)

    assert len(rows) == 1
    assert rows[0]["assertion_id"] == "a-1"
    assert rows[0]["claim_type"] == "topic"
    assert rows[0]["status"] == "accepted"
    assert rows[0]["confidence"] == pytest.approx(0.93)
    assert "external_id: $contact_id" in captured["query"]
    assert "MATCH (a:" in captured["query"]
    assert "LIMIT $limit" in captured["query"]
    assert captured["params"]["contact_id"] == "contact-123"
    assert captured["params"]["limit"] == 5
