from __future__ import annotations

from types import SimpleNamespace

from app.workers.jobs import (
    _claim_evidence_refs_for_chunks,
    _contradiction_task_payload,
    _filter_graph_claims,
    _persist_claim_assertions_v2,
    _relationship_signal_bonus_from_context_signals,
    _split_claim_pipelines,
)


def test_filter_graph_claims_keeps_topic_context_claims() -> None:
    claims = [
        {
            "claim_id": "topic-1",
            "claim_type": "topic",
            "value_json": {"label": "pricing", "object": "pricing"},
        },
        {
            "claim_id": "empty-1",
            "claim_type": "topic",
            "value_json": {"label": ""},
        },
    ]

    filtered = _filter_graph_claims(claims)

    assert [claim["claim_id"] for claim in filtered] == ["topic-1"]


def test_filter_graph_claims_drops_low_signal_topic_noise_but_keeps_high_confidence_topic() -> None:
    claims = [
        {
            "claim_id": "topic-noise",
            "claim_type": "topic",
            "confidence": 0.4,
            "value_json": {"label": "meeting", "object": "meeting"},
        },
        {
            "claim_id": "topic-valuable",
            "claim_type": "topic",
            "confidence": 0.7,
            "value_json": {"label": "energy procurement", "object": "energy procurement"},
        },
        {
            "claim_id": "topic-high-confidence-generic",
            "claim_type": "topic",
            "confidence": 0.92,
            "value_json": {"label": "update", "object": "update"},
        },
    ]

    filtered = _filter_graph_claims(claims)

    assert [claim["claim_id"] for claim in filtered] == ["topic-valuable", "topic-high-confidence-generic"]


def test_filter_graph_claims_drops_low_confidence_proposed_relationship_signal_noise() -> None:
    claims = [
        {
            "claim_id": "rel-noise",
            "claim_type": "relationship_signal",
            "status": "proposed",
            "confidence": 0.4,
            "value_json": {"label": "great", "object": "great"},
        },
        {
            "claim_id": "rel-keep",
            "claim_type": "relationship_signal",
            "status": "accepted",
            "confidence": 0.8,
            "value_json": {"label": "strong responsiveness", "object": "strong responsiveness"},
        },
    ]

    filtered = _filter_graph_claims(claims)

    assert [claim["claim_id"] for claim in filtered] == ["rel-keep"]


def test_claim_evidence_refs_are_claim_specific_using_spans() -> None:
    chunks = [
        SimpleNamespace(
            chunk_id="chunk-a",
            text="Pricing proposal and budget planning for Q2 pilot",
            span_json={"start": 0, "end": 60},
        ),
        SimpleNamespace(
            chunk_id="chunk-b",
            text="Timeline confirmation and workshop scheduling details",
            span_json={"start": 61, "end": 130},
        ),
    ]
    pricing_claim = {
        "claim_type": "opportunity",
        "value_json": {
            "object": "Q2 pilot pricing proposal",
            "evidence_spans": [{"start": 5, "end": 40}],
        },
    }
    timeline_claim = {
        "claim_type": "commitment",
        "value_json": {
            "object": "confirm workshop timeline",
            "evidence_spans": [{"start": 80, "end": 110}],
        },
    }

    pricing_refs = _claim_evidence_refs_for_chunks(
        pricing_claim,
        interaction_id="int-1",
        chunks=chunks,
        default_limit=2,
    )
    timeline_refs = _claim_evidence_refs_for_chunks(
        timeline_claim,
        interaction_id="int-1",
        chunks=chunks,
        default_limit=2,
    )

    assert [ref["chunk_id"] for ref in pricing_refs] == ["chunk-a"]
    assert [ref["chunk_id"] for ref in timeline_refs] == ["chunk-b"]


