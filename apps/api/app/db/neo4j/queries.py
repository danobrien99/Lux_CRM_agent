from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.neo4j.driver import neo4j_session


_CONTACT_RELATION_ALIASES = {
    "contact",
    "this contact",
    "recipient",
    "prospect",
    "lead",
    "person",
}
_STOPWORDS = {
    "and",
    "the",
    "with",
    "from",
    "that",
    "this",
    "for",
    "your",
    "about",
    "into",
    "their",
    "have",
    "been",
    "will",
    "were",
    "there",
    "they",
    "them",
    "then",
    "than",
}


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _normalize_key(value: Any) -> str:
    text = _normalize_text(value).lower()
    return re.sub(r"\s+", " ", text).strip()


def _normalize_predicate(value: Any) -> str:
    text = _normalize_key(value)
    if not text:
        return "related_to"
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return normalized or "related_to"


def _contact_entity_id(contact_id: str) -> str:
    return f"contact:{contact_id}"


def _stable_entity_id(name: str, kind: str) -> str:
    payload = f"entity:{kind.lower()}:{_normalize_key(name)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def _relation_id(
    *,
    contact_id: str,
    interaction_id: str,
    claim_id: str | None,
    subject_name: str,
    predicate_norm: str,
    object_name: str,
) -> str:
    if isinstance(claim_id, str) and claim_id.strip():
        return f"claim:{claim_id.strip()}"
    payload = f"{contact_id}:{interaction_id}:{_normalize_key(subject_name)}:{predicate_norm}:{_normalize_key(object_name)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def _extract_keywords(text: str | None, max_keywords: int = 8) -> list[str]:
    if not isinstance(text, str):
        return []
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    keywords: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen or token in _STOPWORDS:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= max_keywords:
            break
    return keywords


def _is_contact_alias(value: str, *, contact_email: str | None = None, contact_display_name: str | None = None) -> bool:
    normalized = _normalize_key(value)
    if not normalized:
        return False
    aliases = set(_CONTACT_RELATION_ALIASES)
    if contact_email:
        aliases.add(_normalize_key(contact_email))
    if contact_display_name:
        aliases.add(_normalize_key(contact_display_name))
    return normalized in aliases


def _build_path_text(node_names: list[str], predicates: list[str]) -> str:
    if not node_names:
        return ""
    if len(node_names) == 1 or not predicates:
        return node_names[0]
    parts = [node_names[0]]
    for idx, predicate in enumerate(predicates):
        if idx + 1 >= len(node_names):
            break
        parts.append(f"-[{predicate}]->")
        parts.append(node_names[idx + 1])
    return " ".join(part for part in parts if part)


