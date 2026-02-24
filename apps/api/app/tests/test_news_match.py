from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from app.db.pg.base import Base
from app.db.pg.models import ContactCache
from app.db.pg.session import SessionLocal, engine
from app.services.news import match_contacts as news_match
from app.services.news.match_contacts import match_contacts_for_news


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_news_match_returns_ranked_contacts() -> None:
    reset_db()
    db = SessionLocal()
    try:
        db.add(
            ContactCache(
                contact_id="c-1",
                primary_email="alex@example.com",
                display_name="Alex Energy",
                owner_user_id="owner-1",
            )
        )
        db.commit()
        matches = match_contacts_for_news(db, "Energy market expansion and macro policy", max_results=5)
        assert len(matches) == 1
        assert matches[0]["contact_id"] == "c-1"
    finally:
        db.close()


def test_graph_candidates_use_v2_assertions_when_graph_v2_read_enabled(monkeypatch) -> None:
    captured: dict = {"queries": []}

    class _FakeRun:
        def __init__(self, rows):
            self._rows = rows

        def data(self):
            return self._rows

    class _FakeSession:
        def run(self, query, **params):  # noqa: ANN001
            captured["queries"].append(query)
            captured["params"] = params
            if "KGAssertion" in query:
                return _FakeRun(
                    [
                        {
                            "contact_id": "c-1",
                            "display_name": "Alex Energy",
                            "matched_keywords": ["energy"],
                            "graph_hits": 2,
                            "company_names": [],
                            "evidence_refs": [
                                {
                                    "assertion_id": "a-1",
                                    "claim_type": "topic",
                                    "predicate": "mentionsTopic",
                                    "object_name": "energy",
                                    "status": "accepted",
                                    "confidence": 0.92,
                                    "interaction_id": "int-1",
                                    "chunk_id": "chunk-1",
                                    "updated_at": "2026-02-20T10:00:00Z",
                                }
                            ],
                            "latest_seen_at": "2026-02-20T10:00:00Z",
                        }
                    ]
                )
            if "WORKS_AT" in query:
                return _FakeRun(
                    [
                        {
                            "contact_id": "c-1",
                            "display_name": "Alex Energy",
                            "matched_keywords": ["energy"],
                            "company_names": ["EnergyCo"],
                            "company_hits": 1,
                            "latest_seen_at": "2026-02-21T10:00:00Z",
                        }
                    ]
                )
            return _FakeRun([])

    @contextmanager
    def _fake_neo4j_session():
        yield _FakeSession()

    monkeypatch.setattr(
        news_match,
        "get_settings",
        lambda: SimpleNamespace(graph_v2_enabled=True, graph_v2_read_v2=True),
    )
    monkeypatch.setattr(news_match, "neo4j_session", _fake_neo4j_session)

    candidates = news_match._graph_candidates(["energy"], limit=5)

    assert any("KGAssertion" in query for query in captured["queries"])
    assert any("WORKS_AT" in query for query in captured["queries"])
    assert captured["params"]["keywords"] == ["energy"]
    assert candidates["c-1"]["graph_hits"] == 3
    assert candidates["c-1"]["evidence_refs"][0]["assertion_id"] == "a-1"
    assert "EnergyCo" in candidates["c-1"]["company_names"]
    assert any(ref.get("kind") == "company_association" for ref in candidates["c-1"]["evidence_refs"])


def test_weighted_graph_signal_downweights_topic_spam_and_rewards_company_association() -> None:
    graph_meta = {
        "graph_hits": 12,
        "evidence_refs": [
            {"assertion_id": "a-topic-1", "claim_type": "topic"},
            {"assertion_id": "a-topic-2", "claim_type": "topic"},
            {"assertion_id": "a-topic-3", "claim_type": "topic"},
            {"assertion_id": "a-topic-4", "claim_type": "topic"},
            {"assertion_id": "a-opp-1", "claim_type": "opportunity"},
            {"kind": "company_association", "company_name": "Acme"},
        ],
    }

    weighted = news_match._weighted_graph_signal(graph_meta)

    assert 0.0 < weighted < 1.0
    assert weighted < 0.9  # topic-heavy evidence should not saturate graph score
