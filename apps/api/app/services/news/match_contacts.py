from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from math import sqrt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.neo4j.driver import neo4j_session
from app.db.pg.models import ContactCache, Interaction
from app.services.embeddings.embedder import embed_texts
from app.services.ontology.runtime_contract import ontology_term_to_neo4j_identifier


_CONTACT_LABEL = ontology_term_to_neo4j_identifier("hs:Contact") or "`Contact`"
_ASSERTION_LABEL = ontology_term_to_neo4j_identifier("hs:Assertion") or "`Assertion`"
_ASSERTION_OBJECT_REL = ontology_term_to_neo4j_identifier("hs:assertionObject") or "`assertionObject`"


_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "because",
    "being",
    "below",
    "between",
    "could",
    "doing",
    "during",
    "from",
    "have",
    "into",
    "over",
    "such",
    "that",
    "their",
    "there",
    "these",
    "they",
    "this",
    "under",
    "very",
    "what",
    "when",
    "where",
    "which",
    "while",
    "with",
    "would",
}


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sqrt(sum(a * a for a in left))
    right_norm = sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot_product / (left_norm * right_norm)


def _extract_keywords(article_text: str, max_keywords: int = 12) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", article_text.lower())
    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen or token in _STOPWORDS:
            continue
        seen.add(token)
        unique.append(token)
        if len(unique) >= max_keywords:
            break
    return unique


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def _recency_signal(latest_seen_at: Any, *, max_days: int = 180) -> float:
    dt = _parse_dt(latest_seen_at)
    if dt is None:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    return max(0.0, 1.0 - min(age_days, float(max_days)) / float(max_days))


