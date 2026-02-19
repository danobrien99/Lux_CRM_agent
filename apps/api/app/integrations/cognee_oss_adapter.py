from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.prompts import render_prompt


def _heuristic(interaction_id: str, text: str) -> dict[str, Any]:
    words = [w.strip(".,:;!?()[]{}\"'") for w in text.split() if len(w) > 3]
    unique = sorted(set(w.lower() for w in words))
    topics = [{"label": item, "confidence": 0.55} for item in unique[:8]]
    entities = [{"name": item.title(), "type": "Topic", "confidence": 0.5} for item in unique[:5]]
    relations = []
    if "joined" in text.lower() or "new role" in text.lower():
        relations.append(
            {
                "subject": "contact",
                "predicate": "employment_change",
                "object": "detected",
                "confidence": 0.91,
                "evidence_spans": [{"start": 0, "end": min(len(text), 180)}],
            }
        )
    return {
        "interaction_id": interaction_id,
        "entities": entities,
        "relations": relations,
        "topics": topics,
        "signature": hashlib.md5(text.encode("utf-8")).hexdigest(),
    }


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


def _import_cognee_module() -> Any:
    settings = get_settings()
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_api_key and not os.getenv("LLM_API_KEY"):
        # Cognee reads provider credentials from its own LLM_* env names.
        os.environ["LLM_API_KEY"] = openai_api_key
    if openai_api_key and not os.getenv("EMBEDDING_API_KEY"):
        os.environ["EMBEDDING_API_KEY"] = openai_api_key
    if settings.llm_provider and not os.getenv("LLM_PROVIDER"):
        os.environ["LLM_PROVIDER"] = settings.llm_provider
    if settings.llm_model and not os.getenv("LLM_MODEL"):
        os.environ["LLM_MODEL"] = settings.llm_model
    if settings.embedding_provider and not os.getenv("EMBEDDING_PROVIDER"):
        os.environ["EMBEDDING_PROVIDER"] = settings.embedding_provider
    if settings.embedding_model and not os.getenv("EMBEDDING_MODEL"):
        os.environ["EMBEDDING_MODEL"] = settings.embedding_model
    if settings.embedding_dim and not os.getenv("EMBEDDING_DIMENSIONS"):
        os.environ["EMBEDDING_DIMENSIONS"] = str(settings.embedding_dim)
    try:
        import cognee  # type: ignore

        return cognee
    except Exception:
        repo_path = Path(settings.cognee_repo_path).expanduser()
        if repo_path.exists():
            sys.path.insert(0, str(repo_path))
        import cognee  # type: ignore

        return cognee


def _resolve_search_type(cognee_module: Any, search_type_name: str) -> Any:
    search_type = search_type_name.strip().upper()
    enum_cls = getattr(cognee_module, "SearchType", None)
    if enum_cls is None:
        return search_type

    try:
        return enum_cls[search_type]
    except Exception:
        try:
            return enum_cls(search_type)
        except Exception:
            return enum_cls.GRAPH_COMPLETION


def _extract_json_blocks(text: str) -> list[Any]:
    parsed: list[Any] = []
    candidate_fragments: list[str] = []
    raw = text.strip()
    if raw:
        candidate_fragments.append(raw)

    for match in re.findall(r"```(?:json)?\s*(.+?)\s*```", text, flags=re.DOTALL | re.IGNORECASE):
        candidate_fragments.append(match.strip())

    first_curly = text.find("{")
    last_curly = text.rfind("}")
    if first_curly >= 0 and last_curly > first_curly:
        candidate_fragments.append(text[first_curly : last_curly + 1].strip())

    first_list = text.find("[")
    last_list = text.rfind("]")
    if first_list >= 0 and last_list > first_list:
        candidate_fragments.append(text[first_list : last_list + 1].strip())

    seen: set[str] = set()
    for fragment in candidate_fragments:
        if not fragment or fragment in seen:
            continue
        seen.add(fragment)
        try:
            parsed.append(json.loads(fragment))
        except Exception:
            continue
    return parsed


def _walk_payload(payload: Any, collector: list[dict[str, Any]]) -> None:
    if isinstance(payload, dict):
        collector.append(payload)
        for value in payload.values():
            _walk_payload(value, collector)
        return

    if isinstance(payload, list):
        for item in payload:
            _walk_payload(item, collector)
        return

    if isinstance(payload, str):
        for decoded in _extract_json_blocks(payload):
            _walk_payload(decoded, collector)


def _candidate_dicts(entry: Any) -> list[dict[str, Any]]:
    if isinstance(entry, dict):
        return [entry]
    if isinstance(entry, str):
        parsed: list[dict[str, Any]] = []
        for decoded in _extract_json_blocks(entry):
            if isinstance(decoded, dict):
                parsed.append(decoded)
            elif isinstance(decoded, list):
                parsed.extend(item for item in decoded if isinstance(item, dict))
        return parsed
    return []


