from __future__ import annotations


def compute_priority_score(relationship_score: float, inactivity_days: int, open_loops: int, trigger_score: float) -> tuple[float, dict]:
    inactivity = min(35.0, inactivity_days * 1.5)
    loop_component = min(25.0, open_loops * 5.0)
    trigger_component = max(0.0, min(20.0, trigger_score))
    relationship_temp = relationship_score * 0.2

    total = max(0.0, min(100.0, inactivity + loop_component + trigger_component + relationship_temp))
    components = {
        "inactivity": inactivity,
        "open_loops": loop_component,
        "triggers": trigger_component,
        "relationship_temp": relationship_temp,
    }
    return total, components
