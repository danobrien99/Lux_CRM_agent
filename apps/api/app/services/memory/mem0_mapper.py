from __future__ import annotations


def build_mem0_bundle(
    *,
    interaction_summary: str,
    recent_claims: list[dict],
    cognee_candidates: list[dict],
    auto_accept_threshold: float,
    scope_ids: dict | None = None,
) -> dict:
    return {
        "new_interaction_summary": interaction_summary,
        "recent_claims": recent_claims,
        "cognee_candidates": cognee_candidates,
        "auto_accept_threshold": auto_accept_threshold,
        "scope_ids": scope_ids or {},
    }
