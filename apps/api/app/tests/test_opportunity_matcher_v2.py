from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from app.db.neo4j import queries


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


def test_find_best_opportunity_for_interaction_v2_uses_recency_as_tiebreaker(monkeypatch) -> None:
    @contextmanager
    def _fake_neo4j_session():
        yield object()

    def _fake_session_run(session, query, **params):  # noqa: ANN001
        assert session is not None
        assert "MATCH (opp:" in query
        assert "LIMIT $limit" in query
        assert params["limit"] >= 1
        return _FakeResult(
            [
                {
                    "opportunity_id": "opp-old",
                    "title": "Acme Renewal",
                    "last_thread_id": "thread-1",
                    "updated_at": "2026-01-01T10:00:00Z",
                    "stage": "proposal",
                    "status": "open",
                    "company_name": "Acme",
                    "contact_ids": ["contact-1"],
                },
                {
                    "opportunity_id": "opp-new",
                    "title": "Acme Renewal",
                    "last_thread_id": "thread-1",
                    "updated_at": "2026-02-20T10:00:00Z",
                    "stage": "proposal",
                    "status": "open",
                    "company_name": "Acme",
                    "contact_ids": ["contact-1"],
                },
            ]
        )

    monkeypatch.setattr(queries, "neo4j_session", _fake_neo4j_session)
    monkeypatch.setattr(queries, "_session_run", _fake_session_run)
    monkeypatch.setattr(
        queries,
        "get_settings",
        lambda: SimpleNamespace(
            graph_v2_enabled=True,
            graph_v2_read_v2=True,
            graph_v2_case_opportunity_threshold=0.68,
        ),
    )

    best = queries.find_best_opportunity_for_interaction_v2(
        thread_id="thread-1",
        company_name="Acme",
        contact_ids=["contact-1"],
    )

    assert best is not None
    assert best["opportunity_id"] == "opp-new"
    assert best["meets_threshold"] is True
    assert any(reason.get("kind") == "recency" for reason in best.get("reason_chain", []))
    assert best["score_components"]["thread_match"] == 0.45
    assert best["score_components"]["company_match"] == 0.35


def test_find_best_opportunity_for_interaction_v2_uses_lexical_similarity_and_stage_compatibility(monkeypatch) -> None:
    @contextmanager
    def _fake_neo4j_session():
        yield object()

    def _fake_session_run(session, query, **params):  # noqa: ANN001
        return _FakeResult(
            [
                {
                    "opportunity_id": "opp-closed",
                    "title": "Acme Renewal Pricing Review",
                    "last_thread_id": None,
                    "updated_at": "2026-02-24T10:00:00Z",
                    "stage": "closed_lost",
                    "status": "open",
                    "company_name": "Acme",
                    "contact_ids": ["contact-1"],
                },
                {
                    "opportunity_id": "opp-active",
                    "title": "Acme Pricing Pilot Proposal",
                    "last_thread_id": None,
                    "updated_at": "2026-02-10T10:00:00Z",
                    "stage": "proposal",
                    "status": "open",
                    "company_name": "Acme",
                    "contact_ids": ["contact-1"],
                },
            ]
        )

    monkeypatch.setattr(queries, "neo4j_session", _fake_neo4j_session)
    monkeypatch.setattr(queries, "_session_run", _fake_session_run)
    monkeypatch.setattr(
        queries,
        "get_settings",
        lambda: SimpleNamespace(
            graph_v2_enabled=True,
            graph_v2_read_v2=True,
            graph_v2_case_opportunity_threshold=0.4,
        ),
    )

    best = queries.find_best_opportunity_for_interaction_v2(
        thread_id=None,
        company_name="Acme",
        contact_ids=["contact-1"],
        subject_hint="pricing pilot proposal follow-up",
        body_hint="Need to confirm proposal timeline for the Acme pilot",
    )

    assert best is not None
    assert best["opportunity_id"] == "opp-active"
    assert best["score_components"]["lexical_similarity"] > 0
    assert best["score_components"]["stage_compatibility"] >= 0
    assert any(reason.get("kind") == "lexical_similarity" for reason in best.get("reason_chain", []))


def test_find_best_opportunity_for_interaction_v2_uses_activity_pressure_open_loop_proxy(monkeypatch) -> None:
    @contextmanager
    def _fake_neo4j_session():
        yield object()

    def _fake_session_run(session, query, **params):  # noqa: ANN001
        assert "recent_engagement_count_30d" in query
        assert "recent_inbound_count_30d" in query
        assert "cutoff_30d" in params
        return _FakeResult(
            [
                {
                    "opportunity_id": "opp-quiet",
                    "title": "Acme Renewal",
                    "last_thread_id": None,
                    "updated_at": "2026-02-24T10:00:00Z",
                    "last_engagement_at": "2026-02-10T10:00:00Z",
                    "recent_engagement_count_30d": 1,
                    "recent_inbound_count_30d": 0,
                    "stage": "proposal",
                    "status": "open",
                    "company_name": "Acme",
                    "contact_ids": ["contact-1"],
                },
                {
                    "opportunity_id": "opp-active",
                    "title": "Acme Renewal",
                    "last_thread_id": None,
                    "updated_at": "2026-02-24T10:00:00Z",
                    "last_engagement_at": "2026-02-23T10:00:00Z",
                    "recent_engagement_count_30d": 4,
                    "recent_inbound_count_30d": 3,
                    "stage": "proposal",
                    "status": "open",
                    "company_name": "Acme",
                    "contact_ids": ["contact-1"],
                },
            ]
        )

    monkeypatch.setattr(queries, "neo4j_session", _fake_neo4j_session)
    monkeypatch.setattr(queries, "_session_run", _fake_session_run)
    monkeypatch.setattr(
        queries,
        "get_settings",
        lambda: SimpleNamespace(
            graph_v2_enabled=True,
            graph_v2_read_v2=True,
            graph_v2_case_opportunity_threshold=0.1,
        ),
    )

    best = queries.find_best_opportunity_for_interaction_v2(
        thread_id=None,
        company_name="Acme",
        contact_ids=["contact-1"],
    )

    assert best is not None
    assert best["opportunity_id"] == "opp-active"
    assert best["score_components"]["activity_pressure"] > 0
    assert any(reason.get("kind") == "activity_pressure" for reason in best.get("reason_chain", []))
