from __future__ import annotations

import json
import uuid
from typing import Any

from app.db.neo4j.queries import create_claim_with_evidence
from app.services.ontology import map_relation_to_claim, map_topic_to_claim


_ENTITY_TYPE_TO_CLAIM_TYPE: dict[str, str] = {
    "opportunity": "opportunity",
    "deal": "opportunity",
    "pipeline": "opportunity",
    "preference": "preference",
    "interest": "preference",
    "motivator": "preference",
    "driver": "preference",
    "commitment": "commitment",
    "promise": "commitment",
    "action_item": "commitment",
    "personal_detail": "personal_detail",
    "family": "family",
    "education": "education",
    "school": "education",
    "location": "location",
    "geography": "location",
    "city": "location",
    "region": "location",
    "country": "location",
    "industry": "topic",
    "company": "topic",
    "organization": "topic",
    "business_context": "topic",
    "title": "topic",
    "role": "topic",
    "technology": "topic",
    "tech_stack": "topic",
    "risk": "topic",
    "need": "topic",
    "pain_point": "topic",
    "use_case": "topic",
    "competitor": "topic",
}

_CLAIM_DEFAULT_PREDICATES: dict[str, str] = {
    "topic": "discussed_topic",
    "opportunity": "has_opportunity",
    "preference": "has_preference",
    "commitment": "committed_to",
    "personal_detail": "has_personal_detail",
    "family": "has_family_detail",
    "education": "has_education_detail",
    "location": "located_in",
}


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _normalized_token(value: object) -> str:
    return _normalized_text(value).lower().replace("-", "_").replace(" ", "_")


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _claim_fingerprint(claim: dict[str, Any]) -> str:
    claim_type = _normalized_text(claim.get("claim_type")).lower() or "topic"
    value_json = claim.get("value_json")
    payload = dict(value_json) if isinstance(value_json, dict) else {}
    if claim_type == "topic":
        for key in ("label", "object"):
            value = payload.get(key)
            if isinstance(value, str):
                payload[key] = _normalized_text(value).casefold()
    try:
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    except Exception:
        serialized = str(payload)
    return f"{claim_type}:{serialized}"


def _entity_label(entity: dict[str, Any]) -> str:
    for key in ("name", "label", "text", "value", "title", "entity"):
        value = _normalized_text(entity.get(key))
        if value:
            return value
    return ""


def _entity_claim_type(entity_type: str) -> str:
    if entity_type in _ENTITY_TYPE_TO_CLAIM_TYPE:
        return _ENTITY_TYPE_TO_CLAIM_TYPE[entity_type]
    if "opportun" in entity_type or "deal" in entity_type:
        return "opportunity"
    if "prefer" in entity_type or "motiv" in entity_type or "driver" in entity_type:
        return "preference"
    if "commit" in entity_type or "action" in entity_type:
        return "commitment"
    if "family" in entity_type:
        return "family"
    if "educat" in entity_type or "school" in entity_type:
        return "education"
    if "location" in entity_type or "geo" in entity_type or "region" in entity_type:
        return "location"
    if "personal" in entity_type or "bio" in entity_type:
        return "personal_detail"
    return "topic"


def _entity_to_claim(entity: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entity, dict):
        return None
    label = _entity_label(entity)
    if not label:
        return None

    entity_type = _normalized_token(entity.get("type") or entity.get("kind") or entity.get("category"))
    claim_type = _entity_claim_type(entity_type)
    if claim_type == "topic":
        return map_topic_to_claim(
            {
                "label": label,
                "confidence": _as_float(entity.get("confidence"), 0.45),
            },
            source_system="cognee",
        )

    predicate = _CLAIM_DEFAULT_PREDICATES.get(claim_type, "related_to")
    confidence = max(0.0, min(1.0, _as_float(entity.get("confidence"), 0.45)))
    mapped = map_relation_to_claim(
        {
            "subject": "contact",
            "predicate": predicate,
            "object": label,
            "claim_type": claim_type,
            "confidence": confidence,
            "sensitive": claim_type in {"personal_detail", "family"},
        },
        source_system="cognee",
        default_confidence=confidence,
    )
    if mapped is None:
        return None
    value_json = mapped.get("value_json")
    if isinstance(value_json, dict) and entity_type:
        value_json["object_type"] = entity_type
    if not mapped.get("claim_id"):
        mapped["claim_id"] = str(uuid.uuid4())
    return mapped


def _dedupe_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    for claim in claims:
        fingerprint = _claim_fingerprint(claim)
        if fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        deduped.append(claim)
    return deduped


def candidates_to_claims(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for rel in candidates.get("relations", []):
        mapped = map_relation_to_claim(rel, source_system="cognee", default_confidence=0.5)
        if mapped is None:
            continue
        if not mapped.get("claim_id"):
            mapped["claim_id"] = str(uuid.uuid4())
        claims.append(mapped)

    for entity in candidates.get("entities", [])[:24]:
        mapped = _entity_to_claim(entity)
        if mapped is None:
            continue
        claims.append(mapped)

    for topic in candidates.get("topics", [])[:5]:
        mapped = map_topic_to_claim(topic, source_system="cognee")
        if mapped is None:
            continue
        claims.append(mapped)
    return _dedupe_claims(claims)


def write_claims_with_evidence(contact_id: str, interaction_id: str, claims: list[dict], evidence_refs: list[dict]) -> None:
    for claim in claims:
        source_evidence_refs = claim.get("evidence_refs") if isinstance(claim.get("evidence_refs"), list) else evidence_refs
        claim_evidence = []
        for ref in source_evidence_refs:
            if not isinstance(ref, dict):
                continue
            chunk_id = ref.get("chunk_id")
            if not chunk_id:
                continue
            claim_evidence.append(
                {
                    "evidence_id": str(uuid.uuid4()),
                    "interaction_id": ref.get("interaction_id") or interaction_id,
                    "chunk_id": chunk_id,
                    "span_json": ref.get("span_json", {}),
                    "quote_hash": ref.get("quote_hash", ""),
                }
            )
        if not claim_evidence:
            continue
        create_claim_with_evidence(contact_id, interaction_id, claim, claim_evidence)
