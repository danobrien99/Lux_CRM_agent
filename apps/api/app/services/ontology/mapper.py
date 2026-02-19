from __future__ import annotations

import copy
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_ONTOLOGY: dict[str, Any] = {
    "version": "1.0",
    "description": "Default Lux ontology used for canonical claim and relation mapping.",
    "predicate_aliases": {
        "employment_change": "works_at",
        "employed_by": "works_at",
        "current_employer": "works_at",
        "works_for": "works_at",
        "talked_about": "discussed_topic",
        "discussion": "discussed_topic",
        "discussed": "discussed_topic",
        "topic": "discussed_topic",
        "child_attends": "has_education_detail",
        "school": "has_education_detail",
        "family_detail": "has_family_detail",
        "interest": "has_preference",
        "preference": "has_preference",
        "goal": "has_opportunity",
        "opportunity": "has_opportunity",
    },
    "predicate_claim_type": {
        "works_at": "employment",
        "discussed_topic": "topic",
        "related_to": "topic",
        "has_personal_detail": "personal_detail",
        "has_preference": "preference",
        "committed_to": "commitment",
        "has_opportunity": "opportunity",
        "located_in": "location",
        "has_family_detail": "family",
        "has_education_detail": "education",
    },
    "claim_types": {
        "topic": {
            "default_predicate": "discussed_topic",
            "subject_kind": "Contact",
            "object_kind": "Topic",
            "sensitive": False,
            "high_value": False,
        },
        "employment": {
            "default_predicate": "works_at",
            "subject_kind": "Contact",
            "object_kind": "Company",
            "sensitive": False,
            "high_value": True,
        },
        "personal_detail": {
            "default_predicate": "has_personal_detail",
            "subject_kind": "Contact",
            "object_kind": "PersonalDetail",
            "sensitive": True,
            "high_value": True,
        },
        "preference": {
            "default_predicate": "has_preference",
            "subject_kind": "Contact",
            "object_kind": "Preference",
            "sensitive": False,
            "high_value": True,
        },
        "commitment": {
            "default_predicate": "committed_to",
            "subject_kind": "Contact",
            "object_kind": "Commitment",
            "sensitive": False,
            "high_value": True,
        },
        "opportunity": {
            "default_predicate": "has_opportunity",
            "subject_kind": "Contact",
            "object_kind": "Opportunity",
            "sensitive": False,
            "high_value": True,
        },
        "location": {
            "default_predicate": "located_in",
            "subject_kind": "Contact",
            "object_kind": "Location",
            "sensitive": False,
            "high_value": False,
        },
        "family": {
            "default_predicate": "has_family_detail",
            "subject_kind": "Contact",
            "object_kind": "FamilyMember",
            "sensitive": True,
            "high_value": True,
        },
        "education": {
            "default_predicate": "has_education_detail",
            "subject_kind": "Contact",
            "object_kind": "Institution",
            "sensitive": False,
            "high_value": False,
        },
    },
    "high_value_predicates": [
        "works_at",
        "committed_to",
        "has_opportunity",
        "has_preference",
        "has_personal_detail",
        "has_family_detail",
    ],
}


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _normalize_token(value: object) -> str:
    text = _normalize_text(value).lower()
    if not text:
        return ""
    return text.replace("-", "_").replace(" ", "_")


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _default_ontology_path() -> Path:
    return Path(__file__).resolve().parent / "ontology_config.json"


def _resolve_ontology_path(path_value: str) -> Path:
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    except Exception:
        logger.exception("ontology_config_parse_failed", extra={"path": str(path)})
        return None
    if not isinstance(payload, dict):
        logger.warning("ontology_config_invalid_type", extra={"path": str(path)})
        return None
    return payload


@lru_cache(maxsize=1)
def load_ontology_config() -> dict[str, Any]:
    settings = get_settings()
    configured_path = _resolve_ontology_path(settings.ontology_config_path)
    default_path = _default_ontology_path()

    payload = _read_json(configured_path)
    if payload is None and configured_path != default_path:
        payload = _read_json(default_path)
    if payload is None:
        payload = {}

    return _deep_merge_dict(_DEFAULT_ONTOLOGY, payload)


def clear_ontology_cache() -> None:
    load_ontology_config.cache_clear()


def _claim_type_config(claim_type: str) -> dict[str, Any]:
    config = load_ontology_config()
    claim_types = config.get("claim_types")
    if not isinstance(claim_types, dict):
        return _DEFAULT_ONTOLOGY["claim_types"]["topic"]

    normalized_claim_type = _normalize_token(claim_type)
    claim_config = claim_types.get(normalized_claim_type)
    if isinstance(claim_config, dict):
        return claim_config
    topic_config = claim_types.get("topic")
    if isinstance(topic_config, dict):
        return topic_config
    return _DEFAULT_ONTOLOGY["claim_types"]["topic"]


def canonicalize_predicate(predicate: str | None) -> str:
    normalized = _normalize_token(predicate)
    if not normalized:
        return ""

    config = load_ontology_config()
    aliases = config.get("predicate_aliases")
    if not isinstance(aliases, dict):
        return normalized

    normalized_aliases = {_normalize_token(key): _normalize_token(value) for key, value in aliases.items()}
    return normalized_aliases.get(normalized, normalized)