def _graph_candidates(keywords: list[str], limit: int) -> dict[str, dict[str, Any]]:
    settings = get_settings()
    use_v2 = bool(settings.graph_v2_enabled and settings.graph_v2_read_v2)
    with neo4j_session() as session:
        if session is None:
            return {}

        company_records: list[dict[str, Any]] = []
        if use_v2 and keywords:
            records = session.run(
                """
                UNWIND $keywords AS kw
                MATCH (c:CRMContact)
                MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(c)
                WHERE coalesce(a.status, "proposed") <> "rejected"
                  AND (
                    toLower(coalesce(a.claim_type, "")) CONTAINS kw
                    OR toLower(coalesce(a.predicate, "")) CONTAINS kw
                    OR toLower(coalesce(a.object_name, "")) CONTAINS kw
                    OR toLower(toString(coalesce(a.value_json, ""))) CONTAINS kw
                  )
                OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(ch:EvidenceChunk)
                WITH c, kw, collect(DISTINCT {
                    assertion_id: a.assertion_id,
                    claim_type: a.claim_type,
                    predicate: a.predicate,
                    object_name: a.object_name,
                    status: a.status,
                    confidence: coalesce(a.confidence, 0.0),
                    updated_at: a.updated_at,
                    interaction_id: ch.interaction_id,
                    chunk_id: ch.chunk_id
                }) AS kw_matches
                WITH c, collect(DISTINCT kw) AS matched_keywords, collect(kw_matches) AS grouped
                WITH c, matched_keywords, reduce(all = [], grp IN grouped | all + grp) AS flat_matches
                RETURN c.external_id AS contact_id,
                       c.display_name AS display_name,
                       matched_keywords,
                       size([m IN flat_matches WHERE m.assertion_id IS NOT NULL]) AS graph_hits,
                       [m IN flat_matches WHERE m.assertion_id IS NOT NULL][0..6] AS evidence_refs,
                       reduce(latest = null, m IN flat_matches |
                            CASE
                              WHEN m.updated_at IS NULL THEN latest
                              WHEN latest IS NULL THEN m.updated_at
                              WHEN m.updated_at > latest THEN m.updated_at
                              ELSE latest
                            END) AS latest_seen_at
                LIMIT $limit
                """,
                keywords=keywords,
                limit=limit,
            ).data()
            company_records = session.run(
                """
                UNWIND $keywords AS kw
                MATCH (c:CRMContact)-[w:WORKS_AT]->(co:CRMCompany)
                WHERE toLower(coalesce(co.name, "")) CONTAINS kw
                RETURN c.external_id AS contact_id,
                       c.display_name AS display_name,
                       collect(DISTINCT kw) AS matched_keywords,
                       collect(DISTINCT co.name) AS company_names,
                       count(DISTINCT co) AS company_hits,
                       max(coalesce(w.updated_at, co.updated_at)) AS latest_seen_at
                LIMIT $limit
                """,
                keywords=keywords,
                limit=limit,
            ).data()
        elif use_v2:
            records = session.run(
                """
                MATCH (c:CRMContact)
                OPTIONAL MATCH (c)-[:WORKS_AT]->(co:CRMCompany)
                OPTIONAL MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(c)
                WHERE a IS NULL OR coalesce(a.status, "proposed") <> "rejected"
                OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(ch:EvidenceChunk)
                RETURN c.external_id AS contact_id,
                       c.display_name AS display_name,
                       [] AS matched_keywords,
                       count(DISTINCT a) AS graph_hits,
                       collect(DISTINCT co.name) AS company_names,
                       collect(DISTINCT {
                          assertion_id: a.assertion_id,
                          claim_type: a.claim_type,
                          predicate: a.predicate,
                          object_name: a.object_name,
                          status: a.status,
                          confidence: coalesce(a.confidence, 0.0),
                          updated_at: a.updated_at,
                          interaction_id: ch.interaction_id,
                          chunk_id: ch.chunk_id
                       })[0..6] AS evidence_refs,
                       max(a.updated_at) AS latest_seen_at
                LIMIT $limit
                """,
                limit=limit,
            ).data()
        elif keywords:
            records = session.run(
                f"""
                UNWIND $keywords AS kw
                MATCH (c:{_CONTACT_LABEL})
                MATCH (cl:{_ASSERTION_LABEL})-[:{_ASSERTION_OBJECT_REL}]->(c)
                WHERE toLower(coalesce(cl.claim_type, "")) CONTAINS kw
                   OR toLower(toString(coalesce(cl.value_json, ""))) CONTAINS kw
                   OR toLower(coalesce(cl.object_name, "")) CONTAINS kw
                RETURN c.external_id AS contact_id,
                       c.display_name AS display_name,
                       collect(DISTINCT kw) AS matched_keywords,
                       count(cl) AS graph_hits,
                       [] AS company_names,
                       [] AS evidence_refs,
                       max(cl.updated_at) AS latest_seen_at
                LIMIT $limit
                """,
                keywords=keywords,
                limit=limit,
            ).data()
        else:
            records = session.run(
                f"""
                MATCH (c:{_CONTACT_LABEL})
                OPTIONAL MATCH (cl:{_ASSERTION_LABEL})-[:{_ASSERTION_OBJECT_REL}]->(c)
                RETURN c.external_id AS contact_id,
                       c.display_name AS display_name,
                       [] AS matched_keywords,
                       count(cl) AS graph_hits,
                       [] AS company_names,
                       [] AS evidence_refs,
                       max(cl.updated_at) AS latest_seen_at
                LIMIT $limit
                """,
                limit=limit,
            ).data()

    candidates: dict[str, dict[str, Any]] = {}
    for row in records:
        contact_id = row.get("contact_id")
        if not contact_id:
            continue
        candidates[contact_id] = {
            "display_name": row.get("display_name"),
            "graph_hits": int(row.get("graph_hits", 0)),
            "matched_keywords": row.get("matched_keywords", []),
            "company_names": [name for name in (row.get("company_names") or []) if isinstance(name, str) and name.strip()],
            "evidence_refs": [ref for ref in (row.get("evidence_refs") or []) if isinstance(ref, dict)],
            "latest_seen_at": row.get("latest_seen_at"),
        }
    for row in company_records:
        contact_id = row.get("contact_id")
        if not contact_id:
            continue
        candidate = candidates.setdefault(
            contact_id,
            {
                "display_name": row.get("display_name"),
                "graph_hits": 0,
                "matched_keywords": [],
                "company_names": [],
                "evidence_refs": [],
                "latest_seen_at": row.get("latest_seen_at"),
            },
        )
        candidate["display_name"] = candidate.get("display_name") or row.get("display_name")
        candidate["graph_hits"] = int(candidate.get("graph_hits", 0)) + int(row.get("company_hits", 0))
        existing_keywords = {kw for kw in candidate.get("matched_keywords", []) if isinstance(kw, str)}
        for kw in row.get("matched_keywords", []) or []:
            if isinstance(kw, str) and kw not in existing_keywords:
                candidate.setdefault("matched_keywords", []).append(kw)
                existing_keywords.add(kw)
        existing_companies = {name for name in candidate.get("company_names", []) if isinstance(name, str)}
        for company_name in row.get("company_names", []) or []:
            if isinstance(company_name, str) and company_name.strip() and company_name not in existing_companies:
                candidate.setdefault("company_names", []).append(company_name)
                existing_companies.add(company_name)
                candidate.setdefault("evidence_refs", []).append(
                    {
                        "kind": "company_association",
                        "company_name": company_name,
                        "updated_at": row.get("latest_seen_at"),
                    }
                )
        if not candidate.get("latest_seen_at") and row.get("latest_seen_at"):
            candidate["latest_seen_at"] = row.get("latest_seen_at")
    return candidates


