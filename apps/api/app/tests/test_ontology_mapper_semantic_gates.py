from __future__ import annotations

from app.services.ontology.mapper import map_relation_to_claim, relation_payload_from_claim


def test_supported_predicate_is_marked_ontology_supported() -> None:
    claim = map_relation_to_claim(
        {
            "subject": "contact",
            "predicate": "works_at",
            "object": "TNFD",
            "claim_type": "employment",
            "confidence": 0.92,
        },
        source_system="cognee",
    )
    assert claim is not None
    assert claim["ontology_supported"] is True
    assert claim["ontology_predicate"] == "hs:worksAt"
    assert claim["promotion_scope"] == "crm_case_evidence"
    assert relation_payload_from_claim(claim) is not None


def test_unsupported_custom_predicate_is_quarantined_from_relation_projection() -> None:
    claim = map_relation_to_claim(
        {
            "subject": "contact",
            "predicate": "has_opportunity",
            "object": "Q2 pilot rollout",
            "claim_type": "opportunity",
            "confidence": 0.8,
        },
        source_system="cognee",
    )
    assert claim is not None
    assert claim["ontology_supported"] is False
    assert claim["ontology_predicate"] == "hs:hasOpportunity"
    assert claim["promotion_scope"] == "case_evidence_only"
    # Unsupported predicates stay in Evidence/Case but should not project into CRM/legacy relation triples.
    assert relation_payload_from_claim(claim) is None

