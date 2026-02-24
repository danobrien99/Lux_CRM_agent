from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.v1.routes import scores
from app.api.v1.schemas import InteractionSummary
from app.db.pg.base import Base
from app.db.pg.models import Chunk, ContactCache, Interaction
from app.db.pg.session import SessionLocal, engine


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_contact_score_detail_includes_profile_interaction_summary_and_components(monkeypatch) -> None:
    reset_db()
    db = SessionLocal()
    try:
        monkeypatch.setenv("OPENAI_API_KEY", "")

        db.add(
            ContactCache(
                contact_id="contact-1",
                primary_email="jane@example.com",
                display_name="Jane Doe",
                owner_user_id="owner-42",
            )
        )

        now = datetime.now(timezone.utc)
        db.add_all(
            [
                Interaction(
                    source_system="gmail",
                    external_id="evt-1",
                    type="email_received",
                    timestamp=now - timedelta(days=1),
                    direction="in",
                    subject="Project kickoff",
                    thread_id="thread-1",
                    participants_json={"from": [], "to": [], "cc": []},
                    contact_ids_json=["contact-1"],
                    status="new",
                ),
                Interaction(
                    source_system="gmail",
                    external_id="evt-2",
                    type="email_sent",
                    timestamp=now - timedelta(days=4),
                    direction="out",
                    subject="Follow up",
                    thread_id="thread-1",
                    participants_json={"from": [], "to": [], "cc": []},
                    contact_ids_json=["contact-1"],
                    status="new",
                ),
            ]
        )
        db.commit()

        monkeypatch.setattr(
            scores,
            "get_contact_claims",
            lambda *_args, **_kwargs: [
                {
                    "claim_type": "employment",
                    "status": "accepted",
                    "value_json": {"company": "Acme Corp"},
                }
            ],
        )
        monkeypatch.setattr(
            scores,
            "get_contact_score_snapshots",
            lambda **_kwargs: [
                {
                    "asof": "2026-02-15",
                    "relationship_score": 44.5,
                    "priority_score": 27.4,
                    "components_json": {
                        "relationship": {
                            "days_since_last": 1,
                            "recency": 44.75,
                            "frequency": 8.0,
                            "warmth": 0.0,
                            "depth": 1.0,
                            "interaction_count_30d": 2,
                            "interaction_count_90d": 2,
                            "warmth_depth_source": {"source": "heuristic"},
                        },
                        "priority": {
                            "relationship_component": 17.8,
                            "inactivity": 0.0,
                            "open_loops": 5.0,
                            "triggers": 0.0,
                            "open_loop_count": 1,
                            "trigger_score": 0.0,
                        },
                    },
                }
            ],
        )

        payload = scores.contact_score_detail("contact-1", db)

        assert payload.profile is not None
        assert payload.profile.display_name == "Jane Doe"
        assert payload.profile.primary_email == "jane@example.com"
        assert payload.profile.company == "Acme Corp"

        assert payload.interaction_summary is not None
        assert payload.interaction_summary.total_interactions == 2
        assert payload.interaction_summary.inbound_count == 1
        assert payload.interaction_summary.outbound_count == 1
        assert payload.interaction_summary.last_subject == "Project kickoff"
        assert payload.interaction_summary.recent_topics
        assert payload.interaction_summary.priority_next_step
        assert not payload.interaction_summary.priority_next_step.startswith("Stub:")
        assert payload.interaction_summary.next_step is not None
        assert payload.interaction_summary.next_step.source in {"heuristic", "opportunity", "case_opportunity", "llm"}

        assert payload.current is not None
        assert payload.score_components is not None
        assert "interaction_count_30d" in payload.score_components.relationship
        assert payload.score_components.relationship.get("warmth_depth_source_label") == "heuristic"
        assert "open_loop_count" in payload.score_components.priority
        assert payload.current.priority_score == 27.4
        assert payload.trend[0].asof == "2026-02-15"
    finally:
        db.close()