def _build_interaction_cache(db: Session, limit: int = 500) -> dict[str, list[Interaction]]:
    cache: dict[str, list[Interaction]] = defaultdict(list)
    interactions = db.scalars(select(Interaction).order_by(Interaction.timestamp.desc()).limit(limit)).all()
    for interaction in interactions:
        for contact_id in interaction.contact_ids_json or []:
            cache[contact_id].append(interaction)
    return cache


def _interaction_keyword_signal(
    interactions: list[Interaction], keywords: list[str]
) -> tuple[float, list[dict[str, Any]], datetime | None]:
    if not interactions or not keywords:
        return 0.0, [], None

    matched_keywords: set[str] = set()
    evidence_refs: list[dict[str, Any]] = []
    latest_match_ts: datetime | None = None
    for interaction in interactions[:20]:
        subject = (interaction.subject or "").lower()
        overlap = [kw for kw in keywords if kw in subject]
        if not overlap:
            continue
        matched_keywords.update(overlap)
        ts = _parse_dt(interaction.timestamp)
        if ts is not None and (latest_match_ts is None or ts > latest_match_ts):
            latest_match_ts = ts
        evidence_refs.append(
            {
                "interaction_id": interaction.interaction_id,
                "matched_keywords": overlap,
                "timestamp": interaction.timestamp.isoformat() if getattr(interaction, "timestamp", None) else None,
            }
        )
    lexical_signal = min(1.0, len(matched_keywords) / max(1, len(keywords)))
    return lexical_signal, evidence_refs[:5], latest_match_ts


def _claim_snippets(contact_id: str) -> list[str]:
    settings = get_settings()
    use_v2 = bool(settings.graph_v2_enabled and settings.graph_v2_read_v2)
    with neo4j_session() as session:
        if session is None:
            return []
        if use_v2:
            rows = session.run(
                """
                MATCH (c:CRMContact {external_id: $contact_id})
                MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(c)
                WHERE coalesce(a.status, "proposed") <> "rejected"
                RETURN coalesce(a.claim_type, "unknown") AS claim_type,
                       toString(coalesce(a.value_json, "")) AS value_json,
                       coalesce(a.status, "proposed") AS status,
                       coalesce(a.confidence, 0.0) AS confidence
                ORDER BY confidence DESC, a.updated_at DESC
                LIMIT 12
                """,
                contact_id=contact_id,
            ).data()
        else:
            rows = session.run(
                f"""
                MATCH (c:{_CONTACT_LABEL} {{external_id: $contact_id}})
                MATCH (cl:{_ASSERTION_LABEL})-[:{_ASSERTION_OBJECT_REL}]->(c)
                RETURN coalesce(cl.claim_type, "unknown") AS claim_type,
                       toString(coalesce(cl.value_json, "")) AS value_json,
                       coalesce(cl.status, "proposed") AS status,
                       coalesce(cl.confidence, 0.0) AS confidence
                ORDER BY confidence DESC
                LIMIT 12
                """,
                contact_id=contact_id,
            ).data()
    return [f"{row['claim_type']}[{row['status']}]: {row['value_json']}" for row in rows]


def _build_contact_profile(contact: ContactCache, interactions: list[Interaction], claim_lines: list[str]) -> str:
    recent = interactions[:3]
    interaction_lines = [
        f"{item.timestamp.isoformat()} {item.type} {item.subject or ''}".strip()
        for item in recent
    ]
    profile_sections = [
        f"Contact: {contact.display_name or contact.contact_id}",
        f"Email: {contact.primary_email}",
        "Claims:",
        *claim_lines,
        "Recent interactions:",
        *interaction_lines,
    ]
    return "\n".join(line for line in profile_sections if line)


