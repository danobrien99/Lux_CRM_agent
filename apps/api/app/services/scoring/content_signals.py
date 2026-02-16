from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.pg.models import Chunk, Interaction
from app.services.prompts import render_prompt

logger = logging.getLogger(__name__)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None

    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _as_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()


def _snippet_map(db: Session | None, interaction_ids: list[str], max_chars: int) -> dict[str, str]:
    if db is None or not interaction_ids:
        return {}

    rows = db.execute(
        select(Chunk.interaction_id, Chunk.text)
        .where(Chunk.interaction_id.in_(interaction_ids))
        .order_by(Chunk.created_at.asc())
    ).all()

    by_interaction: dict[str, str] = {}
    for interaction_id, text in rows:
        if interaction_id in by_interaction:
            continue
        normalized = " ".join((text or "").split())
        if not normalized:
            continue
        by_interaction[interaction_id] = normalized[:max_chars]
    return by_interaction


def _build_context(
    db: Session | None,
    contact_interactions: list[Interaction],
    *,
    max_interactions: int,
    snippet_chars: int,
) -> list[dict[str, str]]:
    recent = contact_interactions[:max_interactions]
    interaction_ids = [interaction.interaction_id for interaction in recent]
    snippets = _snippet_map(db, interaction_ids, snippet_chars)

    context: list[dict[str, str]] = []
    for interaction in recent:
        excerpt = snippets.get(interaction.interaction_id) or (interaction.subject or "")
        context.append(
            {
                "interaction_id": interaction.interaction_id,
                "timestamp": _as_utc_iso(interaction.timestamp),
                "direction": interaction.direction,
                "subject": interaction.subject or "",
                "excerpt": excerpt,
            }
        )
    return context


def _score_with_openai(*, model: str, api_key: str, context: list[dict[str, str]]) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    context_json = json.dumps(context, ensure_ascii=True)
    messages = [
        {
            "role": "system",
            "content": render_prompt("warmth_depth_system"),
        },
        {
            "role": "user",
            "content": render_prompt("warmth_depth_user", context_json=context_json),
        },
    ]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    content = (response.choices[0].message.content or "").strip()
    return _extract_json_object(content) or {}


def derive_warmth_depth_signals(
    *,
    db: Session | None,
    contact_interactions: list[Interaction],
    heuristic_warmth_delta: float,
    heuristic_depth_count: int,
) -> tuple[float, int, dict[str, Any]]:
    settings = get_settings()
    if not settings.scoring_use_llm_warmth_depth:
        return heuristic_warmth_delta, heuristic_depth_count, {"source": "heuristic"}

    if not contact_interactions:
        return heuristic_warmth_delta, heuristic_depth_count, {"source": "heuristic_no_interactions"}

    if settings.llm_provider.strip().lower() != "openai":
        logger.warning("llm_warmth_depth_provider_not_supported", extra={"provider": settings.llm_provider})
        return heuristic_warmth_delta, heuristic_depth_count, {"source": "heuristic_provider_unsupported"}

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("llm_warmth_depth_missing_api_key")
        return heuristic_warmth_delta, heuristic_depth_count, {"source": "heuristic_missing_api_key"}

    context = _build_context(
        db,
        contact_interactions,
        max_interactions=settings.scoring_llm_max_interactions,
        snippet_chars=settings.scoring_llm_snippet_chars,
    )
    if not context:
        return heuristic_warmth_delta, heuristic_depth_count, {"source": "heuristic_empty_context"}

    try:
        payload = _score_with_openai(model=settings.llm_model, api_key=api_key, context=context)
        warmth_raw = float(payload.get("warmth_delta", heuristic_warmth_delta))
        depth_raw = float(payload.get("depth_count", heuristic_depth_count))
        warmth_delta = _clamp(warmth_raw, -10.0, 10.0)
        depth_count = int(_clamp(round(depth_raw), 0.0, 10.0))
        return warmth_delta, depth_count, {"source": "llm", "model": settings.llm_model}
    except Exception:
        logger.exception("llm_warmth_depth_failed_fallback_heuristic", extra={"llm_model": settings.llm_model})
        return heuristic_warmth_delta, heuristic_depth_count, {"source": "heuristic_llm_failure"}
