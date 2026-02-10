from __future__ import annotations

from datetime import datetime, timezone


def compute_relationship_score(last_interaction_at: datetime | None, interaction_count_30d: int, interaction_count_90d: int, warmth_delta: float, depth_count: int) -> tuple[float, dict]:
    now = datetime.now(timezone.utc)
    if last_interaction_at:
        days_since = max((now - last_interaction_at).days, 0)
        recency = max(0.0, 30.0 - min(days_since, 30))
    else:
        days_since = 999
        recency = 0.0

    frequency = min(25.0, interaction_count_30d * 2.0 + interaction_count_90d * 0.2)
    warmth = max(-10.0, min(10.0, warmth_delta)) + 10.0
    depth = min(15.0, depth_count * 1.5)
    base = 20.0
    total = max(0.0, min(100.0, base + recency + frequency + warmth + depth))

    components = {
        "days_since_last": days_since,
        "recency": recency,
        "frequency": frequency,
        "warmth": warmth,
        "depth": depth,
        "base": base,
    }
    return total, components