def match_contacts_for_news(db: Session, article_text: str, max_results: int = 10) -> list[dict]:
    contacts = db.scalars(select(ContactCache)).all()
    if not contacts:
        return []

    contacts_by_id = {contact.contact_id: contact for contact in contacts}
    keywords = _extract_keywords(article_text)
    graph_candidates = _graph_candidates(keywords, limit=max(100, max_results * 5))
    interaction_cache = _build_interaction_cache(db)

    interaction_candidates: dict[str, dict[str, Any]] = {}
    for contact_id, interactions in interaction_cache.items():
        lexical_signal, lexical_refs, latest_interaction_match_at = _interaction_keyword_signal(interactions, keywords)
        if lexical_signal == 0.0:
            continue
        interaction_candidates[contact_id] = {
            "lexical_signal": lexical_signal,
            "lexical_refs": lexical_refs,
            "latest_interaction_match_at": latest_interaction_match_at.isoformat() if latest_interaction_match_at else None,
        }

    candidate_ids = set(graph_candidates.keys()) | set(interaction_candidates.keys())
    if not candidate_ids:
        candidate_ids = set(contacts_by_id.keys())

    article_vector = embed_texts([article_text])[0]
    ranked: list[dict[str, Any]] = []
    for contact_id in candidate_ids:
        contact = contacts_by_id.get(contact_id)
        if contact is None:
            continue

        graph_meta = graph_candidates.get(contact_id, {})
        lexical_meta = interaction_candidates.get(contact_id, {})
        contact_interactions = interaction_cache.get(contact_id, [])
        claim_lines = _claim_snippets(contact_id)

        profile_text = _build_contact_profile(contact, contact_interactions, claim_lines)
        profile_vector = embed_texts([profile_text])[0]
        vector_similarity = max(0.0, _cosine_similarity(article_vector, profile_vector))
        graph_signal = min(1.0, float(graph_meta.get("graph_hits", 0)) / 10.0)
        lexical_signal = float(lexical_meta.get("lexical_signal", 0.0))
        graph_recency_signal = _recency_signal(graph_meta.get("latest_seen_at"))
        interaction_recency_signal = _recency_signal(lexical_meta.get("latest_interaction_match_at"))
        recency_signal = max(graph_recency_signal, interaction_recency_signal)
        match_score = (
            0.45 * vector_similarity
            + 0.25 * graph_signal
            + 0.15 * lexical_signal
            + 0.15 * recency_signal
        )

        ranked.append(
            {
                "contact_id": contact.contact_id,
                "display_name": contact.display_name,
                "match_score": round(min(match_score, 1.0), 4),
                "reason_chain": [
                    {
                        "summary": "Graph candidate generation from matching claims/topics",
                        "evidence_refs": [
                            {
                                "matched_keywords": graph_meta.get("matched_keywords", []),
                                "graph_hits": graph_meta.get("graph_hits", 0),
                                "company_names": graph_meta.get("company_names", [])[:5],
                                "latest_seen_at": graph_meta.get("latest_seen_at"),
                            }
                        ]
                        + [
                            {
                                "assertion_id": ref.get("assertion_id"),
                                "claim_type": ref.get("claim_type"),
                                "predicate": ref.get("predicate"),
                                "object_name": ref.get("object_name"),
                                "status": ref.get("status"),
                                "confidence": ref.get("confidence"),
                                "interaction_id": ref.get("interaction_id"),
                                "chunk_id": ref.get("chunk_id"),
                                "updated_at": ref.get("updated_at"),
                            }
                            for ref in graph_meta.get("evidence_refs", [])[:4]
                        ],
                    },
                    {
                        "summary": "Vector rerank against contact profile built from claims and recent interactions",
                        "evidence_refs": [
                            {
                                "vector_similarity": round(vector_similarity, 4),
                                "profile_claim_count": len(claim_lines),
                                "interaction_refs": lexical_meta.get("lexical_refs", []),
                                "graph_recency_signal": round(graph_recency_signal, 4),
                                "interaction_recency_signal": round(interaction_recency_signal, 4),
                                "recency_signal": round(recency_signal, 4),
                            }
                        ],
                    },
                ],
            }
        )

    ranked.sort(key=lambda row: row["match_score"], reverse=True)
    return ranked[:max_results]
