from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.neo4j.queries import get_claim_by_id, set_current_employer, update_claim_status
from app.db.pg.models import ResolutionTask


def create_resolution_task(
    db: Session,
    *,
    contact_id: str,
    task_type: str,
    proposed_claim_id: str,
    current_claim_id: str | None,
    payload_json: dict,
) -> ResolutionTask:
    task = ResolutionTask(
        contact_id=contact_id,
        task_type=task_type,
        proposed_claim_id=proposed_claim_id,
        current_claim_id=current_claim_id,
        payload_json=payload_json,
        status="open",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def create_identity_resolution_task(db: Session, *, email: str, payload_json: dict | None = None) -> ResolutionTask:
    normalized_email = email.strip().lower()
    if not normalized_email:
        raise ValueError("email is required for identity resolution tasks")

    open_tasks = db.scalars(
        select(ResolutionTask).where(
            ResolutionTask.task_type == "identity_resolution",
            ResolutionTask.status == "open",
        )
    ).all()
    for task in open_tasks:
        payload_email = str((task.payload_json or {}).get("email", "")).strip().lower()
        if payload_email == normalized_email:
            return task

    return create_resolution_task(
        db,
        contact_id="",
        task_type="identity_resolution",
        proposed_claim_id=f"identity:{normalized_email}",
        current_claim_id=None,
        payload_json={
            "email": normalized_email,
            "reason": "No contact match found",
            **(payload_json or {}),
        },
    )


def list_resolution_tasks(db: Session, status: str = "open") -> list[ResolutionTask]:
    return db.scalars(
        select(ResolutionTask).where(ResolutionTask.status == status).order_by(ResolutionTask.created_at.desc())
    ).all()


def _extract_employer_name(value_json: dict) -> str | None:
    for key in ("company", "employer", "organization", "org", "target", "destination", "object"):
        value = value_json.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _json_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return str(value)


def resolve_resolution_task(
    db: Session,
    task_id: str,
    action: str,
    edited_value_json: dict | None,
    audit_update: dict,
) -> ResolutionTask | None:
    task = db.scalar(select(ResolutionTask).where(ResolutionTask.task_id == task_id))
    if task is None:
        return None

    proposed_claim = get_claim_by_id(task.proposed_claim_id) if task.proposed_claim_id else None
    current_claim = get_claim_by_id(task.current_claim_id) if task.current_claim_id else None

    resolved_at = audit_update.get("resolved_at")
    if action in {"accept_proposed", "edit_and_accept"} and task.proposed_claim_id:
        accepted_value = edited_value_json if (action == "edit_and_accept" and edited_value_json) else None
        update_claim_status(
            task.proposed_claim_id,
            "accepted",
            value_json=accepted_value,
            resolved_at_iso=resolved_at,
        )
        if task.current_claim_id:
            update_claim_status(task.current_claim_id, "superseded", resolved_at_iso=resolved_at)

        active_claim = proposed_claim or {}
        claim_type = str(active_claim.get("claim_type") or "")
        value_json = accepted_value if accepted_value else (active_claim.get("value_json") or {})
        employer_name = _extract_employer_name(value_json) if claim_type == "employment" else None
        if employer_name and task.contact_id:
            set_current_employer(
                contact_id=task.contact_id,
                company_name=employer_name,
                claim_id=task.proposed_claim_id,
                resolved_at_iso=str(resolved_at),
            )
        task.status = "resolved"
    else:
        if task.proposed_claim_id:
            update_claim_status(task.proposed_claim_id, "rejected", resolved_at_iso=resolved_at)
        task.status = "dismissed"

    payload = dict(task.payload_json)
    payload.setdefault("audit_log", []).append(audit_update)
    payload["resolution_details"] = {
        "action": action,
        "proposed_claim_status": "accepted" if task.status == "resolved" else "rejected",
        "current_claim_status": "superseded" if task.status == "resolved" and task.current_claim_id else None,
        "proposed_claim_before": _json_safe(proposed_claim),
        "current_claim_before": _json_safe(current_claim),
    }
    task.payload_json = payload
    db.commit()
    db.refresh(task)
    return task
