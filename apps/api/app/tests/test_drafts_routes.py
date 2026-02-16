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
