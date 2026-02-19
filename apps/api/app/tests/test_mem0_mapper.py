from __future__ import annotations

from app.services.memory.mem0_mapper import build_mem0_bundle


def test_build_mem0_bundle_uses_candidate_claims_key() -> None:
    claims = [{"claim_id": "claim-1"}]
    bundle = build_mem0_bundle(
        interaction_summary="Met to discuss a workshop proposal.",
        recent_claims=[],
        candidate_claims=claims,
        auto_accept_threshold=0.9,
        scope_ids={"contact_id": "contact-1"},
    )
    assert bundle["candidate_claims"] == claims
    # Backward compatibility for adapters still reading the old key.
    assert bundle["cognee_candidates"] == claims
