from __future__ import annotations

from datetime import datetime, timezone


def compute_relationship_score(last_interaction_at: datetime | None, interaction_count_30d: int, interaction_count_90d: int, warmth_delta: float, depth_count: int) -> tuple[float, dict]:
    now = datetime.now(timezone.utc)
    if last_interaction_at:
        days_since = max((now - last_interaction_at).days, 0)
        recency = max(0.0, 45.0 - (min(days_since, 180) * 0.25))
    else:
        days_since = 999
        recency = 0.0

    trailing_31_90 = max(0, interaction_count_90d - interaction_count_30d)
    frequency = min(45.0, interaction_count_30d * 4.0 + trailing_31_90 * 1.5)
    warmth = max(-10.0, min(10.0, warmth_delta))
    depth = min(10.0, max(depth_count, 0))
    total = max(0.0, min(100.0, recency + frequency + warmth + depth))

    components = {
        "days_since_last": days_since,
        "recency": recency,
        "frequency": frequency,
        "warmth": warmth,
        "depth": depth,
        "trailing_31_90": trailing_31_90,
    }
    return total, components