def test_today_scores_zero_when_contact_has_no_interactions(monkeypatch) -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id="contact-no-data",
                primary_email="nodata@example.com",
                display_name="No Data",
            )
        )
        db.commit()
        monkeypatch.setattr(scores, "get_contact_company_hints", lambda _ids: {})
        monkeypatch.setattr(scores, "get_latest_score_snapshots", lambda _ids: {})

        payload = scores.today_scores(limit=10, db=db)
        item = next((row for row in payload.items if row.contact_id == "contact-no-data"), None)
        assert item is not None
        assert item.relationship_score == 0
        assert item.priority_score == 0
        assert item.why_now == "No stored score snapshot yet. Ingest interactions to generate scores."
    finally:
        db.close()


def test_today_scores_uses_latest_snapshot_values(monkeypatch) -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add_all(
            [
                ContactCache(contact_id="contact-a", primary_email="a@example.com", display_name="A"),
                ContactCache(contact_id="contact-b", primary_email="b@example.com", display_name="B"),
            ]
        )
        db.commit()

        monkeypatch.setattr(scores, "get_contact_company_hints", lambda _ids: {"contact-a": "Acme"})
        monkeypatch.setattr(
            scores,
            "get_latest_score_snapshots",
            lambda _ids: {
                "contact-a": {
                    "asof": "2026-02-15",
                    "relationship_score": 30.0,
                    "priority_score": 25.0,
                    "components_json": {"relationship": {"days_since_last": 8}, "priority": {"open_loop_count": 0}},
                },
                "contact-b": {
                    "asof": "2026-02-15",
                    "relationship_score": 60.0,
                    "priority_score": 45.0,
                    "components_json": {"relationship": {"days_since_last": 20}, "priority": {"open_loop_count": 1}},
                },
            },
        )

        payload = scores.today_scores(limit=10, db=db)
        assert [row.contact_id for row in payload.items] == ["contact-b", "contact-a"]
        assert payload.items[0].priority_score == 45.0
        assert payload.items[1].company == "Acme"
    finally:
        db.close()


def test_build_score_reason_includes_component_specific_evidence_refs() -> None:
    reason = scores._build_score_reason(
        "2026-02-24T10:00:00Z",
        {"days_since_last": 18},
        {"open_loop_count": 2, "open_loops": 10.0, "trigger_score": 5.0, "triggers": 5.0},
        {"metrics": {"recent_relation_count": 3, "path_count_2hop": 7}},
    )

    kinds = {item.get("kind") for item in reason.evidence_refs if isinstance(item, dict)}
    assert {"recency_driver", "open_loop_driver", "trigger_driver", "graph_boost_driver"} <= kinds
    graph_ref = next(item for item in reason.evidence_refs if item.get("kind") == "graph_boost_driver")
    assert graph_ref["metrics"]["recent_relation_count"] == 3
    assert graph_ref["observed_at"] == "2026-02-24T10:00:00Z"


