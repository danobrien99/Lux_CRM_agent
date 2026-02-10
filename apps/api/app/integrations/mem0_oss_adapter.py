from __future__ import annotations

import copy
import importlib
import importlib.metadata
import json
import os
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings


def _fallback_ops(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    threshold = float(bundle.get("auto_accept_threshold", 0.9))
    ops: list[dict[str, Any]] = []
    for claim in bundle.get("cognee_candidates", []):
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


def _safe_import_mem0_memory_class() -> Any:
    settings = get_settings()
    try:
        module = importlib.import_module("mem0.memory.main")
        return getattr(module, "Memory")
    except Exception:
        repo_path = Path(settings.mem0_repo_path).expanduser()
        if repo_path.exists():
            sys.path.insert(0, str(repo_path))

        original_version_fn = importlib.metadata.version

        def _patched_version(package_name: str) -> str:
            if package_name == "mem0ai":
                try:
                    return original_version_fn(package_name)
                except importlib.metadata.PackageNotFoundError:
                    return "0.0.0-local"
            return original_version_fn(package_name)

        importlib.metadata.version = _patched_version  # type: ignore[assignment]
        try:
            module = importlib.import_module("mem0.memory.main")
        finally:
            importlib.metadata.version = original_version_fn  # type: ignore[assignment]
        return getattr(module, "Memory")


def _build_llm_config() -> dict[str, Any]:
    settings = get_settings()
    config = {
        "provider": settings.llm_provider,
        "config": {
            "model": settings.llm_model,
            "temperature": 0.2,
        },
    }
    openai_key = os.getenv("OPENAI_API_KEY")
    if settings.llm_provider == "openai" and openai_key:
        config["config"]["api_key"] = openai_key
    return config


def _build_embedder_config() -> dict[str, Any]:
    settings = get_settings()
    config = {
        "provider": settings.embedding_provider,
        "config": {
            "model": settings.embedding_model,
            "embedding_dims": settings.embedding_dim,
        },
    }
    openai_key = os.getenv("OPENAI_API_KEY")
    if settings.embedding_provider == "openai" and openai_key:
        config["config"]["api_key"] = openai_key
    return config


def _build_vector_store_config() -> dict[str, Any]:
    settings = get_settings()
    dsn = settings.neon_pg_dsn.strip()
    if not dsn.startswith("postgresql://") and not dsn.startswith("postgresql+psycopg://"):
        raise ValueError(
            "MEM0 requires a PostgreSQL DSN for pgvector in this integration path. "
            f"Current NEON_PG_DSN is not PostgreSQL: {dsn[:24]}..."
        )
    return {
        "provider": "pgvector",
        "config": {
            "connection_string": dsn,
            "collection_name": settings.mem0_collection_name,
            "embedding_model_dims": settings.embedding_dim,
            "hnsw": True,
        },
    }


def _build_graph_store_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "provider": "neo4j",
        "config": {
            "url": settings.neo4j_uri,
            "username": settings.neo4j_user,
            "password": settings.neo4j_password,
            "database": settings.mem0_graph_database,
        },
    }


@lru_cache(maxsize=1)
def _memory_instance() -> Any:
    memory_cls = _safe_import_mem0_memory_class()
    config = {
        "version": "v1.1",
        "llm": _build_llm_config(),
        "embedder": _build_embedder_config(),
        "vector_store": _build_vector_store_config(),
        "graph_store": _build_graph_store_config(),
    }
    return memory_cls.from_config(config)


def _scope_ids(bundle: dict[str, Any]) -> tuple[str, str, str | None]:
    settings = get_settings()
    scope_ids = bundle.get("scope_ids") or {}
    user_id = str(scope_ids.get("user_id") or scope_ids.get("contact_id") or "lux_default_user")
    agent_id = str(scope_ids.get("agent_id") or settings.mem0_agent_id)
    run_id_raw = scope_ids.get("run_id") or scope_ids.get("interaction_id")
    run_id = str(run_id_raw) if run_id_raw else None
    return user_id, agent_id, run_id


