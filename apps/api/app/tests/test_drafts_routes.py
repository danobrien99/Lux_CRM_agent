from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api.v1.routes import drafts as drafts_route
from app.db.pg.base import Base
from app.db.pg.models import Draft
from app.db.pg.session import SessionLocal, engine
from app.main import app

client = TestClient(app)


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_create_draft_returns_subject_and_citation_snippets(monkeypatch) -> None:
    reset_db()

    monkeypatch.setattr(
        drafts_route,
        "build_retrieval_bundle",
        lambda *_args, **_kwargs: {
            "contact": {"display_name": "Jamie", "primary_email": "jamie@example.com"},
            "recent_interactions": [],
            "relevant_chunks": [],
            "graph_claim_snippets": [],
            "objective": "follow up",
        },
    )
    monkeypatch.setattr(drafts_route, "resolve_tone_band", lambda *_args, **_kwargs: {"tone_band": "warm_professional"})
    monkeypatch.setattr(drafts_route, "compose_draft", lambda *_args, **_kwargs: "Draft body text")
    monkeypatch.setattr(drafts_route, "compose_subject", lambda *_args, **_kwargs: "Draft subject")
    monkeypatch.setattr(
        drafts_route,
        "build_citations_from_bundle",
        lambda *_args, **_kwargs: [
            {
                "paragraph": 1,
                "interaction_id": "i-1",
                "chunk_id": "c-1",
                "span_json": {"start": 0, "end": 10},
                "snippet": "Source snippet",
            }
        ],
    )

    response = client.post(
        "/v1/drafts",
        json={
            "contact_id": "contact-1",
            "objective": "follow up",
            "allow_sensitive": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["draft_subject"] == "Draft subject"
    assert payload["draft_text"] == "Draft body text"
    assert payload["objective"] == "follow up"
    assert payload["citations_json"][0]["snippet"] == "Source snippet"


def test_revise_draft_updates_subject_body_and_status() -> None:
    reset_db()
    db = SessionLocal()
    try:
        draft = Draft(
            contact_id="contact-2",
            prompt_json={"draft_subject": "Original subject"},
            draft_text="Original body",
            citations_json=[],
            tone_band="cool_professional",
            status="proposed",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        draft_id = draft.draft_id
    finally:
        db.close()

    response = client.post(
        f"/v1/drafts/{draft_id}/revise",
        json={
            "draft_subject": "Revised subject",
            "draft_body": "Revised body",
            "status": "edited",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["draft_subject"] == "Revised subject"
    assert payload["draft_text"] == "Revised body"
    assert payload["status"] == "edited"


def test_update_writing_style_requires_revised_status() -> None:
    reset_db()
    db = SessionLocal()
    try:
        draft = Draft(
            contact_id="contact-3",
            prompt_json={"draft_subject": "Subj"},
            draft_text="Body",
            citations_json=[],
            tone_band="warm_professional",
            status="proposed",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        draft_id = draft.draft_id
    finally:
        db.close()

    response = client.post(f"/v1/drafts/{draft_id}/update_writing_style", json={})
    assert response.status_code == 400


def test_update_writing_style_uses_learning_service(monkeypatch) -> None:
    reset_db()
    db = SessionLocal()
    try:
        draft = Draft(
            contact_id="contact-4",
            prompt_json={"draft_subject": "Subj"},
            draft_text="Body",
            citations_json=[],
            tone_band="friendly_personal",
            status="edited",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        draft_id = draft.draft_id
    finally:
        db.close()

    monkeypatch.setattr(
        drafts_route,
        "update_writing_style_guide_from_draft",
        lambda *_args, **_kwargs: {
            "updated": True,
            "samples_used": 3,
            "guide_path": "/tmp/writing_style.md",
        },
    )

    response = client.post(f"/v1/drafts/{draft_id}/update_writing_style", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["updated"] is True
    assert payload["samples_used"] == 3


def test_get_latest_draft_for_contact_returns_most_recent() -> None:
    reset_db()
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        older = Draft(
            contact_id="contact-9",
            prompt_json={"draft_subject": "Older", "objective": "old objective"},
            draft_text="Older body",
            citations_json=[],
            tone_band="warm_professional",
            status="edited",
            created_at=now - timedelta(minutes=5),
        )
        newer = Draft(
            contact_id="contact-9",
            prompt_json={"draft_subject": "Newer", "objective": "new objective"},
            draft_text="Newer body",
            citations_json=[],
            tone_band="friendly_personal",
            status="approved",
            created_at=now,
        )
        db.add_all([older, newer])
        db.commit()
        db.refresh(newer)
        expected_draft_id = newer.draft_id
    finally:
        db.close()

    response = client.get("/v1/drafts/latest", params={"contact_id": "contact-9"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["draft_id"] == expected_draft_id
    assert payload["draft_subject"] == "Newer"
    assert payload["objective"] == "new objective"


def test_create_draft_can_overwrite_existing(monkeypatch) -> None:
    reset_db()
    db = SessionLocal()
    try:
        draft = Draft(
            contact_id="contact-10",
            prompt_json={"draft_subject": "Orig", "objective": "orig objective"},
            draft_text="Orig body",
            citations_json=[],
            tone_band="cool_professional",
            status="edited",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        draft_id = draft.draft_id
    finally:
        db.close()

    monkeypatch.setattr(
        drafts_route,
        "build_retrieval_bundle",
        lambda *_args, **_kwargs: {
            "contact": {"display_name": "Jamie", "primary_email": "jamie@example.com"},
            "recent_interactions": [],
            "relevant_chunks": [],
            "graph_claim_snippets": [],
            "email_context_snippets": [],
            "objective": "fresh objective",
        },
    )
    monkeypatch.setattr(drafts_route, "resolve_tone_band", lambda *_args, **_kwargs: {"tone_band": "warm_professional"})
    monkeypatch.setattr(drafts_route, "compose_draft", lambda *_args, **_kwargs: "Overwritten body")
    monkeypatch.setattr(drafts_route, "compose_subject", lambda *_args, **_kwargs: "Overwritten subject")
    monkeypatch.setattr(drafts_route, "build_citations_from_bundle", lambda *_args, **_kwargs: [])

    response = client.post(
        "/v1/drafts",
        json={
            "contact_id": "contact-10",
            "objective": "fresh objective",
            "allow_sensitive": False,
            "overwrite_draft_id": draft_id,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["draft_id"] == draft_id
    assert payload["draft_subject"] == "Overwritten subject"
    assert payload["draft_text"] == "Overwritten body"
    assert payload["status"] == "proposed"


def test_objective_suggestion_returns_derived_value(monkeypatch) -> None:
    reset_db()
    monkeypatch.setattr(
        drafts_route,
        "build_retrieval_bundle",
        lambda *_args, **_kwargs: {
            "contact": {"display_name": "Jamie", "primary_email": "jamie@example.com"},
            "recent_interactions": [{"subject": "Partnership follow-up"}],
            "relevant_chunks": [],
            "email_context_snippets": ["From vector context"],
            "graph_claim_snippets": ["Current company: Acme"],
            "objective": None,
        },
    )
    monkeypatch.setattr(
        drafts_route,
        "derive_objective_from_bundle",
        lambda *_args, **_kwargs: (
            "Follow up on partnership and align next steps around Acme",
            {
                "recent_subject": "Partnership follow-up",
                "vector_context_snippet": "From vector context",
                "graph_context_snippet": "Acme",
            },
        ),
    )

    response = client.get("/v1/drafts/objective_suggestion", params={"contact_id": "contact-11"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["contact_id"] == "contact-11"
    assert payload["objective"].startswith("Follow up on partnership")


def test_create_draft_passes_and_records_policy_flags(monkeypatch) -> None:
    reset_db()
    captured: dict = {}

    def _fake_bundle(*args, **kwargs):  # noqa: ANN001
        captured["kwargs"] = kwargs
        return {
            "contact": {"display_name": "Jamie", "primary_email": "jamie@example.com"},
            "recent_interactions": [],
            "recent_interactions_global": [],
            "relevant_chunks": [],
            "email_context_snippets": [],
            "graph_claim_snippets": [],
            "graph_path_snippets": [],
            "graph_paths": [],
            "graph_metrics": {},
            "assertion_evidence_trace": [],
            "context_signals_v2": [],
            "motivator_signals": [],
            "relationship_score_hint": None,
            "hybrid_graph_query": "follow up",
            "graph_focus_terms": [],
            "opportunity_thread": None,
            "proposed_next_action": None,
            "next_action_rationale": [],
            "allow_sensitive": True,
            "allow_uncertain_context": True,
            "allow_proposed_changes_in_external_text": True,
            "policy_flags": {
                "allow_sensitive": True,
                "allow_uncertain_context": True,
                "allow_proposed_changes_in_external_text": True,
                "effective_allow_uncertain_for_external": True,
            },
            "objective": "follow up",
            "retrieval_asof": "2026-02-24T00:00:00Z",
        }

    monkeypatch.setattr(drafts_route, "build_retrieval_bundle", _fake_bundle)
    monkeypatch.setattr(drafts_route, "resolve_tone_band", lambda *_args, **_kwargs: {"tone_band": "warm_professional"})
    monkeypatch.setattr(drafts_route, "compose_draft", lambda *_args, **_kwargs: "Draft body text")
    monkeypatch.setattr(drafts_route, "compose_subject", lambda *_args, **_kwargs: "Draft subject")
    monkeypatch.setattr(drafts_route, "build_citations_from_bundle", lambda *_args, **_kwargs: [])

    response = client.post(
        "/v1/drafts",
        json={
            "contact_id": "contact-policy",
            "objective": "follow up",
            "allow_sensitive": True,
            "allow_uncertain_context": True,
            "allow_proposed_changes_in_external_text": True,
        },
    )
    assert response.status_code == 200
    assert captured["kwargs"]["allow_uncertain_context"] is True
    assert captured["kwargs"]["allow_proposed_changes_in_external_text"] is True

    db = SessionLocal()
    try:
        saved = db.query(Draft).order_by(Draft.created_at.desc()).first()
        assert saved is not None
        prompt_json = saved.prompt_json or {}
        assert prompt_json["allow_sensitive"] is True
        assert prompt_json["allow_uncertain_context"] is True
        assert prompt_json["allow_proposed_changes_in_external_text"] is True
        assert prompt_json["applied_policy_flags"]["effective_allow_uncertain_for_external"] is True
    finally:
        db.close()


def test_create_draft_passes_opportunity_id_to_retrieval_bundle_and_persists_prompt_metadata(monkeypatch) -> None:
    reset_db()
    captured: dict = {}

    def _fake_bundle(*args, **kwargs):  # noqa: ANN001
        captured["kwargs"] = kwargs
        return {
            "contact": {"display_name": "Jamie", "primary_email": "jamie@example.com"},
            "recent_interactions": [],
            "recent_interactions_global": [],
            "relevant_chunks": [],
            "email_context_snippets": [],
            "graph_claim_snippets": [],
            "graph_path_snippets": [],
            "graph_paths": [],
            "graph_metrics": {},
            "assertion_evidence_trace": [],
            "internal_assertion_evidence_trace": [],
            "context_signals_v2": [],
            "motivator_signals": [],
            "relationship_score_hint": None,
            "hybrid_graph_query": "follow up acme renewal",
            "graph_focus_terms": [],
            "opportunity_thread": {"thread_id": "thread-opp"},
            "opportunity_context": {"opportunity_id": "opp-77", "title": "Acme Renewal", "company_name": "Acme"},
            "proposed_next_action": "Confirm timeline",
            "next_action_rationale": [],
            "allow_sensitive": False,
            "allow_uncertain_context": False,
            "allow_proposed_changes_in_external_text": False,
            "policy_flags": {"allow_sensitive": False},
            "objective": "Advance renewal",
            "retrieval_asof": "2026-02-24T00:00:00Z",
        }

    monkeypatch.setattr(drafts_route, "build_retrieval_bundle", _fake_bundle)
    monkeypatch.setattr(drafts_route, "resolve_tone_band", lambda *_args, **_kwargs: {"tone_band": "warm_professional"})
    monkeypatch.setattr(drafts_route, "compose_draft", lambda *_args, **_kwargs: "Draft body")
    monkeypatch.setattr(drafts_route, "compose_subject", lambda *_args, **_kwargs: "Subject")
    monkeypatch.setattr(drafts_route, "build_citations_from_bundle", lambda *_args, **_kwargs: [])

    response = client.post(
        "/v1/drafts",
        json={
            "contact_id": "contact-opp",
            "objective": "Advance renewal",
            "opportunity_id": "opp-77",
        },
    )
    assert response.status_code == 200
    assert captured["kwargs"]["opportunity_id"] == "opp-77"

    db = SessionLocal()
    try:
        saved = db.query(Draft).order_by(Draft.created_at.desc()).first()
        assert saved is not None
        prompt_json = saved.prompt_json or {}
        assert prompt_json["opportunity_id"] == "opp-77"
    finally:
        db.close()


def test_create_draft_blocks_when_generated_text_leaks_disallowed_internal_assertion(monkeypatch) -> None:
    reset_db()
    monkeypatch.setattr(
        drafts_route,
        "build_retrieval_bundle",
        lambda *_args, **_kwargs: {
            "contact": {"display_name": "Jamie", "primary_email": "jamie@example.com"},
            "recent_interactions": [],
            "recent_interactions_global": [],
            "relevant_chunks": [],
            "email_context_snippets": [],
            "graph_claim_snippets": [],
            "graph_path_snippets": [],
            "graph_paths": [],
            "graph_metrics": {},
            "assertion_evidence_trace": [],
            "internal_assertion_evidence_trace": [
                {
                    "assertion_id": "a-proposed",
                    "claim_type": "commitment",
                    "object_name": "revised pricing concession",
                    "status": "proposed",
                    "confidence": 0.91,
                    "evidence": [],
                }
            ],
            "context_signals_v2": [],
            "motivator_signals": [],
            "relationship_score_hint": None,
            "hybrid_graph_query": "follow up",
            "graph_focus_terms": [],
            "opportunity_thread": None,
            "proposed_next_action": None,
            "next_action_rationale": [],
            "allow_sensitive": False,
            "allow_uncertain_context": False,
            "allow_proposed_changes_in_external_text": False,
            "policy_flags": {
                "allow_sensitive": False,
                "allow_uncertain_context": False,
                "allow_proposed_changes_in_external_text": False,
            },
            "objective": "follow up",
            "retrieval_asof": "2026-02-24T00:00:00Z",
        },
    )
    monkeypatch.setattr(drafts_route, "resolve_tone_band", lambda *_args, **_kwargs: {"tone_band": "warm_professional"})
    monkeypatch.setattr(
        drafts_route,
        "compose_draft",
        lambda *_args, **_kwargs: "Hi Jamie,\n\nI can offer a revised pricing concession.\n\nBest,\n[Your Name]",
    )
    monkeypatch.setattr(drafts_route, "compose_subject", lambda *_args, **_kwargs: "Draft subject")
    monkeypatch.setattr(drafts_route, "build_citations_from_bundle", lambda *_args, **_kwargs: [])

    response = client.post(
        "/v1/drafts",
        json={
            "contact_id": "contact-viol",
            "objective": "follow up",
            "allow_sensitive": False,
            "allow_uncertain_context": False,
            "allow_proposed_changes_in_external_text": False,
        },
    )

    assert response.status_code == 422
    payload = response.json()
    assert "outside the allowed evidence/policy scope" in payload["detail"]["message"]
    assert payload["detail"]["violations"][0]["type"] == "uncertain_assertion_leak"


def test_create_draft_does_not_block_on_low_signal_topic_overlap(monkeypatch) -> None:
    reset_db()
    monkeypatch.setattr(
        drafts_route,
        "build_retrieval_bundle",
        lambda *_args, **_kwargs: {
            "contact": {"display_name": "Mike", "primary_email": "mike@example.com"},
            "recent_interactions": [],
            "recent_interactions_global": [],
            "relevant_chunks": [],
            "email_context_snippets": [],
            "graph_claim_snippets": [],
            "graph_path_snippets": [],
            "graph_paths": [],
            "graph_metrics": {},
            "assertion_evidence_trace": [],
            "internal_assertion_evidence_trace": [
                {
                    "assertion_id": "a-topic",
                    "claim_type": "topic",
                    "object_name": "Mike",
                    "status": "proposed",
                    "confidence": 0.75,
                    "evidence": [],
                }
            ],
            "context_signals_v2": [],
            "motivator_signals": [],
            "relationship_score_hint": None,
            "hybrid_graph_query": "follow up",
            "graph_focus_terms": [],
            "opportunity_thread": None,
            "proposed_next_action": None,
            "next_action_rationale": [],
            "allow_sensitive": False,
            "allow_uncertain_context": False,
            "allow_proposed_changes_in_external_text": False,
            "policy_flags": {
                "allow_sensitive": False,
                "allow_uncertain_context": False,
                "allow_proposed_changes_in_external_text": False,
            },
            "objective": "follow up",
            "retrieval_asof": "2026-02-24T00:00:00Z",
        },
    )
    monkeypatch.setattr(drafts_route, "resolve_tone_band", lambda *_args, **_kwargs: {"tone_band": "warm_professional"})
    monkeypatch.setattr(
        drafts_route,
        "compose_draft",
        lambda *_args, **_kwargs: "Hi Mike,\n\nJust checking in on priorities and next steps.\n\nBest,\n[Your Name]",
    )
    monkeypatch.setattr(drafts_route, "compose_subject", lambda *_args, **_kwargs: "Checking in")
    monkeypatch.setattr(drafts_route, "build_citations_from_bundle", lambda *_args, **_kwargs: [])

    response = client.post(
        "/v1/drafts",
        json={
            "contact_id": "contact-topic",
            "objective": "follow up",
            "allow_sensitive": False,
            "allow_uncertain_context": False,
            "allow_proposed_changes_in_external_text": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["draft_subject"] == "Checking in"
