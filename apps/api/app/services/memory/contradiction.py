from __future__ import annotations

from typing import Any


def _norm_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip().lower()


def _value_json(claim: dict[str, Any]) -> dict[str, Any]:
    value = claim.get("value_json")
    return value if isinstance(value, dict) else {}


def _accepted_existing(existing_claims: list[dict[str, Any]], claim_type: str) -> list[dict[str, Any]]:
    return [c for c in existing_claims if c.get("claim_type") == claim_type and c.get("status") == "accepted"]


def _claim_target_key(claim: dict[str, Any]) -> str:
    value_json = _value_json(claim)
    for key in ("opportunity_id", "thread_id", "object", "target", "company", "label"):
        normalized = _norm_text(value_json.get(key))
        if normalized:
            return normalized
    return ""


def _append_unique(contradictions: list[dict[str, Any]], seen: set[tuple[str, str, str]], item: dict[str, Any]) -> None:
    current_claim = item.get("current_claim") or {}
    proposed_claim = item.get("proposed_claim") or {}
    key = (
        str(item.get("task_type") or ""),
        str(current_claim.get("claim_id") or ""),
        str(proposed_claim.get("claim_id") or ""),
    )
    if key in seen:
        return
    seen.add(key)
    contradictions.append(item)


def detect_contradictions(existing_claims: list[dict], new_claims: list[dict]) -> list[dict]:
    contradictions: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    existing_employment = _accepted_existing(existing_claims, "employment")
    for claim in new_claims:
        claim_type = str(claim.get("claim_type") or "")
        if claim_type != "employment":
            continue
        for current in existing_employment:
            if current.get("value_json") != claim.get("value_json"):
                _append_unique(
                    contradictions,
                    seen,
                    {
                        "task_type": "employment_discrepancy",
                        "current_claim": current,
                        "proposed_claim": claim,
                    },
                )

    existing_opportunities = _accepted_existing(existing_claims, "opportunity")
    for claim in new_claims:
        if claim.get("claim_type") != "opportunity":
            continue
        value_json = _value_json(claim)
        proposed_stage = _norm_text(value_json.get("stage") or value_json.get("status"))
        if not proposed_stage:
            continue
        proposed_target = _claim_target_key(claim)
        for current in existing_opportunities:
            current_value = _value_json(current)
            current_stage = _norm_text(current_value.get("stage") or current_value.get("status"))
            if not current_stage:
                continue
            current_target = _claim_target_key(current)
            if proposed_target and current_target and proposed_target != current_target:
                continue
            if current_stage != proposed_stage:
                _append_unique(
                    contradictions,
                    seen,
                    {
                        "task_type": "opportunity_state_discrepancy",
                        "current_claim": current,
                        "proposed_claim": claim,
                    },
                )

    existing_commitments = _accepted_existing(existing_claims, "commitment")
    for claim in new_claims:
        if claim.get("claim_type") != "commitment":
            continue
        value_json = _value_json(claim)
        proposed_target = _claim_target_key(claim)
        proposed_due = _norm_text(value_json.get("due_date") or value_json.get("due"))
        proposed_owner = _norm_text(value_json.get("owner") or value_json.get("assignee"))
        if not (proposed_target or proposed_due or proposed_owner):
            continue
        for current in existing_commitments:
            current_value = _value_json(current)
            current_target = _claim_target_key(current)
            if proposed_target and current_target and proposed_target != current_target:
                continue
            current_due = _norm_text(current_value.get("due_date") or current_value.get("due"))
            current_owner = _norm_text(current_value.get("owner") or current_value.get("assignee"))
            if (proposed_due and current_due and proposed_due != current_due) or (
                proposed_owner and current_owner and proposed_owner != current_owner
            ):
                _append_unique(
                    contradictions,
                    seen,
                    {
                        "task_type": "commitment_discrepancy",
                        "current_claim": current,
                        "proposed_claim": claim,
                    },
                )

    existing_personal = _accepted_existing(existing_claims, "personal_detail")
    for claim in new_claims:
        if claim.get("claim_type") != "personal_detail":
            continue
        value_json = _value_json(claim)
        proposed_field = _norm_text(value_json.get("predicate") or value_json.get("field") or value_json.get("attribute"))
        proposed_value = _norm_text(value_json.get("object") or value_json.get("label") or value_json.get("value"))
        if not (proposed_field and proposed_value):
            continue
        for current in existing_personal:
            current_value_json = _value_json(current)
            current_field = _norm_text(
                current_value_json.get("predicate") or current_value_json.get("field") or current_value_json.get("attribute")
            )
            current_value = _norm_text(
                current_value_json.get("object") or current_value_json.get("label") or current_value_json.get("value")
            )
            if current_field and current_field == proposed_field and current_value and current_value != proposed_value:
                _append_unique(
                    contradictions,
                    seen,
                    {
                        "task_type": "personal_detail_discrepancy",
                        "current_claim": current,
                        "proposed_claim": claim,
                    },
                )
    return contradictions
