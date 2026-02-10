from __future__ import annotations

from app.services.memory.contradiction import detect_contradictions


def test_employment_contradiction_detected() -> None:
    existing = [
        {
            "claim_id": "old-1",
            "claim_type": "employment",
            "status": "accepted",
            "value_json": {"company": "Acme"},
        }
    ]
    new_claims = [
        {
            "claim_id": "new-1",
            "claim_type": "employment",
            "status": "proposed",
            "value_json": {"company": "Globex"},
        }
    ]
    contradictions = detect_contradictions(existing, new_claims)
    assert len(contradictions) == 1
    assert contradictions[0]["task_type"] == "employment_discrepancy"
