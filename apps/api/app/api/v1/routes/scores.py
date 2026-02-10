from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.schemas import ContactScoreItem, ScoreReason, ScoreTodayResponse
from app.db.pg.models import ContactCache, Interaction
from app.services.scoring.priority_score import compute_priority_score
from app.services.scoring.relationship_score import compute_relationship_score

router = APIRouter(prefix="/scores", tags=["scores"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _interactions_by_contact(db: Session) -> dict[str, list[Interaction]]:
    interactions = db.scalars(select(Interaction).order_by(Interaction.timestamp.desc()).limit(1000)).all()
    grouped: dict[str, list[Interaction]] = defaultdict(list)
    for interaction in interactions:
        for contact_id in interaction.contact_ids_json or []:
            grouped[contact_id].append(interaction)
    return grouped


def _contact_scores(db: Session) -> list[ContactScoreItem]:
    contacts = db.scalars(select(ContactCache)).all()
    interactions_by_contact = _interactions_by_contact(db)
    rows: list[ContactScoreItem] = []
    now = datetime.now(timezone.utc)

    for contact in contacts:
        contact_interactions = interactions_by_contact.get(contact.contact_id, [])
        last = contact_interactions[0] if contact_interactions else None
        inactivity_days = (now - _as_utc(last.timestamp)).days if last else 365
        interaction_count_30d = sum(
            1 for interaction in contact_interactions if (now - _as_utc(interaction.timestamp)).days <= 30
        )
        interaction_count_90d = sum(
            1 for interaction in contact_interactions if (now - _as_utc(interaction.timestamp)).days <= 90
        )

        relationship_score, relationship_components = compute_relationship_score(
            last_interaction_at=_as_utc(last.timestamp) if last else None,
            interaction_count_30d=interaction_count_30d,
            interaction_count_90d=interaction_count_90d,
            warmth_delta=1.5,
            depth_count=2,
        )
        priority_score, priority_components = compute_priority_score(
            relationship_score=relationship_score,
            inactivity_days=inactivity_days,
            open_loops=1,
            trigger_score=0,
        )
        why_now = "No recent interaction" if inactivity_days >= 30 else "Maintain momentum from recent activity"

        rows.append(
            ContactScoreItem(
                contact_id=contact.contact_id,
                display_name=contact.display_name,
                relationship_score=round(relationship_score, 2),
                priority_score=round(priority_score, 2),
                why_now=why_now,
                reasons=[
                    ScoreReason(
                        summary="Score from per-contact recency, frequency, and open-loop features",
                        evidence_refs=[
                            {
                                "component": "relationship",
                                "values": relationship_components,
                                "contact_interaction_count_30d": interaction_count_30d,
                                "contact_interaction_count_90d": interaction_count_90d,
                            },
                            {
                                "component": "priority",
                                "values": priority_components,
                                "last_interaction_id": last.interaction_id if last else None,
                            },
                        ],
                    )
                ],
            )
        )

    rows.sort(key=lambda item: item.priority_score, reverse=True)
    return rows


@router.get("/today", response_model=ScoreTodayResponse)
def today_scores(limit: int = 50, db: Session = Depends(get_db)) -> ScoreTodayResponse:
    items = _contact_scores(db)[:limit]
    return ScoreTodayResponse(asof=datetime.now(timezone.utc), items=items)


@router.get("/contact/{contact_id}")
def contact_score_detail(contact_id: str, db: Session = Depends(get_db)) -> dict:
    all_scores = _contact_scores(db)
    current = next((item for item in all_scores if item.contact_id == contact_id), None)
    if current is None:
        return {"contact_id": contact_id, "trend": [], "current": None}

    trend = [
        {
            "asof": datetime.now(timezone.utc).date().isoformat(),
            "relationship_score": current.relationship_score,
            "priority_score": current.priority_score,
            "components": current.reasons[0].evidence_refs,
        }
    ]
    return {"contact_id": contact_id, "trend": trend, "current": current.model_dump()}
