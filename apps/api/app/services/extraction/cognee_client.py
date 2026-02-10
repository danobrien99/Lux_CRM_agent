from __future__ import annotations

import hashlib
import importlib
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _heuristic_extract(interaction_id: str, text: str) -> dict:
    words = [w.strip(".,:;!?()[]{}\"'") for w in text.split() if len(w) > 3]
    unique = sorted(set(w.lower() for w in words))
    topics = [{"label": item, "confidence": 0.55} for item in unique[:8]]
    entities = [{"name": item.title(), "type": "Topic", "confidence": 0.5} for item in unique[:5]]

    if "joined" in text.lower() or "new role" in text.lower():
        relations = [
            {
                "subject": "contact",
                "predicate": "employment_change",
                "object": "detected",
                "confidence": 0.91,
                "evidence_spans": [{"start": 0, "end": min(180, len(text))}],
            }
        ]
    else:
        relations = []

    signature = hashlib.md5(text.encode("utf-8")).hexdigest()
    return {
        "interaction_id": interaction_id,
        "entities": entities,
        "relations": relations,
        "topics": topics,
        "signature": signature,
    }


def _normalize_result(interaction_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "interaction_id": interaction_id,
        "entities": list(result.get("entities", [])),
        "relations": list(result.get("relations", [])),
        "topics": list(result.get("topics", [])),
        "signature": result.get("signature") or hashlib.md5(str(result).encode("utf-8")).hexdigest(),
    }


def _extract_via_local_module(interaction_id: str, text: str) -> dict[str, Any] | None:
    settings = get_settings()
    try:
        module = importlib.import_module(settings.cognee_local_module)
        extractor = getattr(module, settings.cognee_local_function)
    except Exception:
        logger.exception(
            "cognee_local_import_failed",
            extra={
                "module": settings.cognee_local_module,
                "function": settings.cognee_local_function,
            },
        )
        return None

    try:
        result = extractor(interaction_id=interaction_id, text=text)
    except TypeError:
        # Support adapters that use positional args.
        result = extractor(interaction_id, text)
    except Exception:
        logger.exception("cognee_local_execution_failed")
        return None

    if not isinstance(result, dict):
        logger.error("cognee_local_invalid_result_type", extra={"type": type(result).__name__})
        return None
    return _normalize_result(interaction_id, result)


def _extract_via_http(interaction_id: str, text: str) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.cognee_endpoint:
        return None

    url = f"{settings.cognee_endpoint.rstrip('/')}/extract"
    payload = {"interaction_id": interaction_id, "text": text}
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
    except Exception:
        logger.exception("cognee_http_execution_failed", extra={"url": url})
        return None

    if not isinstance(result, dict):
        logger.error("cognee_http_invalid_result_type", extra={"type": type(result).__name__})
        return None
    return _normalize_result(interaction_id, result)


def extract_candidates(interaction_id: str, text: str) -> dict:
    """
    Local-first extraction adapter.

    Modes:
    - local: import OSS Cognee module directly (default)
    - http: call a self-hosted Cognee HTTP service

    Falls back to deterministic heuristic extraction only if explicitly enabled.
    """
    settings = get_settings()
    backend = settings.cognee_backend.lower().strip()

    if backend == "local":
        result = _extract_via_local_module(interaction_id, text)
        if result is not None:
            return result
    elif backend == "http":
        result = _extract_via_http(interaction_id, text)
        if result is not None:
            return result
    else:
        logger.warning("unknown_cognee_backend", extra={"backend": backend})

    if settings.cognee_enable_heuristic_fallback:
        logger.warning("cognee_fallback_heuristic", extra={"backend": backend})
        return _heuristic_extract(interaction_id, text)

    raise RuntimeError(
        "Cognee extraction failed and heuristic fallback is disabled. "
        "Enable COGNEE_ENABLE_HEURISTIC_FALLBACK=true for rescue mode."
    )
