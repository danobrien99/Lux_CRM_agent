from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.pg.models import Interaction, RawEvent


def upsert_raw_event(
    db: Session,
    *,
    source_system: str,
    event_type: str,
    external_id: str,
    payload_json: dict,
) -> tuple[RawEvent, bool]:
    existing = db.scalar(
        select(RawEvent).where(
            RawEvent.source_system == source_system,
            RawEvent.external_id == external_id,
        )
    )
    if existing:
        return existing, False

    event = RawEvent(
        source_system=source_system,
        event_type=event_type,
        external_id=external_id,
        payload_json=payload_json,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, True


def upsert_interaction(
    db: Session,
    *,
    source_system: str,
    event_type: str,
    external_id: str,
    timestamp: datetime,
    interaction_payload: dict,
) -> tuple[Interaction, bool]:
    existing = db.scalar(
        select(Interaction).where(
            Interaction.source_system == source_system,
            Interaction.external_id == external_id,
        )
    )
    if existing:
        return existing, False

    interaction = Interaction(
        source_system=source_system,
        external_id=external_id,
        type=interaction_payload["type"],
        timestamp=timestamp,
        direction=interaction_payload.get("direction", "na"),
        subject=interaction_payload.get("subject"),
        thread_id=interaction_payload.get("thread_id") or external_id,
        participants_json=interaction_payload["participants_json"],
        contact_ids_json=[],
        status="new",
    )
    db.add(interaction)
    db.commit()
    db.refresh(interaction)
    return interaction, True
