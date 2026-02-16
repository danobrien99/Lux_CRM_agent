from __future__ import annotations


def compute_priority_score(relationship_score: float, inactivity_days: int, open_loops: int, trigger_score: float) -> tuple[float, dict]:
    relationship_component = max(0.0, min(40.0, relationship_score * 0.4))
    inactivity_component = 0.0
    if relationship_score > 0:
        inactivity_component = max(0.0, min(30.0, (max(inactivity_days, 0) - 7) * 0.35))

    loop_component = min(20.0, max(open_loops, 0) * 5.0)
    trigger_component = max(0.0, min(15.0, trigger_score))

    total = max(0.0, min(100.0, relationship_component + inactivity_component + loop_component + trigger_component))
    components = {
        "relationship_component": relationship_component,
        "inactivity": inactivity_component,
        "open_loops": loop_component,
        "triggers": trigger_component,
    }
    return total, components