def _normalize_entities(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in objects:
        candidates = []
        if "entities" in item and isinstance(item["entities"], list):
            candidates.extend(item["entities"])
        if {"entity", "entity_type"} <= set(item.keys()):
            candidates.append(item)
        if {"name", "type"} <= set(item.keys()):
            candidates.append(item)

        for entry in candidates:
            parsed_entries = _candidate_dicts(entry)
            if not parsed_entries and isinstance(entry, str) and entry.strip():
                parsed_entries = [{"name": entry.strip(), "type": "Entity", "confidence": 0.65}]

            for parsed in parsed_entries:
                name = str(parsed.get("name") or parsed.get("entity") or "").strip()
                kind = str(parsed.get("type") or parsed.get("entity_type") or "Entity").strip()
                if not name:
                    continue
                key = (name.lower(), kind.lower())
                if key in seen:
                    continue
                seen.add(key)
                entities.append(
                    {
                        "name": name,
                        "type": kind,
                        "confidence": float(parsed.get("confidence", 0.75)),
                    }
                )
    return entities


def _normalize_relations(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in objects:
        candidates = []
        if "relations" in item and isinstance(item["relations"], list):
            candidates.extend(item["relations"])
        if {"subject", "predicate", "object"} <= set(item.keys()):
            candidates.append(item)
        if {"source", "relationship"} <= set(item.keys()) and ("destination" in item or "target" in item):
            candidates.append(item)

        for entry in candidates:
            for parsed in _candidate_dicts(entry):
                subject = str(parsed.get("subject") or parsed.get("source") or "").strip()
                predicate = str(parsed.get("predicate") or parsed.get("relationship") or "").strip()
                obj = str(parsed.get("object") or parsed.get("destination") or parsed.get("target") or "").strip()
                if not subject or not predicate or not obj:
                    continue
                key = (subject.lower(), predicate.lower(), obj.lower())
                if key in seen:
                    continue
                seen.add(key)
                evidence_spans = parsed.get("evidence_spans", [])
                relations.append(
                    {
                        "subject": subject,
                        "predicate": predicate,
                        "object": obj,
                        "confidence": float(parsed.get("confidence", 0.8)),
                        "evidence_spans": evidence_spans if isinstance(evidence_spans, list) else [],
                    }
                )
    return relations


def _normalize_topics(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in objects:
        candidates = []
        if "topics" in item and isinstance(item["topics"], list):
            candidates.extend(item["topics"])
        if "label" in item and len(item) <= 3:
            candidates.append(item)

        for entry in candidates:
            parsed_entries = _candidate_dicts(entry)
            if not parsed_entries and isinstance(entry, str) and entry.strip():
                parsed_entries = [{"label": entry.strip(), "confidence": 0.65}]

            for parsed in parsed_entries:
                label = str(parsed.get("label") or parsed.get("topic") or "").strip()
                if not label:
                    continue
                normalized = label.lower()
                if normalized in seen:
                    continue
                seen.add(normalized)
                topics.append(
                    {
                        "label": label,
                        "confidence": float(parsed.get("confidence", 0.75)),
                    }
                )
    return topics


def _normalize_search_results(interaction_id: str, search_results: Any) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    _walk_payload(search_results, objects)

    return {
        "interaction_id": interaction_id,
        "entities": _normalize_entities(objects),
        "relations": _normalize_relations(objects),
        "topics": _normalize_topics(objects),
        "signature": hashlib.md5(str(search_results).encode("utf-8")).hexdigest(),
    }


async def _safe_add(cognee_module: Any, payload: str, dataset_name: str) -> Any:
    try:
        return await cognee_module.add(data=payload, dataset_name=dataset_name)
    except TypeError:
        return await cognee_module.add(payload)


async def _safe_cognify(cognee_module: Any, dataset_name: str) -> Any:
    try:
        return await cognee_module.cognify(datasets=[dataset_name])
    except TypeError:
        return await cognee_module.cognify()


async def _safe_search(cognee_module: Any, query: str, search_type: Any, dataset_name: str, top_k: int) -> Any:
    try:
        return await cognee_module.search(
            query_text=query,
            query_type=search_type,
            datasets=[dataset_name],
            top_k=top_k,
            save_interaction=False,
        )
    except TypeError:
        return await cognee_module.search(query, query_type=search_type)


async def _extract_with_cognee(interaction_id: str, text: str) -> dict[str, Any]:
    settings = get_settings()
    cognee_module = _import_cognee_module()
    dataset_name = settings.cognee_dataset_name.strip() or "lux_crm"
    search_type = _resolve_search_type(cognee_module, settings.cognee_search_type)

    ingestion_payload = json.dumps(
        {
            "interaction_id": interaction_id,
            "text": text,
        },
        ensure_ascii=True,
    )

    await _safe_add(cognee_module, ingestion_payload, dataset_name)
    await _safe_cognify(cognee_module, dataset_name)

    query = render_prompt(
        "cognee_extraction_query",
        interaction_id=interaction_id,
        interaction_text=text,
    )
    search_results = await _safe_search(
        cognee_module,
        query=query,
        search_type=search_type,
        dataset_name=dataset_name,
        top_k=max(1, settings.cognee_search_top_k),
    )
    normalized = _normalize_search_results(interaction_id, search_results)
    if normalized["entities"] or normalized["relations"] or normalized["topics"]:
        return normalized

    chunk_search_type = _resolve_search_type(cognee_module, "CHUNKS")
    chunk_results = await _safe_search(
        cognee_module,
        query=query,
        search_type=chunk_search_type,
        dataset_name=dataset_name,
        top_k=max(1, settings.cognee_search_top_k),
    )
    normalized = _normalize_search_results(interaction_id, chunk_results)
    if normalized["entities"] or normalized["relations"] or normalized["topics"]:
        return normalized

    raise RuntimeError("Cognee returned no extractable entities, relations, or topics.")


def extract_candidates(interaction_id: str, text: str) -> dict[str, Any]:
    """
    Adapter entrypoint used by `services/extraction/cognee_client.py`.

    Primary path: real Cognee pipeline `add -> cognify -> search`.
    Fallback path: deterministic heuristic extractor when explicitly enabled.
    """
    settings = get_settings()
    try:
        return _run_async(_extract_with_cognee(interaction_id=interaction_id, text=text))
    except Exception:
        if settings.cognee_enable_heuristic_fallback:
            return _heuristic(interaction_id, text)
        raise
