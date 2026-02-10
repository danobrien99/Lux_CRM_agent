from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.schemas import ContactRow
from app.db.neo4j.queries import merge_contact
from app.db.pg.models import ContactCache
from app.services.contacts_registry.sheets_client import fetch_sheet_rows


def _upsert_contact(db: Session, row: ContactRow) -> ContactCache:
    existing = db.scalar(select(ContactCache).where(ContactCache.contact_id == row.contact_id))
    if existing:
        existing.primary_email = row.primary_email
        existing.display_name = row.display_name
        existing.owner_user_id = row.owner_user_id
        existing.use_sensitive_in_drafts = row.use_sensitive_in_drafts
        db.commit()
        db.refresh(existing)
        merged = existing
    else:
        merged = ContactCache(
            contact_id=row.contact_id,
            primary_email=row.primary_email,
            display_name=row.display_name,
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
            "owner_user_id": merged.owner_user_id,
        }
    )
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
