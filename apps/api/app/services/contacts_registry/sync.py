from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.routes.scores import refresh_cached_interaction_summary
from app.api.v1.schemas import ContactRow
from app.db.neo4j.queries import (
    delete_case_contact_graph_by_email,
    delete_contact_graph,
    merge_contact,
    merge_internal_user,
    upsert_contact_company_relation,
)
from app.db.pg.models import ContactCache, Draft, ResolutionTask
from app.services.identity.internal_users import internal_user_emails, is_internal_email
from app.services.contacts_registry.sheets_client import fetch_sheet_rows

logger = logging.getLogger(__name__)

_GENERIC_DOMAIN_PARTS = {
    "com",
    "org",
    "net",
    "io",
    "co",
    "global",
    "ai",
    "inc",
    "ltd",
}


def _normalize_key(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _email_domain_tokens(email: str | None) -> set[str]:
    if not email or "@" not in email:
        return set()
    domain = email.split("@", 1)[-1].strip().lower()
    tokens = {part for part in re.split(r"[^a-z0-9]+", domain) if part and part not in _GENERIC_DOMAIN_PARTS}
    if "." in domain:
        left = domain.split(".", 1)[0].strip()
        if left and left not in _GENERIC_DOMAIN_PARTS:
            tokens.add(left)
    return tokens


def _company_match_score(primary_email: str | None, company_name: str | None) -> int:
    normalized_company = _normalize_key(company_name)
    if not normalized_company:
        return 0
    score = 1
    domain_tokens = _email_domain_tokens(primary_email)
    if any(token and token in normalized_company for token in domain_tokens):
        score += 10
    if any(token and normalized_company in token for token in domain_tokens):
        score += 6
    return score


def _row_preference_key(row: ContactRow, original_index: int) -> tuple[int, int, int, int]:
    email = (row.primary_email or "").strip().lower()
    company = (row.company or "").strip()
    display_name = (row.display_name or "").strip()
    has_names = int(bool((row.first_name or "").strip() or (row.last_name or "").strip()))
    return (
        _company_match_score(email, company),
        int(bool(company)),
        int(bool(display_name) or bool(has_names)),
        original_index,  # prefer later rows when otherwise equivalent
    )


def _dedupe_source_rows(rows: list[ContactRow]) -> list[ContactRow]:
    if not rows:
        return []
    winners: dict[str, tuple[int, ContactRow, int]] = {}
    duplicate_emails: set[str] = set()
    passthrough: list[ContactRow] = []
    for idx, row in enumerate(rows):
        normalized_email = (row.primary_email or "").strip().lower()
        if not normalized_email:
            passthrough.append(row)
            continue
        candidate_key = _row_preference_key(row, idx)
        if normalized_email not in winners:
            winners[normalized_email] = (idx, row, idx)
            continue
        duplicate_emails.add(normalized_email)
        _, existing_row, existing_idx = winners[normalized_email]
        existing_key = _row_preference_key(existing_row, existing_idx)
        if candidate_key >= existing_key:
            winners[normalized_email] = (idx, row, idx)
    deduped_by_email = [row for _, row, _ in sorted(winners.values(), key=lambda item: item[0])]
    if duplicate_emails:
        logger.warning(
            "contacts_sync_duplicate_emails_deduped",
            extra={
                "duplicate_email_count": len(duplicate_emails),
                "duplicate_emails_sample": sorted(duplicate_emails)[:10],
                "input_rows": len(rows),
                "deduped_rows": len(deduped_by_email) + len(passthrough),
            },
        )
    return deduped_by_email + passthrough


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


def _purge_contact_cache_entry(db: Session, contact: ContactCache) -> None:
    contact_id = contact.contact_id
    db.query(Draft).filter(Draft.contact_id == contact_id).delete(synchronize_session=False)
    db.query(ResolutionTask).filter(ResolutionTask.contact_id == contact_id).delete(synchronize_session=False)
    db.delete(contact)
    db.commit()
    try:
        delete_contact_graph(contact_id)
    except Exception:
        logger.exception("failed_purging_internal_contact_graph", extra={"contact_id": contact_id})


def _handle_internal_row(db: Session, row: ContactRow) -> dict[str, int]:
    normalized_email = (row.primary_email or "").strip().lower()
    if not normalized_email:
        return {"purged_contacts": 0, "internal_users_upserted": 0, "purged_case_contacts": 0}

    purged = 0
    existing = db.scalar(select(ContactCache).where(ContactCache.primary_email == normalized_email))
    if existing is not None:
        _purge_contact_cache_entry(db, existing)
        purged += 1

    # Also purge by contact_id if a stale duplicate row exists under a different email normalization path.
    existing_by_id = db.scalar(select(ContactCache).where(ContactCache.contact_id == row.contact_id))
    if existing_by_id is not None and existing_by_id.primary_email.lower() != normalized_email:
        _purge_contact_cache_entry(db, existing_by_id)
        purged += 1

    purged_case_contacts = 0
    try:
        purged_case_contacts = int(delete_case_contact_graph_by_email(normalized_email) or 0)
    except Exception:
        logger.exception("failed_purging_internal_case_contact_graph", extra={"email": normalized_email})

    merge_internal_user(
        {
            "internal_user_id": row.owner_user_id or None,
            "primary_email": normalized_email,
            "display_name": _resolved_display_name(row),
        }
    )
    return {
        "purged_contacts": purged,
        "internal_users_upserted": 1,
        "purged_case_contacts": purged_case_contacts,
    }


def _purge_stale_internal_contacts(db: Session) -> tuple[int, int]:
    internal_contacts = db.scalars(select(ContactCache)).all()
    purged = 0
    purged_case_contacts = 0
    for contact in internal_contacts:
        normalized_email = (contact.primary_email or "").strip().lower()
        if not is_internal_email(normalized_email):
            continue
        _purge_contact_cache_entry(db, contact)
        purged += 1
        try:
            purged_case_contacts += int(delete_case_contact_graph_by_email(normalized_email) or 0)
        except Exception:
            logger.exception("failed_purging_internal_case_contact_graph", extra={"email": normalized_email})
        merge_internal_user(
            {
                "internal_user_id": contact.owner_user_id,
                "primary_email": contact.primary_email,
                "display_name": contact.display_name,
            }
        )
    return purged, purged_case_contacts


def _purge_configured_internal_case_contacts() -> int:
    purged = 0
    for email in sorted(internal_user_emails()):
        try:
            purged += int(delete_case_contact_graph_by_email(email) or 0)
        except Exception:
            logger.exception("failed_purging_configured_internal_case_contact_graph", extra={"email": email})
    return purged


def sync_contacts(db: Session, mode: str, rows: list[ContactRow]) -> dict:
    source_rows = rows
    if mode == "pull":
        source_rows = [ContactRow(**r) for r in fetch_sheet_rows()]
    source_rows = _dedupe_source_rows(source_rows)

    stale_internal_contacts_purged, stale_internal_case_contacts_purged = _purge_stale_internal_contacts(db)
    stale_internal_case_contacts_purged += _purge_configured_internal_case_contacts()
    upserted = 0
    skipped_internal = 0
    purged_internal_contacts = 0
    purged_internal_case_contacts = 0
    internal_users_upserted = 0
    for row in source_rows:
        if is_internal_email(row.primary_email):
            skipped_internal += 1
            stats = _handle_internal_row(db, row)
            purged_internal_contacts += int(stats.get("purged_contacts", 0))
            purged_internal_case_contacts += int(stats.get("purged_case_contacts", 0))
            internal_users_upserted += int(stats.get("internal_users_upserted", 0))
            continue
        _upsert_contact(db, row)
        upserted += 1

    return {
        "mode": mode,
        "upserted": upserted,
        "skipped_internal": skipped_internal,
        "purged_internal_contacts": purged_internal_contacts,
        "internal_users_upserted": internal_users_upserted,
        "stale_internal_contacts_purged": stale_internal_contacts_purged,
        "stale_internal_case_contacts_purged": stale_internal_case_contacts_purged,
        "purged_internal_case_contacts": purged_internal_case_contacts,
    }
