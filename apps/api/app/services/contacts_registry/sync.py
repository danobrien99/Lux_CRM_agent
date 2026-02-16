from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.routes.scores import refresh_cached_interaction_summary
from app.api.v1.schemas import ContactRow
from app.db.neo4j.queries import merge_contact, upsert_contact_company_relation
from app.db.pg.models import ContactCache
from app.services.contacts_registry.sheets_client import fetch_sheet_rows

logger = logging.getLogger(__name__)


def _resolved_display_name(row: ContactRow) -> str | None:
    explicit = (row.display_name or "").strip()
    if explicit:
        return explicit

    first = (row.first_name or "").strip()
    last = (row.last_name or "").strip()
    joined = " ".join(part for part in [first, last] if part)
    return joined or None


def _apply_contact_updates(contact: ContactCache, row: ContactRow, normalized_email: str) -> None:
    contact.primary_email = normalized_email
    contact.display_name = _resolved_display_name(row)
    contact.owner_user_id = row.owner_user_id
    contact.use_sensitive_in_drafts = row.use_sensitive_in_drafts


def _upsert_contact(db: Session, row: ContactRow) -> ContactCache:
    normalized_email = row.primary_email.strip().lower()
    existing_by_id = db.scalar(select(ContactCache).where(ContactCache.contact_id == row.contact_id))
    existing_by_email = db.scalar(select(ContactCache).where(ContactCache.primary_email == normalized_email))

    if existing_by_id:
        if existing_by_email and existing_by_email.contact_id != existing_by_id.contact_id:
            # Email uniqueness wins to avoid hard failures from duplicate rows in source data.
            merged = existing_by_email
        else:
            merged = existing_by_id
        _apply_contact_updates(merged, row, normalized_email)
    else:
        if existing_by_email:
            merged = existing_by_email
            _apply_contact_updates(merged, row, normalized_email)
        else:
            merged = ContactCache(
                contact_id=row.contact_id,
                primary_email=normalized_email,
                display_name=_resolved_display_name(row),
                owner_user_id=row.owner_user_id,
                use_sensitive_in_drafts=row.use_sensitive_in_drafts,
            )
            db.add(merged)

    db.commit()
    db.refresh(merged)

    merge_contact(
        {
            "contact_id": merged.contact_id,
            "primary_email": merged.primary_email,
            "display_name": merged.display_name,
            "first_name": (row.first_name or "").strip() or None,
            "last_name": (row.last_name or "").strip() or None,
            "company": (row.company or "").strip() or None,
            "owner_user_id": merged.owner_user_id,
        }
    )
    company_name = (row.company or "").strip()
    if company_name:
        upsert_contact_company_relation(
            contact_id=merged.contact_id,
            company_name=company_name,
            source_system="contacts_registry",
            confidence=0.98,
        )
    try:
        refresh_cached_interaction_summary(
            db,
            merged.contact_id,
            display_name=merged.display_name,
            company_name=company_name or None,
        )
    except Exception:
        logger.exception("interaction_summary_cache_refresh_failed_after_contact_sync", extra={"contact_id": merged.contact_id})
    return merged


def sync_contacts(db: Session, mode: str, rows: list[ContactRow]) -> dict:
    source_rows = rows
    if mode == "pull":
        source_rows = [ContactRow(**r) for r in fetch_sheet_rows()]

    upserted = 0
    for row in source_rows:
        _upsert_contact(db, row)
        upserted += 1

    return {"mode": mode, "upserted": upserted}
