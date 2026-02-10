from __future__ import annotations

from datetime import datetime, timezone

from app.db.neo4j.queries import upsert_score_snapshot


def persist_score_snapshot(contact_id: str, relationship_score: float, priority_score: float, components_json: dict) -> dict:
    asof = datetime.now(timezone.utc).date().isoformat()
    upsert_score_snapshot(
        contact_id=contact_id,
        asof=asof,
        relationship_score=relationship_score,
        priority_score=priority_score,
        components_json=components_json,
    )
    return {
        "contact_id": contact_id,
        "asof": asof,
        "relationship_score": relationship_score,
        "priority_score": priority_score,
        "components_json": components_json,
    }
