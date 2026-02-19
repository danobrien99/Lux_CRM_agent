from __future__ import annotations

import uuid
from typing import Any

from app.db.neo4j.queries import create_claim_with_evidence
from app.services.ontology import map_relation_to_claim, map_topic_to_claim


def candidates_to_claims(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for rel in candidates.get("relations", []):
        mapped = map_relation_to_claim(rel, source_system="cognee", default_confidence=0.5)
        if mapped is None:
            continue
        if not mapped.get("claim_id"):
            mapped["claim_id"] = str(uuid.uuid4())
        claims.append(mapped)

    for topic in candidates.get("topics", [])[:5]:
        mapped = map_topic_to_claim(topic, source_system="cognee")
        if mapped is None:
            continue
        claims.append(mapped)
    return claims


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
        create_claim_with_evidence(contact_id, interaction_id, claim, claim_evidence)
