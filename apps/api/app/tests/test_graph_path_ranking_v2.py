from __future__ import annotations

from contextlib import contextmanager

from app.db.neo4j import queries
from app.db.neo4j.queries import _contact_graph_path_sort_key


def test_contact_graph_path_sort_key_prefers_newer_paths_when_other_factors_equal() -> None:
    older = {
        "path_text": "older",
        "uncertain_hops": 0,
        "opportunity_hits": 1,
        "latest_seen_at": "2026-01-01T12:00:00Z",
        "avg_confidence": 0.9,
        "hops": 1,
    }
    newer = {
        "path_text": "newer",
        "uncertain_hops": 0,
        "opportunity_hits": 1,
        "latest_seen_at": "2026-02-01T12:00:00Z",
        "avg_confidence": 0.9,
        "hops": 1,
    }

    ranked = sorted([older, newer], key=_contact_graph_path_sort_key)

    assert ranked[0]["path_text"] == "newer"
    assert ranked[1]["path_text"] == "older"


def test_contact_graph_path_sort_key_puts_null_timestamps_after_timed_rows() -> None:
    timed = {
        "path_text": "timed",
        "uncertain_hops": 0,
        "opportunity_hits": 0,
        "latest_seen_at": "2026-02-10T10:00:00Z",
        "avg_confidence": 0.4,
        "hops": 2,
    }
    untimed = {
        "path_text": "untimed",
        "uncertain_hops": 0,
        "opportunity_hits": 0,
        "latest_seen_at": None,
        "avg_confidence": 0.95,
        "hops": 1,
    }

    ranked = sorted([untimed, timed], key=_contact_graph_path_sort_key)

    assert ranked[0]["path_text"] == "timed"
    assert ranked[1]["path_text"] == "untimed"


def test_contact_graph_path_sort_key_prefers_lower_noise_penalty_when_other_factors_equal() -> None:
    noisy = {
        "path_text": "contact -[related_to]-> update",
        "uncertain_hops": 0,
        "opportunity_hits": 0,
        "latest_seen_at": "2026-02-10T10:00:00Z",
        "avg_confidence": 0.9,
        "hops": 1,
        "noise_penalty": 0.22,
    }
    useful = {
        "path_text": "contact -[commitment]-> send proposal",
        "uncertain_hops": 0,
        "opportunity_hits": 0,
        "latest_seen_at": "2026-02-10T10:00:00Z",
        "avg_confidence": 0.9,
        "hops": 1,
        "noise_penalty": 0.02,
    }

    ranked = sorted([noisy, useful], key=_contact_graph_path_sort_key)

    assert ranked[0]["path_text"] == useful["path_text"]
    assert ranked[1]["path_text"] == noisy["path_text"]


def test_contact_graph_paths_v2_applies_lookback_days_filter(monkeypatch) -> None:
    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def data(self):
            return self._rows

    @contextmanager
    def _fake_neo4j_session():
        yield object()

    def _fake_session_run(session, query, **params):  # noqa: ANN001
        if "coworker_name" in query:
            return _FakeResult([])
        if "claim_type AS claim_type" in query:
            return _FakeResult([])
        if "CaseOpportunity" in query or "case_id AS case_id" in query:
            return _FakeResult([])
        if "opportunity_id" in query and "CRMOpportunity" in query:
            return _FakeResult([])
        return _FakeResult(
            [
                {"contact_name": "Alice", "company_name": "OldCo", "updated_at": "2025-08-01T00:00:00Z"},
                {"contact_name": "Alice", "company_name": "NewCo", "updated_at": "2026-02-20T00:00:00Z"},
            ]
        )

    monkeypatch.setattr(queries, "neo4j_session", _fake_neo4j_session)
    monkeypatch.setattr(queries, "_session_run", _fake_session_run)

    rows = queries._contact_graph_paths_v2("contact-1", lookback_days=30, limit=8)

    path_texts = [row["path_text"] for row in rows]
    assert any("NewCo" in text for text in path_texts)
    assert not any("OldCo" in text for text in path_texts)


def test_contact_graph_paths_v2_filters_low_signal_relationship_signal_noise(monkeypatch) -> None:
    class _FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def data(self):
            return self._rows

    @contextmanager
    def _fake_neo4j_session():
        yield object()

    def _fake_session_run(session, query, **params):  # noqa: ANN001
        if "coworker_name" in query:
            return _FakeResult([])
        if "claim_type AS claim_type" in query:
            return _FakeResult(
                [
                    {
                        "contact_name": "Alice",
                        "assertion_id": "a-noise",
                        "predicate": "related_to",
                        "object_name": "update",
                        "claim_type": "relationship_signal",
                        "status": "accepted",
                        "confidence": 0.92,
                        "updated_at": "2026-02-20T00:00:00Z",
                        "interaction_ids": ["int-1"],
                    },
                    {
                        "contact_name": "Alice",
                        "assertion_id": "a-commit",
                        "predicate": "committed_to",
                        "object_name": "send revised proposal",
                        "claim_type": "commitment",
                        "status": "accepted",
                        "confidence": 0.92,
                        "updated_at": "2026-02-20T00:00:00Z",
                        "interaction_ids": ["int-2"],
                    },
                ]
            )
        if "CaseOpportunity" in query or "case_id AS case_id" in query:
            return _FakeResult([])
        if "opportunity_id" in query and "CRMOpportunity" in query:
            return _FakeResult([])
        return _FakeResult([])

    monkeypatch.setattr(queries, "neo4j_session", _fake_neo4j_session)
    monkeypatch.setattr(queries, "_session_run", _fake_session_run)

    rows = queries._contact_graph_paths_v2("contact-1", lookback_days=30, limit=8, include_uncertain=False)
    path_texts = [row["path_text"] for row in rows]

    assert any("send revised proposal" in text for text in path_texts)
    assert not any("->[update]" in text for text in path_texts)
    assert not any("related_to" in text and "update" in text for text in path_texts)
