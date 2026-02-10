from __future__ import annotations


def detect_contradictions(existing_claims: list[dict], new_claims: list[dict]) -> list[dict]:
    contradictions: list[dict] = []
    existing_employment = [c for c in existing_claims if c.get("claim_type") == "employment" and c.get("status") == "accepted"]
    for claim in new_claims:
        if claim.get("claim_type") != "employment":
            continue
        for current in existing_employment:
            if current.get("value_json") != claim.get("value_json"):
                contradictions.append(
                    {
                        "task_type": "employment_discrepancy",
                        "current_claim": current,
                        "proposed_claim": claim,
                    }
                )
    return contradictions
