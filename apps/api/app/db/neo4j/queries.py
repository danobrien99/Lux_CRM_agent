from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.neo4j.driver import neo4j_session


def merge_contact(contact: dict[str, Any]) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        session.run(
            """
            MERGE (c:Contact {contact_id: $contact_id})
            SET c.primary_email = $primary_email,
                c.display_name = $display_name,
                c.owner_user_id = $owner_user_id
            """,
            **contact,
        )


def merge_interaction(interaction: dict[str, Any]) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        session.run(
            """
            MERGE (i:Interaction {interaction_id: $interaction_id})
            SET i.type = $type,
                i.timestamp = datetime($timestamp),
                i.source_system = $source_system,
                i.direction = $direction
            """,
            **interaction,
        )


def attach_contact_interaction(contact_id: str, interaction_id: str) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})
            MATCH (i:Interaction {interaction_id: $interaction_id})
            MERGE (c)-[:PARTICIPATED_IN]->(i)
            """,
            contact_id=contact_id,
            interaction_id=interaction_id,
        )


def create_claim_with_evidence(
    contact_id: str,
    interaction_id: str,
    claim: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
) -> None:
    now_iso = datetime.utcnow().isoformat()
    with neo4j_session() as session:
        if session is None:
            return
        session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})
            MATCH (i:Interaction {interaction_id: $interaction_id})
            MERGE (cl:Claim {claim_id: $claim_id})
            SET cl.claim_type = $claim_type,
                cl.value_json = $value_json,
                cl.status = $status,
                cl.sensitive = $sensitive,
                cl.valid_from = $valid_from,
                cl.valid_to = $valid_to,
                cl.confidence = $confidence,
                cl.created_at = datetime($created_at),
                cl.source_system = $source_system
            MERGE (i)-[:HAS_CLAIM]->(cl)
            MERGE (c)-[:HAS_CLAIM]->(cl)
            """,
            contact_id=contact_id,
            interaction_id=interaction_id,
            created_at=now_iso,
            **claim,
        )
        for ref in evidence_refs:
            session.run(
                """
                MATCH (cl:Claim {claim_id: $claim_id})
                MERGE (e:Evidence {evidence_id: $evidence_id})
                SET e.interaction_id = $interaction_id,
                    e.chunk_id = $chunk_id,
                    e.span_json = $span_json,
                    e.quote_hash = $quote_hash
                MERGE (cl)-[:SUPPORTED_BY]->(e)
                """,
                claim_id=claim["claim_id"],
                evidence_id=ref["evidence_id"],
                interaction_id=ref["interaction_id"],
                chunk_id=ref["chunk_id"],
                span_json=ref.get("span_json", {}),
                quote_hash=ref.get("quote_hash", ""),
            )


