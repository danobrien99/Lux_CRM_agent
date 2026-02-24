from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, get_settings_dep
from app.api.v1.schemas import ContactLookupResponse, ContactsSyncRequest
from app.core.security import verify_webhook_secret, webhook_secret_header
from app.db.neo4j.queries import delete_contact_graph
from app.db.pg.models import ContactCache, Draft, ResolutionTask
from app.services.contacts_registry.sync import sync_contacts
from app.services.identity.internal_users import is_internal_email
from app.services.resolution.tasks import create_identity_resolution_task

router = APIRouter(prefix="/contacts", tags=["contacts"])
logger = logging.getLogger(__name__)


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
    if is_internal_email(email):
        return ContactLookupResponse(
            contact_id=None,
            primary_email=email.lower(),
            display_name=None,
            resolution_task_id=None,
        )

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


@router.delete("/{contact_id}")
def delete_contact(contact_id: str, db: Session = Depends(get_db)) -> dict:
    contact = db.get(ContactCache, contact_id)
    if contact is None:
        return {"contact_id": contact_id, "deleted": False}

    db.execute(delete(Draft).where(Draft.contact_id == contact_id))
    db.execute(delete(ResolutionTask).where(ResolutionTask.contact_id == contact_id))
    db.delete(contact)
    db.commit()

    graph_deleted = True
    graph_delete_error: str | None = None
    try:
        delete_contact_graph(contact_id)
    except Exception as exc:  # pragma: no cover - defensive path for external db outages
        graph_deleted = False
        graph_delete_error = str(exc)
        logger.exception("Failed deleting contact graph for contact_id=%s", contact_id)

    return {
        "contact_id": contact_id,
        "deleted": True,
        "graph_deleted": graph_deleted,
        "graph_delete_error": graph_delete_error,
    }