def claim_type_for_predicate(predicate: str | None, fallback: str = "topic") -> str:
    canonical = canonicalize_predicate(predicate)
    config = load_ontology_config()
    mapping = config.get("predicate_claim_type")
    claim_types = config.get("claim_types")

    if isinstance(mapping, dict):
        normalized_mapping = {_normalize_token(key): _normalize_token(value) for key, value in mapping.items()}
        mapped = normalized_mapping.get(canonical)
        if mapped and isinstance(claim_types, dict) and mapped in claim_types:
            return mapped

    normalized_fallback = _normalize_token(fallback) or "topic"
    if isinstance(claim_types, dict) and normalized_fallback in claim_types:
        return normalized_fallback
    return "topic"


def _is_high_value(claim_type: str, predicate: str) -> bool:
    config = load_ontology_config()
    high_value_predicates = config.get("high_value_predicates")
    high_value_set = {
        _normalize_token(item) for item in high_value_predicates if isinstance(item, str) and _normalize_token(item)
    } if isinstance(high_value_predicates, list) else set()
    if canonicalize_predicate(predicate) in high_value_set:
        return True
    claim_config = _claim_type_config(claim_type)
    return bool(claim_config.get("high_value", False))


def map_relation_to_claim(
    relation: dict[str, Any],
    *,
    source_system: str,
    default_confidence: float = 0.5,
) -> dict[str, Any] | None:
    if not isinstance(relation, dict):
        return None

    subject = _normalize_text(relation.get("subject") or relation.get("source")) or "contact"
    object_name = _normalize_text(relation.get("object") or relation.get("destination") or relation.get("target"))
    if not object_name:
        return None

    predicate = canonicalize_predicate(
        _normalize_text(relation.get("predicate") or relation.get("relationship"))
    )

    requested_claim_type = _normalize_token(relation.get("claim_type"))
    claim_type = requested_claim_type or claim_type_for_predicate(predicate, fallback="topic")
    claim_config = _claim_type_config(claim_type)
    if not predicate:
        predicate = _normalize_token(claim_config.get("default_predicate")) or "related_to"

    value_json: dict[str, Any] = {
        "subject": subject,
        "predicate": predicate,
        "object": object_name,
    }
    if claim_type == "employment":
        value_json["company"] = object_name

    subject_type = _normalize_text(relation.get("subject_type"))
    object_type = _normalize_text(relation.get("object_type"))
    if subject_type:
        value_json["subject_type"] = subject_type
    if object_type:
        value_json["object_type"] = object_type

    evidence_spans = relation.get("evidence_spans")
    if isinstance(evidence_spans, list):
        value_json["evidence_spans"] = evidence_spans

    status = _normalize_token(relation.get("status")) or "proposed"
    if status not in {"proposed", "accepted", "rejected", "superseded"}:
        status = "proposed"

    claim_id = _normalize_text(relation.get("claim_id")) or str(uuid4())
    confidence = _as_float(relation.get("confidence"), default_confidence)
    sensitive = relation.get("sensitive")
    if not isinstance(sensitive, bool):
        sensitive = bool(claim_config.get("sensitive", False))

    return {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "value_json": value_json,
        "status": status,
        "sensitive": sensitive,
        "valid_from": None,
        "valid_to": None,
        "confidence": confidence,
        "source_system": source_system,
    }


def map_topic_to_claim(topic: dict[str, Any], *, source_system: str = "cognee") -> dict[str, Any] | None:
    if not isinstance(topic, dict):
        return None
    label = _normalize_text(topic.get("label") or topic.get("topic"))
    if not label:
        return None

    claim_type = "topic"
    claim_config = _claim_type_config(claim_type)
    predicate = _normalize_token(claim_config.get("default_predicate")) or "discussed_topic"
    confidence = _as_float(topic.get("confidence"), 0.5)
    return {
        "claim_id": str(uuid4()),
        "claim_type": claim_type,
        "value_json": {
            "label": label,
            "subject": "contact",
            "predicate": predicate,
            "object": label,
        },
        "status": "proposed",
        "sensitive": bool(claim_config.get("sensitive", False)),
        "valid_from": None,
        "valid_to": None,
        "confidence": confidence,
        "source_system": source_system,
    }


def relation_payload_from_claim(claim: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(claim, dict):
        return None
    value_json = claim.get("value_json")
    if not isinstance(value_json, dict):
        return None

    claim_type = _normalize_token(claim.get("claim_type")) or "topic"
    claim_config = _claim_type_config(claim_type)
    subject = _normalize_text(value_json.get("subject")) or "contact"

    predicate = canonicalize_predicate(_normalize_text(value_json.get("predicate")))
    if not predicate:
        predicate = _normalize_token(claim_config.get("default_predicate")) or "related_to"

    object_name = (
        _normalize_text(value_json.get("object"))
        or _normalize_text(value_json.get("company"))
        or _normalize_text(value_json.get("destination"))
        or _normalize_text(value_json.get("target"))
        or _normalize_text(value_json.get("label"))
    )
    if not object_name:
        return None
    if object_name.lower() == subject.lower():
        return None

    subject_kind = _normalize_text(value_json.get("subject_type")) or _normalize_text(claim_config.get("subject_kind"))
    object_kind = _normalize_text(value_json.get("object_type")) or _normalize_text(claim_config.get("object_kind"))
    if not subject_kind:
        subject_kind = "Contact" if subject.lower() == "contact" else "Entity"
    if not object_kind:
        object_kind = "Entity"

    return {
        "subject_name": subject,
        "predicate": predicate,
        "object_name": object_name,
        "subject_kind": subject_kind,
        "object_kind": object_kind,
        "high_value": _is_high_value(claim_type=claim_type, predicate=predicate),
    }
