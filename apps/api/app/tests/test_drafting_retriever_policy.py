from __future__ import annotations

from types import SimpleNamespace

from app.db.pg.base import Base
from app.db.pg.models import ContactCache
from app.db.pg.session import SessionLocal, engine
from app.services.drafting import retriever


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _seed_contact(contact_id: str = "contact-1") -> None:
    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id=contact_id,
                primary_email="jamie@example.com",
                display_name="Jamie",
                owner_user_id="owner-1",
            )
        )
        db.commit()
    finally:
        db.close()


def _patch_retrieval_dependencies(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(
        retriever,
        "get_settings",
        lambda: SimpleNamespace(graph_v2_enabled=True, graph_v2_read_v2=True),
    )
    monkeypatch.setattr(retriever, "search_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "get_contact_graph_paths", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "get_contact_graph_metrics", lambda *args, **kwargs: {})
    monkeypatch.setattr(retriever, "get_latest_score_snapshots", lambda *args, **kwargs: {})
    monkeypatch.setattr(retriever, "get_latest_next_step_suggestions_v2", lambda *args, **kwargs: [])


def test_build_retrieval_bundle_excludes_sensitive_and_uncertain_context_by_default(monkeypatch) -> None:
    reset_db()
    _seed_contact("contact-1")
    _patch_retrieval_dependencies(monkeypatch)

    monkeypatch.setattr(
        retriever,
        "get_contact_context_signals_v2",
        lambda *args, **kwargs: [
            {
                "assertion_id": "a-topic",
                "claim_type": "topic",
                "predicate": "discussed_topic",
                "object_name": "pricing",
                "confidence": 0.95,
                "status": "accepted",
                "sensitive": False,
            },
            {
                "assertion_id": "a-personal",
                "claim_type": "personal_detail",
                "predicate": "has_interest",
                "object_name": "marathon training",
                "confidence": 0.92,
                "status": "accepted",
                "sensitive": True,
            },
            {
                "assertion_id": "a-proposed",
                "claim_type": "commitment",
                "predicate": "committed_to",
                "object_name": "send revised contract",
                "confidence": 0.9,
                "status": "proposed",
                "sensitive": False,
            },
        ],
    )
    monkeypatch.setattr(
        retriever,
        "get_contact_assertion_evidence_trace_v2",
        lambda *args, **kwargs: [
            {"assertion_id": "a-topic", "claim_type": "topic", "status": "accepted", "confidence": 0.95, "evidence": []},
            {
                "assertion_id": "a-personal",
                "claim_type": "personal_detail",
                "status": "accepted",
                "confidence": 0.92,
                "evidence": [],
            },
            {"assertion_id": "a-proposed", "claim_type": "commitment", "status": "proposed", "confidence": 0.9, "evidence": []},
        ],
    )

    db = SessionLocal()
    try:
        bundle = retriever.build_retrieval_bundle(
            db,
            "contact-1",
            objective="follow up on pricing",
            allow_sensitive=False,
        )
    finally:
        db.close()

    assert bundle["policy_flags"]["allow_sensitive"] is False
    assert bundle["policy_flags"]["allow_uncertain_context"] is False
    assert bundle["policy_flags"]["allow_proposed_changes_in_external_text"] is False
    assert "pricing" in bundle["motivator_signals"]
    assert "marathon training" not in bundle["motivator_signals"]
    assert "send revised contract" not in bundle["motivator_signals"]
    assert all(item["assertion_id"] == "a-topic" for item in bundle["assertion_evidence_trace"])
    internal_ids = {item["assertion_id"] for item in bundle["internal_assertion_evidence_trace"]}
    assert {"a-personal", "a-proposed"} <= internal_ids


def test_build_retrieval_bundle_can_include_uncertain_and_sensitive_when_enabled(monkeypatch) -> None:
    reset_db()
    _seed_contact("contact-2")
    _patch_retrieval_dependencies(monkeypatch)

    monkeypatch.setattr(
        retriever,
        "get_contact_context_signals_v2",
        lambda *args, **kwargs: [
            {
                "assertion_id": "a-personal",
                "claim_type": "personal_detail",
                "predicate": "has_interest",
                "object_name": "sailing",
                "confidence": 0.93,
                "status": "accepted",
                "sensitive": True,
            },
            {
                "assertion_id": "a-proposed",
                "claim_type": "commitment",
                "predicate": "committed_to",
                "object_name": "review proposal next week",
                "confidence": 0.88,
                "status": "proposed",
                "sensitive": False,
            },
        ],
    )
    monkeypatch.setattr(
        retriever,
        "get_contact_assertion_evidence_trace_v2",
        lambda *args, **kwargs: [
            {
                "assertion_id": "a-personal",
                "claim_type": "personal_detail",
                "status": "accepted",
                "confidence": 0.93,
                "evidence": [],
            },
            {
                "assertion_id": "a-proposed",
                "claim_type": "commitment",
                "status": "proposed",
                "confidence": 0.88,
                "evidence": [],
            },
        ],
    )

    db = SessionLocal()
    try:
        bundle = retriever.build_retrieval_bundle(
            db,
            "contact-2",
            objective="follow up",
            allow_sensitive=True,
            allow_uncertain_context=False,
            allow_proposed_changes_in_external_text=True,
        )
    finally:
        db.close()

    assert bundle["policy_flags"]["allow_sensitive"] is True
    assert bundle["policy_flags"]["allow_proposed_changes_in_external_text"] is True
    assert bundle["policy_flags"]["effective_allow_uncertain_for_external"] is True
    assert "sailing" in bundle["motivator_signals"]
    assert "review proposal next week" in bundle["motivator_signals"]
    trace_ids = {item["assertion_id"] for item in bundle["assertion_evidence_trace"]}
    assert {"a-personal", "a-proposed"} <= trace_ids


def test_build_retrieval_bundle_includes_opportunity_context_when_opportunity_id_provided(monkeypatch) -> None:
    reset_db()
    _seed_contact("contact-3")
    _patch_retrieval_dependencies(monkeypatch)

    monkeypatch.setattr(retriever, "get_contact_context_signals_v2", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "get_contact_assertion_evidence_trace_v2", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        retriever,
        "list_open_opportunities_v2",
        lambda *args, **kwargs: [
            {
                "opportunity_id": "opp-123",
                "title": "Acme Renewal",
                "company_name": "Acme",
                "thread_id": "thread-123",
                "last_engagement_at": "2026-02-20T10:00:00Z",
                "updated_at": "2026-02-20T10:00:00Z",
            }
        ],
    )

    db = SessionLocal()
    try:
        bundle = retriever.build_retrieval_bundle(
            db,
            "contact-3",
            objective=None,
            allow_sensitive=False,
            opportunity_id="opp-123",
        )
    finally:
        db.close()

    assert bundle["opportunity_context"]["opportunity_id"] == "opp-123"
    assert bundle["opportunity_context"]["thread_id"] == "thread-123"
    assert "Acme Renewal" in bundle["hybrid_graph_query"] or "Acme" in bundle["hybrid_graph_query"]


def test_build_retrieval_bundle_includes_topic_terms_and_persisted_opportunity_next_steps(monkeypatch) -> None:
    reset_db()
    _seed_contact("contact-4")
    _patch_retrieval_dependencies(monkeypatch)

    monkeypatch.setattr(
        retriever,
        "get_contact_context_signals_v2",
        lambda *args, **kwargs: [
            {
                "assertion_id": "a-topic-1",
                "claim_type": "topic",
                "object_name": "pricing expansion",
                "confidence": 0.94,
                "status": "accepted",
                "sensitive": False,
            },
            {
                "assertion_id": "a-topic-2",
                "claim_type": "topic",
                "object_name": "energy procurement",
                "confidence": 0.9,
                "status": "accepted",
                "sensitive": False,
            },
        ],
    )
    monkeypatch.setattr(retriever, "get_contact_assertion_evidence_trace_v2", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        retriever,
        "list_open_opportunities_v2",
        lambda *args, **kwargs: [
            {
                "opportunity_id": "opp-456",
                "title": "Energy Procurement Pilot",
                "company_name": "GridCo",
                "thread_id": "thread-456",
                "last_engagement_at": "2026-02-22T10:00:00Z",
                "updated_at": "2026-02-23T10:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(
        retriever,
        "get_latest_next_step_suggestions_v2",
        lambda *args, **kwargs: [
            {
                "summary": "Send pricing options with two dates for pilot review.",
                "confidence": 0.88,
                "source": "opportunity",
                "opportunity_id": "opp-456",
                "due_at": "2026-02-28T00:00:00Z",
                "evidence_refs": [{"kind": "opportunity", "opportunity_id": "opp-456"}],
            }
        ],
    )

    db = SessionLocal()
    try:
        bundle = retriever.build_retrieval_bundle(
            db,
            "contact-4",
            objective="follow up on the energy procurement pilot",
            allow_sensitive=False,
            opportunity_id="opp-456",
        )
    finally:
        db.close()

    assert "pricing" in bundle["topic_context_terms"]
    assert "energy" in bundle["topic_context_terms"]
    assert bundle["opportunity_next_step_context"][0]["opportunity_id"] == "opp-456"
    assert bundle["next_action_rationale"][0]["kind"] == "persisted_next_step"
    assert "pricing" in bundle["hybrid_graph_query"] or "energy" in bundle["hybrid_graph_query"]


def test_rank_graph_paths_and_focus_terms_downweight_noisy_paths() -> None:
    objective_terms = {"proposal", "pricing"}
    noisy = {
        "path_text": "Jamie -[related_to]-> update",
        "avg_confidence": 0.99,
        "opportunity_hits": 0,
        "uncertain_hops": 0,
        "recency_days": 0,
        "noise_penalty": 0.24,
    }
    useful = {
        "path_text": "Jamie -[opportunity]-> Pricing Pilot Proposal (Acme)",
        "avg_confidence": 0.85,
        "opportunity_hits": 1,
        "uncertain_hops": 0,
        "recency_days": 4,
        "noise_penalty": 0.0,
    }

    ranked = retriever._rank_graph_paths([noisy, useful], objective_terms)
    focus_terms = retriever._extract_graph_focus_terms(ranked, max_terms=6)

    assert ranked[0]["path_text"] == useful["path_text"]
    assert "pricing" in focus_terms or "proposal" in focus_terms
    assert "update" not in focus_terms
