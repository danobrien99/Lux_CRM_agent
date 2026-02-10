from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, get_settings_dep
from app.api.v1.schemas import ContactLookupResponse, ContactsSyncRequest
from app.core.security import verify_webhook_secret, webhook_secret_header
from app.db.pg.models import ContactCache
from app.services.contacts_registry.sync import sync_contacts
from app.services.resolution.tasks import create_identity_resolution_task

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post("/sync")
def contacts_sync(
    payload: ContactsSyncRequest,
    db: Session = Depends(get_db),
    settings=Depends(get_settings_dep),
    x_webhook_secret: str | None = Depends(webhook_secret_header),
) -> dict:
    verify_webhook_secret(settings, x_webhook_secret)
    return sync_contacts(db, payload.mode, payload.rows)


@router.get("/lookup", response_model=ContactLookupResponse)
def lookup_contact(email: str, db: Session = Depends(get_db)) -> ContactLookupResponse:
    contact = db.scalar(select(ContactCache).where(ContactCache.primary_email == email.lower()))
    if contact:
        return ContactLookupResponse(
            contact_id=contact.contact_id,
            primary_email=contact.primary_email,
            display_name=contact.display_name,
        )

    task = create_identity_resolution_task(
        db,
        email=email,
        payload_json={"lookup_source": "contacts.lookup"},
    )
    return ContactLookupResponse(
        contact_id=None,
        primary_email=email,
        display_name=None,
        resolution_task_id=task.task_id,
    )
