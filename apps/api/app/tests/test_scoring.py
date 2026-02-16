from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.scoring.priority_score import compute_priority_score
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


def test_relationship_and_priority_scores_are_zero_without_interactions() -> None:
    relationship, _ = compute_relationship_score(
        last_interaction_at=None,
        interaction_count_30d=0,
        interaction_count_90d=0,
        warmth_delta=0,
        depth_count=0,
    )
    priority, _ = compute_priority_score(
        relationship_score=relationship,
        inactivity_days=365,
        open_loops=0,
        trigger_score=0,
    )

    assert relationship == 0
    assert priority == 0