def test_split_claim_pipelines_separates_graph_context_from_crm_promotable() -> None:
    claims = [
        {
            "claim_id": "topic-ctx",
            "claim_type": "topic",
            "ontology_supported": False,
            "value_json": {"label": "pricing", "object": "pricing"},
        },
        {
            "claim_id": "opp-1",
            "claim_type": "opportunity",
            "ontology_supported": True,
            "value_json": {"object": "Q2 pilot rollout"},
        },
    ]

    graph_context_claims, crm_promotable_claims = _split_claim_pipelines(claims)

    assert [claim["claim_id"] for claim in graph_context_claims] == ["topic-ctx", "opp-1"]
    assert [claim["claim_id"] for claim in crm_promotable_claims] == ["opp-1"]


def test_contradiction_task_payload_includes_summary_and_evidence_refs() -> None:
    issue = {
        "task_type": "commitment_discrepancy",
        "current_claim": {
            "claim_id": "c-1",
            "claim_type": "commitment",
            "value_json": {"object": "send proposal"},
            "evidence_refs": [{"interaction_id": "i-1", "chunk_id": "chunk-1", "span_json": {"start": 0, "end": 10}}],
        },
        "proposed_claim": {
            "claim_id": "c-2",
            "claim_type": "commitment",
            "value_json": {"object": "send revised proposal"},
            "evidence_refs": [{"interaction_id": "i-2", "chunk_id": "chunk-2", "span_json": {"start": 10, "end": 20}}],
        },
    }

    payload = _contradiction_task_payload(issue, interaction_id="int-77")

    assert payload["interaction_id"] == "int-77"
    assert payload["summary"].startswith("commitment_discrepancy:")
    assert payload["evidence_refs"]["current"][0]["chunk_id"] == "chunk-1"
    assert payload["evidence_refs"]["proposed"][0]["chunk_id"] == "chunk-2"


def test_relationship_signal_bonus_counts_only_high_confidence_accepted_signals() -> None:
    count, bonus = _relationship_signal_bonus_from_context_signals(
        [
            {"claim_type": "relationship_signal", "status": "accepted", "confidence": 0.95},
            {"claim_type": "relationship_signal", "status": "verified", "confidence": 0.81},
            {"claim_type": "relationship_signal", "status": "proposed", "confidence": 0.99},
            {"claim_type": "relationship_signal", "status": "accepted", "confidence": 0.5},
            {"claim_type": "topic", "status": "accepted", "confidence": 1.0},
        ]
    )

    assert count == 2
    assert bonus == 1.2


def test_persist_claim_assertions_v2_uses_claim_specific_evidence_for_topic_and_relationship_signal(monkeypatch) -> None:
    calls: list[dict] = []

    monkeypatch.setattr(
        "app.workers.jobs.create_assertion_with_evidence_v2",
        lambda **kwargs: calls.append(kwargs),
    )

    fallback_refs = [{"interaction_id": "int-1", "chunk_id": "fallback", "span_json": {"start": 0, "end": 5}}]
    claims = [
        {
            "claim_id": "topic-1",
            "claim_type": "topic",
            "evidence_refs": [{"interaction_id": "int-1", "chunk_id": "chunk-topic", "span_json": {"start": 1, "end": 9}}],
        },
        {
            "claim_id": "rel-1",
            "claim_type": "relationship_signal",
            "evidence_refs": [{"interaction_id": "int-1", "chunk_id": "chunk-rel", "span_json": {"start": 10, "end": 20}}],
        },
    ]

    created = _persist_claim_assertions_v2(
        interaction_id="int-1",
        contact_id="contact-1",
        claims=claims,
        fallback_evidence_refs=fallback_refs,
        source_system="gmail",
        extractor="cognee",
        stage="cognee_claims",
    )

    assert created == 2
    assert [call["claim"]["claim_type"] for call in calls] == ["topic", "relationship_signal"]
    assert calls[0]["evidence_refs"][0]["chunk_id"] == "chunk-topic"
    assert calls[1]["evidence_refs"][0]["chunk_id"] == "chunk-rel"