def merge_contact(contact: dict[str, Any]) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        session.run(
            """
            MERGE (c:Contact {contact_id: $contact_id})
            SET c.primary_email = $primary_email,
                c.display_name = $display_name,
                c.first_name = $first_name,
                c.last_name = $last_name,
                c.company = $company,
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


def upsert_contact_as_entity(contact_id: str) -> dict[str, str]:
    with neo4j_session() as session:
        if session is None:
            return {
                "entity_id": _contact_entity_id(contact_id),
                "display_name": contact_id,
                "primary_email": "",
            }
        rows = session.run(
            """
            MERGE (c:Contact {contact_id: $contact_id})
            SET c.display_name = coalesce(c.display_name, $fallback_display_name)
            MERGE (e:Entity {entity_id: $entity_id})
            SET e.name = coalesce(c.display_name, c.primary_email, c.contact_id),
                e.normalized_name = toLower(coalesce(c.display_name, c.primary_email, c.contact_id)),
                e.kind = "Contact",
                e.contact_id = c.contact_id,
                e.updated_at = datetime($updated_at)
            MERGE (c)-[:AS_ENTITY]->(e)
            RETURN coalesce(c.display_name, c.contact_id) AS display_name,
                   coalesce(c.primary_email, "") AS primary_email
            """,
            contact_id=contact_id,
            entity_id=_contact_entity_id(contact_id),
            fallback_display_name=contact_id,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ).data()
    row = rows[0] if rows else {}
    return {
        "entity_id": _contact_entity_id(contact_id),
        "display_name": _normalize_text(row.get("display_name")) or contact_id,
        "primary_email": _normalize_text(row.get("primary_email")),
    }


def upsert_relation_triple(
    *,
    contact_id: str,
    interaction_id: str,
    interaction_timestamp_iso: str | None,
    subject_name: str,
    predicate: str,
    object_name: str,
    claim_id: str | None,
    confidence: float,
    status: str,
    source_system: str,
    uncertain: bool,
    evidence_refs: list[dict[str, Any]] | None = None,
    subject_kind: str | None = None,
    object_kind: str | None = None,
) -> dict[str, Any]:
    contact_entity = upsert_contact_as_entity(contact_id)
    display_name = contact_entity.get("display_name") or contact_id
    primary_email = contact_entity.get("primary_email") or ""

    subject_clean = _normalize_text(subject_name) or "contact"
    object_clean = _normalize_text(object_name)
    if not object_clean:
        return {"upserted": False}

    predicate_clean = _normalize_text(predicate) or "related_to"
    predicate_norm = _normalize_predicate(predicate_clean)

    subject_is_contact = _is_contact_alias(
        subject_clean,
        contact_email=primary_email,
        contact_display_name=display_name,
    )
    object_is_contact = _is_contact_alias(
        object_clean,
        contact_email=primary_email,
        contact_display_name=display_name,
    )

    if subject_is_contact:
        subject_entity_id = _contact_entity_id(contact_id)
        resolved_subject_name = display_name
        resolved_subject_kind = "Contact"
    else:
        resolved_subject_name = subject_clean
        resolved_subject_kind = _normalize_text(subject_kind) or "Entity"
        subject_entity_id = _stable_entity_id(resolved_subject_name, resolved_subject_kind)

    if object_is_contact:
        object_entity_id = _contact_entity_id(contact_id)
        resolved_object_name = display_name
        resolved_object_kind = "Contact"
    else:
        resolved_object_name = object_clean
        resolved_object_kind = _normalize_text(object_kind) or (
            "Company" if predicate_norm in {"works_at", "employment_change", "employed_by"} else "Entity"
        )
        object_entity_id = _stable_entity_id(resolved_object_name, resolved_object_kind)

    relation_id = _relation_id(
        contact_id=contact_id,
        interaction_id=interaction_id,
        claim_id=claim_id,
        subject_name=resolved_subject_name,
        predicate_norm=predicate_norm,
        object_name=resolved_object_name,
    )
    seen_at = interaction_timestamp_iso or datetime.now(timezone.utc).isoformat()
    evidence_json = json.dumps(evidence_refs or [], ensure_ascii=True, separators=(",", ":"))

    with neo4j_session() as session:
        if session is None:
            return {"upserted": False}

        if not subject_is_contact:
            session.run(
                """
                MERGE (s:Entity {entity_id: $entity_id})
                SET s.name = $name,
                    s.normalized_name = $normalized_name,
                    s.kind = $kind,
                    s.updated_at = datetime($updated_at)
                """,
                entity_id=subject_entity_id,
                name=resolved_subject_name,
                normalized_name=_normalize_key(resolved_subject_name),
                kind=resolved_subject_kind,
                updated_at=seen_at,
            )

        if not object_is_contact:
            session.run(
                """
                MERGE (o:Entity {entity_id: $entity_id})
                SET o.name = $name,
                    o.normalized_name = $normalized_name,
                    o.kind = $kind,
                    o.updated_at = datetime($updated_at)
                """,
                entity_id=object_entity_id,
                name=resolved_object_name,
                normalized_name=_normalize_key(resolved_object_name),
                kind=resolved_object_kind,
                updated_at=seen_at,
            )

        session.run(
            """
            MATCH (sub:Entity {entity_id: $subject_entity_id})
            MATCH (obj:Entity {entity_id: $object_entity_id})
            MERGE (sub)-[r:RELATES_TO {relation_id: $relation_id}]->(obj)
            SET r.contact_id = $contact_id,
                r.interaction_id = $interaction_id,
                r.claim_id = $claim_id,
                r.predicate = $predicate,
                r.predicate_norm = $predicate_norm,
                r.subject_name = $subject_name,
                r.object_name = $object_name,
                r.confidence = $confidence,
                r.status = $status,
                r.uncertain = $uncertain,
                r.source_system = $source_system,
                r.evidence_json = $evidence_json,
                r.first_seen_at = CASE
                    WHEN r.first_seen_at IS NULL THEN datetime($seen_at)
                    ELSE r.first_seen_at
                END,
                r.last_seen_at = datetime($seen_at)
            """,
            subject_entity_id=subject_entity_id,
            object_entity_id=object_entity_id,
            relation_id=relation_id,
            contact_id=contact_id,
            interaction_id=interaction_id,
            claim_id=claim_id,
            predicate=predicate_clean,
            predicate_norm=predicate_norm,
            subject_name=resolved_subject_name,
            object_name=resolved_object_name,
            confidence=float(confidence),
            status=_normalize_text(status) or "proposed",
            uncertain=bool(uncertain),
            source_system=_normalize_text(source_system) or "unknown",
            evidence_json=evidence_json,
            seen_at=seen_at,
        )

        conflict_rows = session.run(
            """
            MATCH (sub:Entity {entity_id: $subject_entity_id})-[r:RELATES_TO]->(other:Entity)
            WHERE r.contact_id = $contact_id
              AND r.predicate_norm = $predicate_norm
              AND coalesce(r.status, "proposed") = "accepted"
              AND other.entity_id <> $object_entity_id
              AND r.relation_id <> $relation_id
            RETURN r.relation_id AS relation_id,
                   r.claim_id AS claim_id,
                   other.name AS object_name,
                   coalesce(r.confidence, 0.0) AS confidence
            ORDER BY confidence DESC
            LIMIT 1
            """,
            subject_entity_id=subject_entity_id,
            contact_id=contact_id,
            predicate_norm=predicate_norm,
            object_entity_id=object_entity_id,
            relation_id=relation_id,
        ).data()

    conflict = None
    if conflict_rows:
        row = conflict_rows[0]
        conflict = {
            "relation_id": row.get("relation_id"),
            "claim_id": row.get("claim_id"),
            "object_name": row.get("object_name"),
            "confidence": _as_float(row.get("confidence"), 0.0),
        }

    return {
        "upserted": True,
        "relation_id": relation_id,
        "subject_entity_id": subject_entity_id,
        "object_entity_id": object_entity_id,
        "subject_name": resolved_subject_name,
        "object_name": resolved_object_name,
        "predicate": predicate_clean,
        "predicate_norm": predicate_norm,
        "conflict": conflict,
    }


def upsert_contact_company_relation(
    *,
    contact_id: str,
    company_name: str,
    source_system: str = "contacts_registry",
    confidence: float = 0.98,
) -> dict[str, Any]:
    company = _normalize_text(company_name)
    if not company:
        return {"upserted": False}

    claim_id = f"company-hint:{contact_id}:{_normalize_key(company)}"
    return upsert_relation_triple(
        contact_id=contact_id,
        interaction_id=f"{source_system}:{contact_id}:company_hint",
        interaction_timestamp_iso=datetime.now(timezone.utc).isoformat(),
        subject_name="contact",
        predicate="works_at",
        object_name=company,
        claim_id=claim_id,
        confidence=confidence,
        status="accepted",
        source_system=source_system,
        uncertain=False,
        evidence_refs=[{"source": "contact_cache.company", "value": company}],
        subject_kind="Contact",
        object_kind="Company",
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
    components_json_text = json.dumps(components_json or {}, ensure_ascii=True, separators=(",", ":"))
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
            components_json=components_json_text,
        )


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_components_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def get_latest_score_snapshots(contact_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not contact_ids:
        return {}

    with neo4j_session() as session:
        if session is None:
            return {}
        rows = session.run(
            """
            UNWIND $contact_ids AS cid
            OPTIONAL MATCH (c:Contact {contact_id: cid})-[:HAS_SCORE]->(s:ScoreSnapshot)
            WITH cid AS contact_id, s
            ORDER BY contact_id, s.asof DESC
            WITH contact_id, collect(s)[0] AS latest
            RETURN contact_id,
                   latest.asof AS asof,
                   latest.relationship_score AS relationship_score,
                   latest.priority_score AS priority_score,
                   latest.components_json AS components_json
            """,
            contact_ids=contact_ids,
        ).data()

    results: dict[str, dict[str, Any]] = {}
    for row in rows:
        contact_id = row.get("contact_id")
        asof = row.get("asof")
        if not isinstance(contact_id, str) or not isinstance(asof, str):
            continue
        results[contact_id] = {
            "asof": asof,
            "relationship_score": _as_float(row.get("relationship_score")),
            "priority_score": _as_float(row.get("priority_score")),
            "components_json": _as_components_json(row.get("components_json")),
        }
    return results


def get_contact_score_snapshots(contact_id: str, limit: int = 30) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return []
        rows = session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})-[:HAS_SCORE]->(s:ScoreSnapshot)
            RETURN s.asof AS asof,
                   s.relationship_score AS relationship_score,
                   s.priority_score AS priority_score,
                   s.components_json AS components_json
            ORDER BY s.asof DESC
            LIMIT $limit
            """,
            contact_id=contact_id,
            limit=max(1, limit),
        ).data()

    snapshots: list[dict[str, Any]] = []
    for row in rows:
        asof = row.get("asof")
        if not isinstance(asof, str):
            continue
        snapshots.append(
            {
                "asof": asof,
                "relationship_score": _as_float(row.get("relationship_score")),
                "priority_score": _as_float(row.get("priority_score")),
                "components_json": _as_components_json(row.get("components_json")),
            }
        )
    return snapshots


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
    upsert_relation_triple(
        contact_id=contact_id,
        interaction_id=f"resolution:{claim_id}",
        interaction_timestamp_iso=resolved_at_iso,
        subject_name="contact",
        predicate="works_at",
        object_name=company_name,
        claim_id=claim_id,
        confidence=0.95,
        status="accepted",
        source_system="resolution",
        uncertain=False,
        evidence_refs=[{"source": "resolution_task", "claim_id": claim_id}],
        subject_kind="Contact",
        object_kind="Company",
    )


