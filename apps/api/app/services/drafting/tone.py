from __future__ import annotations


def resolve_tone_band(relationship_score: float) -> dict:
    if relationship_score <= 35:
        return {
            "tone_band": "cool_professional",
            "greeting_style": "formal",
            "directness": "high",
            "personal_reference_allowance": "minimal",
            "sentence_length_target": "short",
            "closing_style": "professional",
        }
    if relationship_score <= 70:
        return {
            "tone_band": "warm_professional",
            "greeting_style": "warm",
            "directness": "balanced",
            "personal_reference_allowance": "limited",
            "sentence_length_target": "medium",
            "closing_style": "friendly-professional",
        }
    return {
        "tone_band": "friendly_personal",
        "greeting_style": "informal",
        "directness": "balanced",
        "personal_reference_allowance": "high",
        "sentence_length_target": "medium",
        "closing_style": "personal",
    }
