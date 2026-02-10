from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.scoring.relationship_score import compute_relationship_score


def test_relationship_score_changes_with_recency() -> None:
    recent, _ = compute_relationship_score(
        last_interaction_at=datetime.now(timezone.utc) - timedelta(days=2),
        interaction_count_30d=5,
        interaction_count_90d=10,
        warmth_delta=1,
        depth_count=3,
    )
    stale, _ = compute_relationship_score(
        last_interaction_at=datetime.now(timezone.utc) - timedelta(days=60),
        interaction_count_30d=0,
        interaction_count_90d=1,
        warmth_delta=1,
        depth_count=3,
    )

    assert recent > stale