def get_contact_company_hint(contact_id: str) -> str | None:
    with neo4j_session() as session:
        if session is None:
            return None
        rows = session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})
            OPTIONAL MATCH (c)-[:CURRENT_EMPLOYER]->(co:Company)
            RETURN c.company AS company_hint, co.name AS current_employer
            LIMIT 1
            """,
            contact_id=contact_id,
        ).data()

    if not rows:
        return None
    row = rows[0]
    for key in ("current_employer", "company_hint"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_contact_company_hints(contact_ids: list[str]) -> dict[str, str]:
    if not contact_ids:
        return {}

    with neo4j_session() as session:
        if session is None:
            return {}
        rows = session.run(
            """
            UNWIND $contact_ids AS cid
            OPTIONAL MATCH (c:Contact {contact_id: cid})
            OPTIONAL MATCH (c)-[:CURRENT_EMPLOYER]->(co:Company)
            RETURN cid AS contact_id,
                   coalesce(co.name, c.company) AS company
            """,
            contact_ids=contact_ids,
        ).data()

    results: dict[str, str] = {}
    for row in rows:
        contact_id = row.get("contact_id")
        company = row.get("company")
        if not isinstance(contact_id, str) or not isinstance(company, str):
            continue
        company_value = company.strip()
        if company_value:
            results[contact_id] = company_value
    return results


def get_contact_graph_paths(
    contact_id: str,
    *,
    objective: str | None = None,
    max_hops: int = 3,
    limit: int = 8,
    include_uncertain: bool = False,
) -> list[dict[str, Any]]:
    hops = max(1, min(int(max_hops), 3))
    fetch_limit = max(limit * 8, 40)
    keywords = _extract_keywords(objective or "", max_keywords=8)

    query = f"""
            MATCH (c:Contact {{contact_id: $contact_id}})-[:AS_ENTITY]->(root:Entity)
            MATCH p=(root)-[rels:RELATES_TO*1..{hops}]-(target:Entity)
            WHERE all(rel IN rels WHERE coalesce(rel.status, "proposed") <> "rejected")
            WITH nodes(p) AS ns,
                 rels,
                 reduce(total = 0.0, rel IN rels | total + coalesce(rel.confidence, 0.5)) / toFloat(size(rels)) AS avg_confidence,
                 size([rel IN rels WHERE coalesce(rel.uncertain, false)]) AS uncertain_hops
            RETURN [node IN ns | coalesce(node.name, node.contact_id, "")] AS node_names,
                   [rel IN rels | coalesce(rel.predicate, "related_to")] AS predicates,
                   [rel IN rels | coalesce(rel.relation_id, "")] AS relation_ids,
                   [rel IN rels | coalesce(rel.uncertain, false)] AS uncertain_flags,
                   avg_confidence AS avg_confidence,
                   uncertain_hops AS uncertain_hops,
                   size(rels) AS hops
            ORDER BY uncertain_hops ASC, avg_confidence DESC, hops ASC
            LIMIT $limit
            """

    with neo4j_session() as session:
        if session is None:
            return []
        rows = session.run(
            query,
            contact_id=contact_id,
            limit=fetch_limit,
        ).data()

    results: list[dict[str, Any]] = []
    for row in rows:
        uncertain_flags = row.get("uncertain_flags") or []
        uncertain_count = sum(1 for flag in uncertain_flags if bool(flag))
        if uncertain_count and not include_uncertain:
            continue

        node_names = [name for name in row.get("node_names") or [] if isinstance(name, str) and name.strip()]
        predicates = [item for item in row.get("predicates") or [] if isinstance(item, str) and item.strip()]
        if len(node_names) < 2 or not predicates:
            continue

        path_text = _build_path_text(node_names, predicates)
        if not path_text:
            continue
        if keywords and not any(keyword in path_text.lower() for keyword in keywords):
            continue

        results.append(
            {
                "path_text": path_text,
                "node_names": node_names,
                "predicates": predicates,
                "relation_ids": [item for item in row.get("relation_ids") or [] if isinstance(item, str) and item],
                "avg_confidence": round(_as_float(row.get("avg_confidence"), 0.0), 4),
                "hops": int(row.get("hops") or 0),
                "uncertain_hops": uncertain_count,
            }
        )
        if len(results) >= limit:
            break
    return results


def get_contact_graph_metrics(contact_id: str, *, lookback_days: int = 120) -> dict[str, Any]:
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=max(1, int(lookback_days)))).isoformat()

    with neo4j_session() as session:
        if session is None:
            return {
                "direct_relation_count": 0,
                "accepted_relation_count": 0,
                "uncertain_relation_count": 0,
                "recent_relation_count": 0,
                "entity_reach_2hop": 0,
                "path_count_2hop": 0,
                "opportunity_edge_count": 0,
            }

        row_data = session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})-[:AS_ENTITY]->(root:Entity)
            OPTIONAL MATCH (root)-[direct:RELATES_TO]-(:Entity)
            WHERE coalesce(direct.status, "proposed") <> "rejected"
            WITH root,
                 count(direct) AS direct_relation_count,
                 count(CASE WHEN coalesce(direct.status, "proposed") = "accepted" THEN 1 END) AS accepted_relation_count,
                 count(CASE WHEN coalesce(direct.uncertain, false) THEN 1 END) AS uncertain_relation_count,
                 count(CASE WHEN direct.last_seen_at >= datetime($cutoff_iso) THEN 1 END) AS recent_relation_count
            OPTIONAL MATCH (root)-[:RELATES_TO*1..2]-(reach:Entity)
            WITH root,
                 direct_relation_count,
                 accepted_relation_count,
                 uncertain_relation_count,
                 recent_relation_count,
                 count(DISTINCT reach) AS entity_reach_2hop
            OPTIONAL MATCH p=(root)-[hop:RELATES_TO*1..2]-(:Entity)
            WHERE all(rel IN hop WHERE coalesce(rel.status, "proposed") <> "rejected")
            RETURN direct_relation_count,
                   accepted_relation_count,
                   uncertain_relation_count,
                   recent_relation_count,
                   entity_reach_2hop,
                   count(DISTINCT p) AS path_count_2hop
            LIMIT 1
            """,
            contact_id=contact_id,
            cutoff_iso=cutoff_iso,
        ).data()

        opportunity_data = session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})-[:AS_ENTITY]->(root:Entity)
            OPTIONAL MATCH (root)-[r:RELATES_TO]-(:Entity)
            WHERE coalesce(r.status, "proposed") <> "rejected"
              AND (
                toLower(coalesce(r.predicate, "")) CONTAINS "opportun"
                OR toLower(coalesce(r.predicate, "")) CONTAINS "proposal"
                OR toLower(coalesce(r.predicate, "")) CONTAINS "deal"
                OR toLower(coalesce(r.object_name, "")) CONTAINS "opportun"
                OR toLower(coalesce(r.object_name, "")) CONTAINS "proposal"
              )
            RETURN count(r) AS opportunity_edge_count
            LIMIT 1
            """,
            contact_id=contact_id,
        ).data()

    row = row_data[0] if row_data else {}
    opp_row = opportunity_data[0] if opportunity_data else {}
    return {
        "direct_relation_count": int(row.get("direct_relation_count") or 0),
        "accepted_relation_count": int(row.get("accepted_relation_count") or 0),
        "uncertain_relation_count": int(row.get("uncertain_relation_count") or 0),
        "recent_relation_count": int(row.get("recent_relation_count") or 0),
        "entity_reach_2hop": int(row.get("entity_reach_2hop") or 0),
        "path_count_2hop": int(row.get("path_count_2hop") or 0),
        "opportunity_edge_count": int(opp_row.get("opportunity_edge_count") or 0),
    }


def delete_contact_graph(contact_id: str) -> None:
    with neo4j_session() as session:
        if session is None:
            return

        session.run(
            """
            MATCH (s:ScoreSnapshot {contact_id: $contact_id})
            DETACH DELETE s
            """,
            contact_id=contact_id,
        )
        session.run(
            """
            MATCH ()-[r:RELATES_TO {contact_id: $contact_id}]-()
            DELETE r
            """,
            contact_id=contact_id,
        )
        session.run(
            """
            MATCH (e:Entity {contact_id: $contact_id})
            DETACH DELETE e
            """,
            contact_id=contact_id,
        )
        session.run(
            """
            MATCH (e:Entity)
            WHERE NOT (e)--()
            DELETE e
            """
        )
        session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})
            DETACH DELETE c
            """,
            contact_id=contact_id,
        )
