from __future__ import annotations

import copy
import importlib
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _normalize_ops(ops: list[dict], auto_accept_threshold: float) -> list[dict]:
    normalized: list[dict] = []
    for item in ops:
        claim = copy.deepcopy(item.get("claim", {}))
        confidence = float(claim.get("confidence", 0.0))

        if claim and confidence >= auto_accept_threshold and claim.get("status") != "accepted":
            claim["status"] = "accepted"

        normalized.append(
            {
                "op": item.get("op", "ADD"),
                "claim": claim,
                "target_claim_id": item.get("target_claim_id"),
                "evidence_refs": item.get("evidence_refs", claim.get("evidence_refs", [])),
            }
        )
    return normalized


def _fallback_ops(bundle: dict) -> list[dict]:
    ops: list[dict] = []
    threshold = float(bundle.get("auto_accept_threshold", 0.9))
    candidates = bundle.get("candidate_claims")
    if not isinstance(candidates, list):
        candidates = bundle.get("cognee_candidates", [])

    for claim in candidates:
        claim_copy = copy.deepcopy(claim)
        confidence = float(claim_copy.get("confidence", 0.0))
        if confidence >= threshold:
            claim_copy["status"] = "accepted"

        ops.append(
            {
                "op": "ADD",
                "claim": claim_copy,
                "target_claim_id": claim_copy.get("target_claim_id"),
                "evidence_refs": claim_copy.get("evidence_refs", []),
            }
        )
    return ops


def _propose_via_local_module(bundle: dict) -> list[dict] | None:
    settings = get_settings()
    try:
        module = importlib.import_module(settings.mem0_local_module)
        proposer = getattr(module, settings.mem0_local_function)
    except Exception:
        logger.exception(
            "mem0_local_import_failed",
            extra={
                "module": settings.mem0_local_module,
                "function": settings.mem0_local_function,
            },
        )
        return None

    def _invoke() -> Any:
        try:
            return proposer(bundle=bundle)
        except TypeError:
            return proposer(bundle)

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_invoke)
        try:
            result = future.result(timeout=float(settings.mem0_local_timeout_seconds))
        except FutureTimeoutError:
            logger.error(
                "mem0_local_execution_timeout",
                extra={"timeout_seconds": settings.mem0_local_timeout_seconds},
            )
            future.cancel()
            return None
    except Exception:
        logger.exception("mem0_local_execution_failed")
        return None
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    if not isinstance(result, list):
        logger.error("mem0_local_invalid_result_type", extra={"type": type(result).__name__})
        return None

    return _normalize_ops(result, float(bundle.get("auto_accept_threshold", 0.9)))


def _propose_via_http(bundle: dict) -> list[dict] | None:
    settings = get_settings()
    if not settings.mem0_endpoint:
        return None

    url = f"{settings.mem0_endpoint.rstrip('/')}/propose_ops"
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(url, json=bundle)
        response.raise_for_status()
        result = response.json()
    except Exception:
        logger.exception("mem0_http_execution_failed", extra={"url": url})
        return None

    if not isinstance(result, list):
        logger.error("mem0_http_invalid_result_type", extra={"type": type(result).__name__})
        return None

    return _normalize_ops(result, float(bundle.get("auto_accept_threshold", 0.9)))


def propose_memory_ops(bundle: dict) -> list[dict]:
    """
    Local-first memory adapter.

    Modes:
    - local: import OSS Mem0 module directly (default)
    - http: call a self-hosted Mem0 HTTP service

    Falls back to deterministic ops when adapters fail.
    """
    settings = get_settings()
    backend = settings.mem0_backend.lower().strip()

    if backend == "local":
        result = _propose_via_local_module(bundle)
        if result is not None:
            return result
    elif backend == "http":
        result = _propose_via_http(bundle)
        if result is not None:
            return result
    else:
        logger.warning("unknown_mem0_backend", extra={"backend": backend})

    if settings.mem0_enable_rules_fallback:
        logger.warning("mem0_fallback_rules", extra={"backend": backend})
        return _fallback_ops(bundle)

    raise RuntimeError(
        "Mem0 memory operation proposal failed and rules fallback is disabled. "
        "Enable MEM0_ENABLE_RULES_FALLBACK=true for rescue mode."
    )
