from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.schemas import ResolveTaskRequest, ResolveTaskResponse, ResolutionTaskListResponse
from app.services.resolution.tasks import list_resolution_tasks, resolve_resolution_task
from app.services.resolution.ui_payloads import to_ui_payload

router = APIRouter(prefix="/resolution", tags=["resolution"])


@router.get("/tasks", response_model=ResolutionTaskListResponse)
def get_resolution_tasks(status: str = "open", db: Session = Depends(get_db)) -> ResolutionTaskListResponse:
    tasks = list_resolution_tasks(db, status=status)
    return ResolutionTaskListResponse(tasks=[to_ui_payload(task) for task in tasks])


@router.post("/tasks/{task_id}/resolve", response_model=ResolveTaskResponse)
def resolve_task(task_id: str, payload: ResolveTaskRequest, db: Session = Depends(get_db)) -> ResolveTaskResponse:
    audit_update = {
        "action": payload.action,
        "edited_value_json": payload.edited_value_json,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    task = resolve_resolution_task(
        db,
        task_id=task_id,
        action=payload.action,
        edited_value_json=payload.edited_value_json,
        audit_update=audit_update,
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    return ResolveTaskResponse(task_id=task.task_id, status=task.status)
