from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db, get_settings_dep
from app.api.v1.schemas import IngestResponse, InteractionEventIn, NewsItemIn
from app.core.security import verify_webhook_secret, webhook_secret_header
from app.services.ingest.normalize import normalize_interaction_event, normalize_news_item
from app.services.ingest.store_raw import upsert_interaction, upsert_raw_event
from app.workers.queue import enqueue_job

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/interaction_event", response_model=IngestResponse)
def ingest_interaction_event(
    payload: InteractionEventIn,
    db: Session = Depends(get_db),
    settings=Depends(get_settings_dep),
    x_webhook_secret: str | None = Depends(webhook_secret_header),
) -> IngestResponse:
    verify_webhook_secret(settings, x_webhook_secret)

    raw_event, _ = upsert_raw_event(
        db,
        source_system=payload.source_system,
        event_type=payload.event_type,
        external_id=payload.external_id,
        payload_json=payload.model_dump(mode="json", by_alias=True),
    )
    interaction_payload = normalize_interaction_event(payload)
    interaction, created = upsert_interaction(
        db,
        source_system=payload.source_system,
        event_type=payload.event_type,
        external_id=payload.external_id,
        timestamp=payload.timestamp.astimezone(timezone.utc),
        interaction_payload=interaction_payload,
    )

    status = "duplicate"
    if created:
        enqueue_job("process_interaction", interaction.interaction_id)
        status = "enqueued"

    return IngestResponse(
        raw_event_id=raw_event.id,
        interaction_id=interaction.interaction_id,
        status=status,
    )


@router.post("/news_item", response_model=IngestResponse)
def ingest_news_item(
    payload: NewsItemIn,
    db: Session = Depends(get_db),
    settings=Depends(get_settings_dep),
    x_webhook_secret: str | None = Depends(webhook_secret_header),
) -> IngestResponse:
    verify_webhook_secret(settings, x_webhook_secret)

    external_id = payload.url or payload.title
    raw_event, _ = upsert_raw_event(
        db,
        source_system="news",
        event_type="news_item",
        external_id=external_id,
        payload_json=payload.model_dump(mode="json"),
    )

    normalized = normalize_news_item(payload)
    timestamp = payload.published_at or raw_event.received_at
    interaction, created = upsert_interaction(
        db,
        source_system="news",
        event_type="news_item",
        external_id=external_id,
        timestamp=timestamp,
        interaction_payload=normalized,
    )

    status = "duplicate"
    if created:
        enqueue_job("process_news", interaction.interaction_id)
        status = "enqueued"

    return IngestResponse(
        raw_event_id=raw_event.id,
        interaction_id=interaction.interaction_id,
        status=status,
    )
