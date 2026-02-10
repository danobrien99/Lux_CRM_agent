from __future__ import annotations

from app.api.v1.schemas import InteractionEventIn, NewsItemIn


EVENT_TYPE_TO_INTERACTION_TYPE = {
    "email_received": "email",
    "email_sent": "email",
    "meeting_transcript": "meeting",
    "news_item": "news",
    "note": "note",
}


def normalize_interaction_event(payload: InteractionEventIn) -> dict:
    return {
        "source_system": payload.source_system,
        "type": EVENT_TYPE_TO_INTERACTION_TYPE.get(payload.event_type, "note"),
        "timestamp": payload.timestamp,
        "direction": payload.direction,
        "subject": payload.subject,
        "thread_id": payload.thread_id,
        "participants_json": payload.participants.model_dump(by_alias=True),
    }


def normalize_news_item(payload: NewsItemIn) -> dict:
    return {
        "source_system": "news",
        "type": "news",
        "timestamp": payload.published_at,
        "direction": "na",
        "subject": payload.title,
        "thread_id": payload.url,
        "participants_json": {"from": [], "to": [], "cc": []},
    }
