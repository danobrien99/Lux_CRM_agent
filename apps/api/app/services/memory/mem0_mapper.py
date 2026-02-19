from __future__ import annotations


def build_mem0_bundle(
    *,
    interaction_summary: str,
    recent_claims: list[dict],
    cognee_candidates: list[dict] | None = None,
    candidate_claims: list[dict] | None = None,
    auto_accept_threshold: float,
    scope_ids: dict | None = None,
) -> dict:
    normalized_candidates = candidate_claims if candidate_claims is not None else (cognee_candidates or [])
    return {
        "new_interaction_summary": interaction_summary,
        "recent_claims": recent_claims,
        # `candidate_claims` is the canonical key. `cognee_candidates` remains for backward compatibility.
        "candidate_claims": normalized_candidates,
        "cognee_candidates": normalized_candidates,
        "auto_accept_threshold": auto_accept_threshold,
        "scope_ids": scope_ids or {},
    }