def upsert_score_snapshot(contact_id: str, asof: str, relationship_score: float, priority_score: float, components_json: dict[str, Any]) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})
            MERGE (s:ScoreSnapshot {contact_id: $contact_id, asof: $asof})
            SET s.relationship_score = $relationship_score,
                s.priority_score = $priority_score,
                s.components_json = $components_json
            MERGE (c)-[:HAS_SCORE]->(s)
            """,
            contact_id=contact_id,
            asof=asof,
            relationship_score=relationship_score,
            priority_score=priority_score,
            components_json=components_json,
        )


def get_contact_claims(contact_id: str, status: str | None = None) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return []

        if status:
            rows = session.run(
                """
                MATCH (c:Contact {contact_id: $contact_id})-[:HAS_CLAIM]->(cl:Claim)
                WHERE cl.status = $status
                RETURN cl.claim_id AS claim_id,
                       cl.claim_type AS claim_type,
                       cl.value_json AS value_json,
                       cl.status AS status,
                       cl.sensitive AS sensitive,
                       cl.valid_from AS valid_from,
                       cl.valid_to AS valid_to,
                       cl.confidence AS confidence,
                       cl.source_system AS source_system
                ORDER BY cl.created_at DESC
                """,
                contact_id=contact_id,
                status=status,
            ).data()
        else:
            rows = session.run(
                """
                MATCH (c:Contact {contact_id: $contact_id})-[:HAS_CLAIM]->(cl:Claim)
                RETURN cl.claim_id AS claim_id,
                       cl.claim_type AS claim_type,
                       cl.value_json AS value_json,
                       cl.status AS status,
                       cl.sensitive AS sensitive,
                       cl.valid_from AS valid_from,
                       cl.valid_to AS valid_to,
                       cl.confidence AS confidence,
                       cl.source_system AS source_system
                ORDER BY cl.created_at DESC
                """,
                contact_id=contact_id,
            ).data()

    claims: list[dict[str, Any]] = []
    for row in rows:
        claims.append(
            {
                "claim_id": row.get("claim_id"),
                "claim_type": row.get("claim_type"),
                "value_json": row.get("value_json") or {},
                "status": row.get("status"),
                "sensitive": bool(row.get("sensitive", False)),
                "valid_from": row.get("valid_from"),
                "valid_to": row.get("valid_to"),
                "confidence": float(row.get("confidence", 0.0)),
                "source_system": row.get("source_system") or "mem0",
            }
        )
    return claims


def get_claim_by_id(claim_id: str) -> dict[str, Any] | None:
    with neo4j_session() as session:
        if session is None:
            return None
        rows = session.run(
            """
            MATCH (cl:Claim {claim_id: $claim_id})
            OPTIONAL MATCH (c:Contact)-[:HAS_CLAIM]->(cl)
            RETURN cl.claim_id AS claim_id,
                   cl.claim_type AS claim_type,
                   cl.value_json AS value_json,
                   cl.status AS status,
                   cl.sensitive AS sensitive,
                   cl.valid_from AS valid_from,
                   cl.valid_to AS valid_to,
                   cl.confidence AS confidence,
                   cl.source_system AS source_system,
                   c.contact_id AS contact_id
            LIMIT 1
            """,
            claim_id=claim_id,
        ).data()

    if not rows:
        return None
    row = rows[0]
    return {
        "claim_id": row.get("claim_id"),
        "claim_type": row.get("claim_type"),
        "value_json": row.get("value_json") or {},
        "status": row.get("status"),
        "sensitive": bool(row.get("sensitive", False)),
        "valid_from": row.get("valid_from"),
        "valid_to": row.get("valid_to"),
        "confidence": float(row.get("confidence", 0.0)),
        "source_system": row.get("source_system") or "mem0",
        "contact_id": row.get("contact_id"),
    }


def update_claim_status(
    claim_id: str,
    status: str,
    *,
    value_json: dict[str, Any] | None = None,
    resolved_at_iso: str | None = None,
) -> None:
    with neo4j_session() as session:
        if session is None:
            return

        session.run(
            """
            MATCH (cl:Claim {claim_id: $claim_id})
            SET cl.status = $status,
                cl.resolved_at = CASE
                    WHEN $resolved_at IS NULL THEN cl.resolved_at
                    ELSE datetime($resolved_at)
                END,
                cl.value_json = CASE
                    WHEN $value_json IS NULL THEN cl.value_json
                    ELSE $value_json
                END
            """,
            claim_id=claim_id,
            status=status,
            value_json=value_json,
            resolved_at=resolved_at_iso,
        )


def set_current_employer(contact_id: str, company_name: str, claim_id: str, resolved_at_iso: str) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})
            OPTIONAL MATCH (c)-[existing:CURRENT_EMPLOYER]->(:Company)
            DELETE existing
            MERGE (co:Company {name: $company_name})
            MERGE (c)-[rel:CURRENT_EMPLOYER]->(co)
            SET rel.claim_id = $claim_id,
                rel.updated_at = datetime($resolved_at)
            """,
            contact_id=contact_id,
            company_name=company_name,
            claim_id=claim_id,
            resolved_at=resolved_at_iso,
        )
