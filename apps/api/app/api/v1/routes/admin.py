from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, get_settings_dep
from app.api.v1.schemas import BackfillContactStatusResponse, ReprocessRequest
from app.core.security import verify_webhook_secret, webhook_secret_header
from app.db.pg.models import ContactCache, Interaction
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


@router.get("/backfill_contact_status", response_model=BackfillContactStatusResponse)
def backfill_contact_status(
    db: Session = Depends(get_db),
    settings=Depends(get_settings_dep),
    x_webhook_secret: str | None = Depends(webhook_secret_header),
) -> BackfillContactStatusResponse:
    verify_webhook_secret(settings, x_webhook_secret)

    contacts = db.scalars(select(ContactCache)).all()
    processed_contact_ids: set[str] = set()
    processed_contact_rows = db.scalars(select(Interaction.contact_ids_json).where(Interaction.status == "processed")).all()
    for contact_ids in processed_contact_rows:
        if not isinstance(contact_ids, list):
            continue
        for contact_id in contact_ids:
            if isinstance(contact_id, str) and contact_id.strip():
                processed_contact_ids.add(contact_id.strip())

    processed_primary_emails = sorted(
        {
            contact.primary_email.strip().lower()
            for contact in contacts
            if contact.contact_id in processed_contact_ids and contact.primary_email
        }
    )

    return BackfillContactStatusResponse(
        asof=datetime.now(timezone.utc),
        total_contact_count=len(contacts),
        processed_contact_count=len(processed_contact_ids),
        processed_contact_ids=sorted(processed_contact_ids),
        processed_primary_emails=processed_primary_emails,
    )
