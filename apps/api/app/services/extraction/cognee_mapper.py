from __future__ import annotations

import uuid
from typing import Any

from app.db.neo4j.queries import create_claim_with_evidence


def candidates_to_claims(candidates: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for rel in candidates.get("relations", []):
        claim_type = "employment" if rel.get("predicate") == "employment_change" else "topic"
        claims.append(
            {
                "claim_id": str(uuid.uuid4()),
                "claim_type": claim_type,
                "value_json": {"subject": rel.get("subject"), "object": rel.get("object")},
                "status": "proposed",
                "sensitive": False,
                "valid_from": None,
                "valid_to": None,
                "confidence": float(rel.get("confidence", 0.5)),
                "source_system": "cognee",
            }
        )

    for topic in candidates.get("topics", [])[:5]:
        claims.append(
            {
                "claim_id": str(uuid.uuid4()),
                "claim_type": "topic",
                "value_json": {"label": topic.get("label")},
                "status": "proposed",
                "sensitive": False,
                "valid_from": None,
                "valid_to": None,
                "confidence": float(topic.get("confidence", 0.5)),
                "source_system": "cognee",
            }
        )
    return claims


def write_claims_with_evidence(contact_id: str, interaction_id: str, claims: list[dict], evidence_refs: list[dict]) -> None:
    for claim in claims:
        claim_evidence = []
        for ref in evidence_refs:
            claim_evidence.append(
                {
                    "evidence_id": str(uuid.uuid4()),
                    "interaction_id": interaction_id,
                    "chunk_id": ref["chunk_id"],
                    "span_json": ref.get("span_json", {}),
                    "quote_hash": ref.get("quote_hash", ""),
                }
            )
        create_claim_with_evidence(contact_id, interaction_id, claim, claim_evidence)
