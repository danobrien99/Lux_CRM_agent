from __future__ import annotations

from app.db.pg.models import ResolutionTask


def to_ui_payload(task: ResolutionTask) -> dict:
    return {
        "task_id": task.task_id,
        "contact_id": task.contact_id,
        "task_type": task.task_type,
        "proposed_claim_id": task.proposed_claim_id,
        "current_claim_id": task.current_claim_id,
        "payload_json": task.payload_json,
        "status": task.status,
    }
