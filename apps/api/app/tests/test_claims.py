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


def test_opportunity_stage_contradiction_detected() -> None:
    existing = [
        {
            "claim_id": "opp-old",
            "claim_type": "opportunity",
            "status": "accepted",
            "value_json": {"object": "Acme renewal", "stage": "discovery"},
        }
    ]
    new_claims = [
        {
            "claim_id": "opp-new",
            "claim_type": "opportunity",
            "status": "proposed",
            "value_json": {"object": "Acme renewal", "stage": "closed_won"},
        }
    ]

    contradictions = detect_contradictions(existing, new_claims)

    assert len(contradictions) == 1
    assert contradictions[0]["task_type"] == "opportunity_state_discrepancy"


def test_commitment_due_date_contradiction_detected() -> None:
    existing = [
        {
            "claim_id": "commit-old",
            "claim_type": "commitment",
            "status": "accepted",
            "value_json": {"object": "send proposal", "due_date": "2026-03-01"},
        }
    ]
    new_claims = [
        {
            "claim_id": "commit-new",
            "claim_type": "commitment",
            "status": "proposed",
            "value_json": {"object": "send proposal", "due_date": "2026-03-05"},
        }
    ]

    contradictions = detect_contradictions(existing, new_claims)

    assert len(contradictions) == 1
    assert contradictions[0]["task_type"] == "commitment_discrepancy"


def test_personal_detail_contradiction_detected() -> None:
    existing = [
        {
            "claim_id": "pd-old",
            "claim_type": "personal_detail",
            "status": "accepted",
            "value_json": {"predicate": "home_city", "object": "Boston"},
        }
    ]
    new_claims = [
        {
            "claim_id": "pd-new",
            "claim_type": "personal_detail",
            "status": "proposed",
            "value_json": {"predicate": "home_city", "object": "Austin"},
        }
    ]

    contradictions = detect_contradictions(existing, new_claims)

    assert len(contradictions) == 1
    assert contradictions[0]["task_type"] == "personal_detail_discrepancy"


def test_preference_contradiction_detected_for_same_preference_field() -> None:
    existing = [
        {
            "claim_id": "pref-old",
            "claim_type": "preference",
            "status": "accepted",
            "value_json": {"predicate": "communication_style", "object": "short email updates"},
        }
    ]
    new_claims = [
        {
            "claim_id": "pref-new",
            "claim_type": "preference",
            "status": "proposed",
            "value_json": {"predicate": "communication_style", "object": "phone calls"},
        }
    ]

    contradictions = detect_contradictions(existing, new_claims)

    assert len(contradictions) == 1
    assert contradictions[0]["task_type"] == "preference_discrepancy"
