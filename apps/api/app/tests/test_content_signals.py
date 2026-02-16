from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.scoring import content_signals


def test_llm_content_signals_disabled_uses_heuristics(monkeypatch) -> None:
    monkeypatch.setattr(
        content_signals,
        "get_settings",
        lambda: SimpleNamespace(
            scoring_use_llm_warmth_depth=False,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            scoring_llm_max_interactions=8,
            scoring_llm_snippet_chars=280,
        ),
    )
    interactions = [
        SimpleNamespace(
            interaction_id="int-1",
            timestamp=datetime.now(timezone.utc),
            direction="in",
            subject="Quick check-in",
        )
    ]

    warmth, depth, meta = content_signals.derive_warmth_depth_signals(
        db=None,
        contact_interactions=interactions,  # type: ignore[arg-type]
        heuristic_warmth_delta=1.5,
        heuristic_depth_count=2,
    )

    assert warmth == 1.5
    assert depth == 2
    assert meta["source"] == "heuristic"


def test_llm_content_signals_enabled_uses_openai_result(monkeypatch) -> None:
    monkeypatch.setattr(
        content_signals,
        "get_settings",
        lambda: SimpleNamespace(
            scoring_use_llm_warmth_depth=True,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            scoring_llm_max_interactions=8,
            scoring_llm_snippet_chars=280,
        ),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        content_signals,
        "_score_with_openai",
        lambda **_kwargs: {"warmth_delta": 7.3, "depth_count": 6},
    )
    interactions = [
        SimpleNamespace(
            interaction_id="int-2",
            timestamp=datetime.now(timezone.utc),
            direction="out",
            subject="Follow-up and next steps",
        )
    ]

    warmth, depth, meta = content_signals.derive_warmth_depth_signals(
        db=None,
        contact_interactions=interactions,  # type: ignore[arg-type]
        heuristic_warmth_delta=0.0,
        heuristic_depth_count=1,
    )

    assert warmth == 7.3
    assert depth == 6
    assert meta["source"] == "llm"