def _evidence_refs_from_bundle(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_refs: list[dict[str, Any]] = []
    for claim in bundle.get("cognee_candidates", []):
        for ref in claim.get("evidence_refs", []):
            if not isinstance(ref, dict):
                continue
            evidence_refs.append(
                {
                    "interaction_id": ref.get("interaction_id"),
                    "chunk_id": ref.get("chunk_id"),
                    "span_json": ref.get("span_json", {}),
                }
            )
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in evidence_refs:
        key = (str(ref.get("interaction_id")), str(ref.get("chunk_id")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _compose_messages(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    summary = str(bundle.get("new_interaction_summary", "")).strip()
    candidates = bundle.get("cognee_candidates", [])
    recent_claims = bundle.get("recent_claims", [])
    content = (
        "Summarize factual relationship memory updates from this interaction.\n\n"
        f"Interaction summary:\n{summary}\n\n"
        f"Candidate claims:\n{json.dumps(candidates, ensure_ascii=True)}\n\n"
        f"Recent accepted claims:\n{json.dumps(recent_claims, ensure_ascii=True)}"
    )
    return [{"role": "user", "content": content}]


def _iter_dicts(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        rows.append(payload)
        for value in payload.values():
            rows.extend(_iter_dicts(value))
    elif isinstance(payload, list):
        for item in payload:
            rows.extend(_iter_dicts(item))
    return rows


def _claim_type_for_relation(predicate: str) -> str:
    normalized = predicate.lower()
    employment_markers = {
        "works_at",
        "employed_by",
        "current_employer",
        "joined",
        "left",
        "employment_change",
    }
    if normalized in employment_markers or "employ" in normalized or "works" in normalized:
        return "employment"
    return "topic"


def _stable_claim_id(claim_type: str, value_json: dict[str, Any]) -> str:
    payload = f"{claim_type}:{json.dumps(value_json, sort_keys=True, ensure_ascii=True)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def _claim_from_relation(
    relation: dict[str, Any],
    *,
    threshold: float,
    evidence_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    source = str(relation.get("source") or relation.get("subject") or "").strip()
    predicate = str(relation.get("relationship") or relation.get("predicate") or "").strip()
    destination = str(relation.get("destination") or relation.get("target") or relation.get("object") or "").strip()
    if not source or not predicate or not destination:
        return None

    claim_type = _claim_type_for_relation(predicate)
    if claim_type == "employment":
        value_json = {
            "subject": source,
            "company": destination,
            "predicate": predicate,
        }
    else:
        value_json = {
            "subject": source,
            "predicate": predicate,
            "object": destination,
        }

    confidence = float(relation.get("confidence", 0.82))
    status = "accepted" if confidence >= threshold else "proposed"
    return {
        "op": "ADD",
        "claim": {
            "claim_id": _stable_claim_id(claim_type, value_json),
            "claim_type": claim_type,
            "value_json": value_json,
            "status": status,
            "sensitive": False,
            "valid_from": None,
            "valid_to": None,
            "confidence": confidence,
            "source_system": "mem0",
            "evidence_refs": evidence_refs,
        },
        "target_claim_id": None,
        "evidence_refs": evidence_refs,
    }


def _claim_from_memory_result(
    result: dict[str, Any],
    *,
    threshold: float,
    evidence_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    memory_text = str(result.get("memory") or result.get("text") or "").strip()
    if not memory_text:
        return None

    event = str(result.get("event", "ADD")).upper()
    op = "ADD"
    status = "proposed"
    if event == "DELETE":
        op = "REJECT"
        status = "rejected"
    elif event == "UPDATE":
        op = "UPDATE"

    confidence = float(result.get("score", 0.78))
    if status == "proposed" and confidence >= threshold:
        status = "accepted"

    value_json = {"label": memory_text}
    return {
        "op": op,
        "claim": {
            "claim_id": _stable_claim_id("topic", value_json),
            "claim_type": "topic",
            "value_json": value_json,
            "status": status,
            "sensitive": False,
            "valid_from": None,
            "valid_to": None,
            "confidence": confidence,
            "source_system": "mem0",
            "evidence_refs": evidence_refs,
        },
        "target_claim_id": result.get("id"),
        "evidence_refs": evidence_refs,
    }


def _ops_from_mem0_outputs(
    add_response: dict[str, Any],
    search_response: dict[str, Any],
    *,
    threshold: float,
    evidence_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    memories: list[dict[str, Any]] = []

    for row in _iter_dicts(add_response) + _iter_dicts(search_response):
        if {"source", "relationship"} <= set(row.keys()) and ("destination" in row or "target" in row):
            relations.append(row)
        if "memory" in row or "event" in row:
            memories.append(row)

    ops: list[dict[str, Any]] = []
    for relation in relations:
        op = _claim_from_relation(relation, threshold=threshold, evidence_refs=evidence_refs)
        if op:
            ops.append(op)

    for memory in memories:
        op = _claim_from_memory_result(memory, threshold=threshold, evidence_refs=evidence_refs)
        if op:
            ops.append(op)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for op in ops:
        claim = op.get("claim", {})
        key = f"{claim.get('claim_type')}::{json.dumps(claim.get('value_json', {}), sort_keys=True, ensure_ascii=True)}::{op.get('op')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(op)
    return deduped


def propose_memory_ops(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Adapter entrypoint used by `services/memory/mem0_client.py`.

    Primary path: Mem0 OSS `Memory.from_config` + `add` + `search`.
    Fallback path: deterministic rules when explicitly enabled.
    """
    settings = get_settings()
    threshold = float(bundle.get("auto_accept_threshold", 0.9))
    evidence_refs = _evidence_refs_from_bundle(bundle)
    user_id, agent_id, run_id = _scope_ids(bundle)
    messages = _compose_messages(bundle)
    query = str(bundle.get("new_interaction_summary", "")).strip() or "relationship updates"
    metadata = {"source_system": "lux_crm", "pipeline": "mem0_oss_adapter"}

    try:
        memory = _memory_instance()
        add_response = memory.add(
            messages=messages,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            metadata=metadata,
        )
        search_response = memory.search(
            query=query,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            limit=max(1, settings.mem0_search_limit),
        )
        ops = _ops_from_mem0_outputs(
            add_response=add_response or {},
            search_response=search_response or {},
            threshold=threshold,
            evidence_refs=evidence_refs,
        )
        if ops:
            return ops
        raise RuntimeError("Mem0 returned no candidate memory operations.")
    except Exception:
        if settings.mem0_enable_rules_fallback:
            return _fallback_ops(bundle)
        raise
