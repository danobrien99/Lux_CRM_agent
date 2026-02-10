from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.schemas import ReprocessRequest
from app.workers.jobs import cleanup_data
from app.workers.queue import enqueue_job

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reprocess")
def reprocess(payload: ReprocessRequest) -> dict:
    job_id = enqueue_job("process_interaction", payload.interaction_id)
    return {"job_id": job_id, "status": "enqueued"}


@router.post("/recompute_scores")
def recompute_scores() -> dict:
    job_id = enqueue_job("recompute_scores")
    return {"job_id": job_id, "status": "enqueued"}


@router.post("/cleanup")
def cleanup() -> dict:
    return cleanup_data()