def test_contact_score_detail_prefers_llm_summary_when_available(monkeypatch) -> None:
    reset_db()
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        db.add(
            ContactCache(
                contact_id="contact-llm",
                primary_email="llm@example.com",
                display_name="LLM Contact",
            )
        )
        interaction = Interaction(
            source_system="gmail",
            external_id="evt-llm-1",
            type="email_received",
            timestamp=now - timedelta(days=2),
            direction="in",
            subject="Subject should not drive summary",
            thread_id="thread-llm",
            participants_json={"from": [], "to": [], "cc": []},
            contact_ids_json=["contact-llm"],
            status="new",
        )
        db.add(interaction)
        db.flush()
        db.add(
            Chunk(
                interaction_id=interaction.interaction_id,
                chunk_type="email_body",
                text="Let's confirm proposal details and timeline for the pilot kickoff.",
                span_json={"start": 0, "end": 75},
            )
        )
        db.commit()

        monkeypatch.setattr(scores, "get_contact_claims", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(scores, "get_contact_company_hint", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(scores, "get_contact_score_snapshots", lambda **_kwargs: [])
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        monkeypatch.setattr(
            scores,
            "_summarize_recent_interactions_with_openai",
            lambda **_kwargs: (
                "LLM summary from message excerpts only.",
                ["Proposal details", "Pilot timeline"],
                "Verify open opportunities in HubSpot and send a proposal follow-up.",
            ),
        )

        payload = scores.contact_score_detail("contact-llm", db)
        assert payload.interaction_summary is not None
        assert payload.interaction_summary.brief == "LLM summary from message excerpts only."
        assert payload.interaction_summary.recent_topics == ["Proposal details", "Pilot timeline"]
        assert payload.interaction_summary.priority_next_step == "Verify open opportunities in HubSpot and send a proposal follow-up."
        assert payload.interaction_summary.summary_source == "llm"
        assert payload.interaction_summary.priority_next_step_source == "llm"
    finally:
        db.close()


def test_contact_score_detail_uses_cached_interaction_summary(monkeypatch) -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.store.get(key)

        def setex(self, key: str, _ttl: int, value: str) -> None:
            self.store[key] = value

        def delete(self, key: str) -> None:
            self.store.pop(key, None)

    reset_db()
    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id="contact-cache-hit",
                primary_email="cache@example.com",
                display_name="Cache Hit",
            )
        )
        db.commit()

        fake_redis = FakeRedis()
        monkeypatch.setattr(scores, "_summary_cache_client", lambda: fake_redis)
        monkeypatch.setattr(scores, "get_contact_claims", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(scores, "get_contact_company_hint", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(scores, "get_contact_score_snapshots", lambda **_kwargs: [])
        monkeypatch.setattr(
            scores,
            "_build_interaction_summary",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("interaction summary should come from cache")),
        )

        cached_summary = InteractionSummary(
            total_interactions=3,
            interaction_count_30d=2,
            interaction_count_90d=3,
            inbound_count=2,
            outbound_count=1,
            last_interaction_at=None,
            last_subject=None,
            recent_subjects=[],
            recent_topics=["Pilot timeline"],
            priority_next_step="Follow up on pilot timeline opportunity.",
            next_step=None,
            summary_source="llm",
            priority_next_step_source="llm",
            brief="Cached summary payload.",
        )
        scores._write_cached_interaction_summary("contact-cache-hit", cached_summary)

        payload = scores.contact_score_detail("contact-cache-hit", db)
        assert payload.interaction_summary is not None
        assert payload.interaction_summary.brief == "Cached summary payload."
        assert payload.interaction_summary.recent_topics == ["Pilot timeline"]
    finally:
        db.close()


def test_refresh_cached_interaction_summary_rebuilds_and_stores(monkeypatch) -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.store.get(key)

        def setex(self, key: str, _ttl: int, value: str) -> None:
            self.store[key] = value

        def delete(self, key: str) -> None:
            self.store.pop(key, None)

    reset_db()
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        db.add(
            ContactCache(
                contact_id="contact-cache-refresh",
                primary_email="refresh@example.com",
                display_name="Cache Refresh",
            )
        )
        interaction = Interaction(
            source_system="gmail",
            external_id="evt-cache-refresh",
            type="email_received",
            timestamp=now - timedelta(days=1),
            direction="in",
            subject="Subject line not used for summary body",
            thread_id="thread-cache-refresh",
            participants_json={"from": [], "to": [], "cc": []},
            contact_ids_json=["contact-cache-refresh"],
            status="new",
        )
        db.add(interaction)
        db.flush()
        db.add(
            Chunk(
                interaction_id=interaction.interaction_id,
                chunk_type="email_body",
                text="Let's review the proposal milestones and finalize next week's workshop meeting.",
                span_json={"start": 0, "end": 82},
            )
        )
        db.commit()

        fake_redis = FakeRedis()
        monkeypatch.setattr(scores, "_summary_cache_client", lambda: fake_redis)
        monkeypatch.setenv("OPENAI_API_KEY", "")
        monkeypatch.setattr(scores, "get_contact_claims", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(scores, "get_contact_company_hint", lambda *_args, **_kwargs: None)

        rebuilt = scores.refresh_cached_interaction_summary(db, "contact-cache-refresh")
        cached = scores.get_cached_interaction_summary("contact-cache-refresh")
        assert cached is not None
        assert cached.brief == rebuilt.brief
        assert cached.priority_next_step is not None
        assert not cached.priority_next_step.startswith("Stub:")
    finally:
        db.close()


def test_refresh_contact_interaction_summary_endpoint_returns_refreshed_summary(monkeypatch) -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        def get(self, key: str) -> str | None:
            return self.store.get(key)

        def setex(self, key: str, _ttl: int, value: str) -> None:
            self.store[key] = value

        def delete(self, key: str) -> None:
            self.store.pop(key, None)

    reset_db()
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        db.add(
            ContactCache(
                contact_id="contact-refresh-endpoint",
                primary_email="refresh-endpoint@example.com",
                display_name="Refresh Endpoint",
            )
        )
        interaction = Interaction(
            source_system="gmail",
            external_id="evt-refresh-endpoint",
            type="email_received",
            timestamp=now - timedelta(days=1),
            direction="in",
            subject="Subject for refresh endpoint",
            thread_id="thread-refresh-endpoint",
            participants_json={"from": [], "to": [], "cc": []},
            contact_ids_json=["contact-refresh-endpoint"],
            status="new",
        )
        db.add(interaction)
        db.flush()
        db.add(
            Chunk(
                interaction_id=interaction.interaction_id,
                chunk_type="email_body",
                text="Please share final pricing and timeline before next Wednesday.",
                span_json={"start": 0, "end": 62},
            )
        )
        db.commit()

        fake_redis = FakeRedis()
        monkeypatch.setattr(scores, "_summary_cache_client", lambda: fake_redis)
        monkeypatch.setattr(scores, "get_contact_claims", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(scores, "get_contact_company_hint", lambda *_args, **_kwargs: None)
        monkeypatch.setenv("OPENAI_API_KEY", "")

        response = scores.refresh_contact_interaction_summary("contact-refresh-endpoint", db)
        assert response.contact_id == "contact-refresh-endpoint"
        assert response.refreshed is True
        assert response.interaction_summary.total_interactions == 1

        cached = scores.get_cached_interaction_summary("contact-refresh-endpoint")
        assert cached is not None
        assert cached.brief == response.interaction_summary.brief
    finally:
        db.close()


def test_ranked_opportunities_returns_real_case_and_promoted_items(monkeypatch) -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add_all(
            [
                ContactCache(contact_id="contact-a", primary_email="a@example.com", display_name="Alice"),
                ContactCache(contact_id="contact-b", primary_email="b@example.com", display_name="Bob"),
            ]
        )
        db.commit()

        monkeypatch.setattr(
            scores,
            "list_open_opportunities_v2",
            lambda limit=100: [
                {
                    "opportunity_id": "opp-1",
                    "title": "Acme Renewal",
                    "company_name": "Acme",
                    "status": "open",
                    "entity_status": "canonical",
                    "thread_id": "thread-1",
                    "contact_ids": ["contact-a"],
                    "last_engagement_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        )
        monkeypatch.setattr(
            scores,
            "list_case_opportunities_v2",
            lambda status="open", limit=100: [
                {
                    "case_id": "case_opp:1",
                    "title": "Beta Pilot",
                    "company_name": "Beta Co",
                    "status": status,
                    "entity_status": "provisional",
                    "thread_id": "thread-2",
                    "interaction_id": "int-1",
                    "contact_ids": ["contact-b"],
                    "motivators": ["proposal", "timeline"],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        )
        monkeypatch.setattr(
            scores,
            "get_latest_score_snapshots",
            lambda _ids: {
                "contact-a": {"priority_score": 82.0},
                "contact-b": {"priority_score": 60.0},
            },
        )

        payload = scores.ranked_opportunities(limit=10, db=db)
        assert len(payload.items) == 2
        assert payload.items[0].kind == "opportunity"
        assert payload.items[0].opportunity_id == "opp-1"
        assert payload.items[0].next_step is not None
        assert payload.items[0].linked_contacts[0].display_name == "Alice"
        assert any(item.kind == "case_opportunity" for item in payload.items)
    finally:
        db.close()
