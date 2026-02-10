from __future__ import annotations

import re
from collections import defaultdict
from math import sqrt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.neo4j.driver import neo4j_session
from app.db.pg.models import ContactCache, Interaction
from app.services.embeddings.embedder import embed_texts


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


def _graph_candidates(keywords: list[str], limit: int) -> dict[str, dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return {}

        if keywords:
            records = session.run(
                """
                UNWIND $keywords AS kw
                MATCH (c:Contact)-[:HAS_CLAIM]->(cl:Claim)
                WHERE toLower(coalesce(cl.claim_type, "")) CONTAINS kw
                   OR toLower(toString(coalesce(cl.value_json, ""))) CONTAINS kw
                RETURN c.contact_id AS contact_id,
                       c.display_name AS display_name,
                       collect(DISTINCT kw) AS matched_keywords,
                       count(cl) AS graph_hits
                LIMIT $limit
                """,
                keywords=keywords,
                limit=limit,
            ).data()
        else:
            records = session.run(
                """
                MATCH (c:Contact)
                OPTIONAL MATCH (c)-[:HAS_CLAIM]->(cl:Claim)
                RETURN c.contact_id AS contact_id,
                       c.display_name AS display_name,
                       [] AS matched_keywords,
                       count(cl) AS graph_hits
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
        }
    return candidates


def _build_interaction_cache(db: Session, limit: int = 500) -> dict[str, list[Interaction]]:
    cache: dict[str, list[Interaction]] = defaultdict(list)
    interactions = db.scalars(select(Interaction).order_by(Interaction.timestamp.desc()).limit(limit)).all()
    for interaction in interactions:
        for contact_id in interaction.contact_ids_json or []:
            cache[contact_id].append(interaction)
    return cache


def _interaction_keyword_signal(interactions: list[Interaction], keywords: list[str]) -> tuple[float, list[dict[str, Any]]]:
    if not interactions or not keywords:
        return 0.0, []

    matched_keywords: set[str] = set()
    evidence_refs: list[dict[str, Any]] = []
    for interaction in interactions[:20]:
        subject = (interaction.subject or "").lower()
        overlap = [kw for kw in keywords if kw in subject]
        if not overlap:
            continue
        matched_keywords.update(overlap)
        evidence_refs.append(
            {
                "interaction_id": interaction.interaction_id,
                "matched_keywords": overlap,
            }
        )
    lexical_signal = min(1.0, len(matched_keywords) / max(1, len(keywords)))
    return lexical_signal, evidence_refs[:5]


def _claim_snippets(contact_id: str) -> list[str]:
    with neo4j_session() as session:
        if session is None:
            return []
        rows = session.run(
            """
            MATCH (c:Contact {contact_id: $contact_id})-[:HAS_CLAIM]->(cl:Claim)
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
        lexical_signal, lexical_refs = _interaction_keyword_signal(interactions, keywords)
        if lexical_signal == 0.0:
            continue
        interaction_candidates[contact_id] = {
            "lexical_signal": lexical_signal,
            "lexical_refs": lexical_refs,
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
        match_score = 0.5 * vector_similarity + 0.35 * graph_signal + 0.15 * lexical_signal

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
                            }
                        ],
                    },
                    {
                        "summary": "Vector rerank against contact profile built from claims and recent interactions",
                        "evidence_refs": [
                            {
                                "vector_similarity": round(vector_similarity, 4),
                                "profile_claim_count": len(claim_lines),
                                "interaction_refs": lexical_meta.get("lexical_refs", []),
                            }
                        ],
                    },
                ],
            }
        )

    ranked.sort(key=lambda row: row["match_score"], reverse=True)
    return ranked[:max_results]
