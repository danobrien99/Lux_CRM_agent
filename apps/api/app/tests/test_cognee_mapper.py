from __future__ import annotations

from app.services.extraction.cognee_mapper import candidates_to_claims


def test_candidates_to_claims_extracts_entity_context_claims() -> None:
    candidates = {
        "relations": [],
        "entities": [
            {"type": "Preference", "name": "short email updates", "confidence": 0.88},
            {"type": "Opportunity", "name": "Q2 pilot rollout", "confidence": 0.82},
            {"type": "Personal_Detail", "name": "runner", "confidence": 0.63},
        ],
        "topics": [],
    }

    claims = candidates_to_claims(candidates)
    claim_types = {claim.get("claim_type") for claim in claims}
    objects = {
        (claim.get("value_json") or {}).get("object")
        for claim in claims
        if isinstance(claim.get("value_json"), dict)
    }

    assert "preference" in claim_types
    assert "opportunity" in claim_types
    assert "personal_detail" in claim_types
    assert "short email updates" in objects
    assert "Q2 pilot rollout" in objects
