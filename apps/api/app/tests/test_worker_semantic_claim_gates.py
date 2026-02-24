from __future__ import annotations

from app.workers.jobs import _extract_company_hint_from_claims, _is_low_signal_text


def test_extract_company_hint_ignores_unsupported_claims() -> None:
    claims = [
        {
            "claim_type": "opportunity",
            "ontology_supported": False,
            "value_json": {"predicate": "has_opportunity", "object": "Q2 pilot rollout", "company": "Noise Co"},
        },
        {
            "claim_type": "employment",
            "ontology_supported": True,
            "value_json": {"predicate": "works_at", "object": "TNFD", "company": "TNFD"},
        },
    ]
    assert _extract_company_hint_from_claims(claims) == "TNFD"


def test_low_signal_filter_allows_non_trivial_acronym_like_org_names() -> None:
    assert _is_low_signal_text("TNFD") is False
    assert _is_low_signal_text("PwC") is False
