from __future__ import annotations

from app.services.drafting.tone import resolve_tone_band


def test_tone_band_mapping() -> None:
    assert resolve_tone_band(20)["tone_band"] == "cool_professional"
    assert resolve_tone_band(55)["tone_band"] == "warm_professional"
    assert resolve_tone_band(90)["tone_band"] == "friendly_personal"
