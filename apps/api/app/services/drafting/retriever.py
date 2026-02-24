from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.neo4j.queries import (
    get_contact_assertion_evidence_trace_v2,
    get_contact_claims,
    get_contact_context_signals_v2,
    get_contact_graph_metrics,
    get_contact_graph_paths,
    get_latest_score_snapshots,
)
from app.db.pg.models import Chunk, ContactCache, Interaction
from app.services.embeddings.vector_store import search_chunks

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
    "just",
    "also",
    "more",
    "would",
    "could",
    "should",
    "very",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
}

_OPPORTUNITY_TERMS = {
    "opportunity",
    "proposal",
    "deal",
    "pilot",
    "timeline",
    "workshop",
    "milestone",
    "budget",
    "pricing",
    "scope",
    "renewal",
    "contract",
}

_ACTION_TERMS = {
    "next",
    "step",
    "action",
    "follow",
    "meeting",
    "review",
    "confirm",
    "approve",
    "decision",
    "owner",
    "date",
    "send",
}


def _clean_phrase(value: str | None, max_chars: int = 90) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return None
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip()}..."


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_since(timestamp: datetime | None, now: datetime) -> int:
    if timestamp is None:
        return 999
    return max(0, (now - _as_utc(timestamp)).days)


def _tokenize(value: str | None, *, max_tokens: int = 20) -> list[str]:
    if not isinstance(value, str):
        return []
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", value.lower())
    results: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen or token in _STOPWORDS:
            continue
        seen.add(token)
        results.append(token)
        if len(results) >= max_tokens:
            break
    return results


def _keyword_overlap(text: str, query_terms: set[str]) -> float:
    if not text or not query_terms:
        return 0.0
    tokens = set(_tokenize(text, max_tokens=40))
    if not tokens:
        return 0.0
    hits = len(tokens & query_terms)
    if hits <= 0:
        return 0.0
    return min(1.0, hits / max(2, len(query_terms)))


def _contains_terms(text: str, terms: set[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _claim_focus(claim_snippet: str | None) -> str | None:
    cleaned = _clean_phrase(claim_snippet, max_chars=78)
    if not cleaned:
        return None
    if ":" in cleaned:
        return cleaned.split(":", 1)[1].strip() or cleaned
    return cleaned


def _claim_snippet(claim: dict) -> str | None:
    claim_type = str(claim.get("claim_type") or "").strip().lower()
    value_json = claim.get("value_json")
    if not isinstance(value_json, dict):
        return None

    subject = _clean_phrase(value_json.get("subject"), max_chars=44)
    obj = _clean_phrase(
        value_json.get("object")
        or value_json.get("company")
        or value_json.get("destination")
        or value_json.get("target")
        or value_json.get("label"),
        max_chars=80,
    )

    if claim_type == "employment" and obj:
        title = _clean_phrase(value_json.get("title") or value_json.get("role"), max_chars=48)
        if title:
            return f"Current role: {title} at {obj}"
        return f"Current company: {obj}"

    predicate = _clean_phrase(value_json.get("predicate"), max_chars=32)
    if claim_type in {"opportunity", "commitment", "preference", "family", "education", "personal_detail", "topic", "location"} and obj:
        if predicate and predicate not in {"related_to", "discussed_topic"}:
            if subject and subject.lower() != "contact":
                return f"{claim_type.title()}: {subject} {predicate} {obj}"
            return f"{claim_type.title()}: {predicate} {obj}"
        return f"{claim_type.title()}: {obj}"

    for key in ("location", "timezone", "focus_area", "priority", "goal"):
        value = value_json.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key.replace('_', ' ').title()}: {value.strip()}"

    return None


def _interaction_snippet_map(db: Session, interaction_ids: list[str], *, max_chars: int = 360) -> dict[str, str]:
    if not interaction_ids:
        return {}

    rows = db.execute(
        select(Chunk.interaction_id, Chunk.text)
        .where(Chunk.interaction_id.in_(interaction_ids))
        .order_by(Chunk.created_at.asc())
    ).all()

    snippets: dict[str, str] = {}
    for interaction_id, text in rows:
        normalized = " ".join((text or "").split())
        if not normalized:
            continue
        existing = snippets.get(interaction_id, "")
        combined = f"{existing} {normalized}".strip() if existing else normalized
        if len(combined) > max_chars:
            combined = combined[:max_chars].rstrip()
        snippets[interaction_id] = combined
    return snippets


def _interaction_meta(contact_interactions: list[Interaction], snippet_map: dict[str, str]) -> dict[str, dict]:
    meta: dict[str, dict] = {}
    for interaction in contact_interactions:
        meta[interaction.interaction_id] = {
            "interaction_id": interaction.interaction_id,
            "timestamp": interaction.timestamp,
            "thread_id": interaction.thread_id,
            "direction": interaction.direction,
            "subject": interaction.subject,
            "snippet": snippet_map.get(interaction.interaction_id, ""),
        }
    return meta


def _rank_graph_paths(graph_paths: list[dict], objective_terms: set[str]) -> list[dict]:
    ranked: list[dict] = []
    for path in graph_paths:
        if not isinstance(path, dict):
            continue

        text = str(path.get("path_text") or "").strip()
        if not text:
            continue

        avg_confidence = float(path.get("avg_confidence", 0.0) or 0.0)
        opportunity_hits = int(path.get("opportunity_hits") or 0)
        uncertain_hops = int(path.get("uncertain_hops") or 0)

        recency_days_raw = path.get("recency_days")
        recency_days = int(recency_days_raw) if isinstance(recency_days_raw, int) else 120
        recency_score = math.exp(-max(0, recency_days) / 90.0)

        semantic_score = _keyword_overlap(text, objective_terms)
        opportunity_score = min(1.0, opportunity_hits / 2.0)
        uncertainty_penalty = min(0.35, uncertain_hops * 0.14)

        retrieval_score = (
            avg_confidence * 0.35
            + recency_score * 0.30
            + semantic_score * 0.20
            + opportunity_score * 0.15
            - uncertainty_penalty
        )

        entry = dict(path)
        entry["retrieval_score"] = round(max(0.0, retrieval_score), 6)
        entry["recency_days"] = recency_days
        entry["opportunity_hits"] = opportunity_hits
        entry["semantic_score"] = round(semantic_score, 6)
        entry["recency_score"] = round(recency_score, 6)
        entry["opportunity_score"] = round(opportunity_score, 6)
        ranked.append(entry)

    ranked.sort(
        key=lambda item: (
            float(item.get("retrieval_score", 0.0)),
            float(item.get("avg_confidence", 0.0)),
            -int(item.get("recency_days", 999)),
        ),
        reverse=True,
    )
    return ranked


def _extract_graph_focus_terms(graph_paths: list[dict], *, max_terms: int = 10) -> list[str]:
    counter: Counter[str] = Counter()
    for path in graph_paths[:6]:
        text = str(path.get("path_text") or "")
        boost = 1 + int(path.get("opportunity_hits") or 0)
        for token in _tokenize(text, max_tokens=28):
            if token in _ACTION_TERMS:
                counter[token] += 1 * boost
                continue
            if token in _OPPORTUNITY_TERMS:
                counter[token] += 3 * boost
            elif len(token) >= 5:
                counter[token] += 1

    return [token for token, _count in counter.most_common(max_terms)]


def _build_thread_candidates(
    contact_interactions: list[Interaction],
    interaction_meta_by_id: dict[str, dict],
    graph_paths: list[dict],
    now: datetime,
) -> list[dict]:
    if not contact_interactions:
        return []

    graph_interaction_ids: set[str] = set()
    opportunity_graph_ids: set[str] = set()
    for path in graph_paths[:8]:
        ids = path.get("interaction_ids") or []
        valid_ids = [item for item in ids if isinstance(item, str) and item.strip()]
        graph_interaction_ids.update(valid_ids)
        if int(path.get("opportunity_hits") or 0) > 0:
            opportunity_graph_ids.update(valid_ids)

    buckets: dict[str, dict] = {}
    for interaction in contact_interactions:
        thread_id = interaction.thread_id or interaction.interaction_id
        entry = buckets.get(thread_id)
        if entry is None:
            entry = {
                "thread_id": thread_id,
                "interaction_ids": [],
                "latest_interaction_at": interaction.timestamp,
                "latest_interaction_id": interaction.interaction_id,
                "latest_direction": interaction.direction,
                "recent_subjects": [],
                "opportunity_term_hits": 0,
                "graph_hit_count": 0,
                "graph_opportunity_hit_count": 0,
                "interaction_count": 0,
            }
            buckets[thread_id] = entry

        entry["interaction_ids"].append(interaction.interaction_id)
        entry["interaction_count"] += 1

        if _as_utc(interaction.timestamp) > _as_utc(entry["latest_interaction_at"]):
            entry["latest_interaction_at"] = interaction.timestamp
            entry["latest_interaction_id"] = interaction.interaction_id
            entry["latest_direction"] = interaction.direction

        subject = (interaction.subject or "").strip()
        if subject and subject not in entry["recent_subjects"] and len(entry["recent_subjects"]) < 4:
            entry["recent_subjects"].append(subject)

        meta = interaction_meta_by_id.get(interaction.interaction_id, {})
        text_blob = " ".join(
            part
            for part in [
                str(interaction.subject or ""),
                str(meta.get("snippet") or ""),
            ]
            if part
        )
        term_hits = _contains_terms(text_blob, _OPPORTUNITY_TERMS)
        entry["opportunity_term_hits"] += term_hits

        if interaction.interaction_id in graph_interaction_ids:
            entry["graph_hit_count"] += 1
        if interaction.interaction_id in opportunity_graph_ids:
            entry["graph_opportunity_hit_count"] += 1

    candidates: list[dict] = []
    for thread in buckets.values():
        recency_days = _days_since(thread.get("latest_interaction_at"), now)
        recency_score = math.exp(-recency_days / 45.0)
        interaction_score = min(1.0, float(thread["interaction_count"]) / 6.0)
        opportunity_score = min(1.0, float(thread["opportunity_term_hits"]) / 5.0)
        graph_score = min(1.0, float(thread["graph_hit_count"]) / 3.0)
        graph_opportunity_score = min(1.0, float(thread["graph_opportunity_hit_count"]) / 2.0)
        open_loop_bonus = 0.12 if str(thread.get("latest_direction") or "") == "in" else 0.0

        thread_score = (
            recency_score * 0.34
            + interaction_score * 0.20
            + opportunity_score * 0.22
            + graph_score * 0.12
            + graph_opportunity_score * 0.12
            + open_loop_bonus
        )

        rationale = [
            f"{thread['interaction_count']} messages in thread",
            f"latest {recency_days} day(s) ago",
        ]
        if thread["opportunity_term_hits"] > 0:
            rationale.append(f"{thread['opportunity_term_hits']} opportunity keyword hit(s)")
        if thread["graph_hit_count"] > 0:
            rationale.append(f"{thread['graph_hit_count']} graph-linked interaction(s)")
        if thread["graph_opportunity_hit_count"] > 0:
            rationale.append(f"{thread['graph_opportunity_hit_count']} opportunity graph hit(s)")

        thread["recency_days"] = recency_days
        thread["thread_score"] = round(thread_score, 6)
        thread["rationale"] = rationale
        candidates.append(thread)

    candidates.sort(
        key=lambda item: (
            float(item.get("thread_score", 0.0)),
            -int(item.get("recency_days", 999)),
            int(item.get("interaction_count", 0)),
        ),
        reverse=True,
    )
    return candidates


def _merge_ranked_chunks(*chunk_lists: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for chunk_list in chunk_lists:
        for chunk in chunk_list:
            if not isinstance(chunk, dict):
                continue
            chunk_id = str(chunk.get("chunk_id") or "").strip()
            if not chunk_id:
                continue
            score = float(chunk.get("score", 0.0) or 0.0)
            existing = merged.get(chunk_id)
            if existing is None or float(existing.get("score", 0.0) or 0.0) < score:
                merged[chunk_id] = chunk
    return list(merged.values())


def _rerank_chunks(
    chunks: list[dict],
    interaction_meta_by_id: dict[str, dict],
    objective_terms: set[str],
    graph_focus_terms: set[str],
    now: datetime,
    *,
    active_thread_id: str | None,
    top_k: int,
) -> list[dict]:
    reranked: list[dict] = []
    for chunk in chunks:
        interaction_id = str(chunk.get("interaction_id") or "")
        if not interaction_id:
            continue

        meta = interaction_meta_by_id.get(interaction_id)
        if meta is None:
            continue

        semantic_score = float(chunk.get("score", 0.0) or 0.0)
        recency_days = _days_since(meta.get("timestamp"), now)
        recency_score = math.exp(-recency_days / 60.0)
        thread_bonus = 0.20 if active_thread_id and meta.get("thread_id") == active_thread_id else 0.0

        text = str(chunk.get("text") or "")
        objective_overlap = _keyword_overlap(text, objective_terms)
        graph_overlap = _keyword_overlap(text, graph_focus_terms)

        retrieval_score = (
            semantic_score * 0.45
            + recency_score * 0.25
            + objective_overlap * 0.20
            + graph_overlap * 0.10
            + thread_bonus
        )

        candidate = dict(chunk)
        candidate["thread_id"] = meta.get("thread_id")
        candidate["timestamp"] = _as_utc(meta.get("timestamp")).isoformat() if meta.get("timestamp") else None
        candidate["recency_days"] = recency_days
        candidate["retrieval_score"] = round(max(0.0, retrieval_score), 6)
        candidate["semantic_score"] = round(semantic_score, 6)
        candidate["objective_overlap"] = round(objective_overlap, 6)
        candidate["graph_overlap"] = round(graph_overlap, 6)
        reranked.append(candidate)

    reranked.sort(
        key=lambda item: (
            float(item.get("retrieval_score", 0.0)),
            float(item.get("score", 0.0)),
            -int(item.get("recency_days", 999)),
        ),
        reverse=True,
    )
    return reranked[:top_k]


def _email_context_snippets(relevant_chunks: list[dict]) -> list[str]:
    snippets: list[str] = []
    for chunk in relevant_chunks:
        text = chunk.get("text")
        if not isinstance(text, str):
            continue
        normalized = " ".join(text.split())
        if not normalized:
            continue
        snippets.append(normalized[:240])
        if len(snippets) >= 4:
            break
    return snippets


def _graph_path_snippets(graph_paths: list[dict], limit: int = 5) -> list[str]:
    snippets: list[str] = []
    for path in graph_paths:
        if not isinstance(path, dict):
            continue
        text = _clean_phrase(path.get("path_text"), max_chars=190)
        if not text:
            continue
        snippets.append(text)
        if len(snippets) >= limit:
            break
    return snippets


def _select_recent_interactions(contact_interactions: list[Interaction], active_thread_id: str | None) -> list[Interaction]:
    if not contact_interactions:
        return []
    if not active_thread_id:
        return contact_interactions[:3]

    in_thread = [interaction for interaction in contact_interactions if interaction.thread_id == active_thread_id][:3]
    if in_thread:
        return in_thread
    return contact_interactions[:3]


def _build_next_action(
    *,
    active_thread: dict | None,
    graph_paths: list[dict],
    relevant_chunks: list[dict],
    objective_query: str,
    motivator_signals: list[str] | None = None,
) -> tuple[str | None, list[str]]:
    rationale: list[str] = []
    thread_subject = None
    latest_direction = None

    if active_thread:
        subjects = active_thread.get("recent_subjects") or []
        if subjects and isinstance(subjects[0], str):
            thread_subject = subjects[0]
        latest_direction = str(active_thread.get("latest_direction") or "")
        rationale.append(
            f"Active thread selected ({active_thread.get('thread_id')}) with score {active_thread.get('thread_score')}"
        )
        for reason in active_thread.get("rationale") or []:
            if isinstance(reason, str):
                rationale.append(reason)

    top_path = None
    for path in graph_paths:
        if int(path.get("opportunity_hits") or 0) > 0:
            top_path = path
            break
    if top_path is None and graph_paths:
        top_path = graph_paths[0]

    path_focus = _claim_focus(top_path.get("path_text")) if isinstance(top_path, dict) else None
    if path_focus:
        rationale.append(f"Graph signal: {path_focus}")

    evidence_snippet = None
    if relevant_chunks:
        first_text = str(relevant_chunks[0].get("text") or "")
        evidence_snippet = _clean_phrase(first_text, max_chars=120)
    if evidence_snippet:
        rationale.append(f"Recent evidence: {evidence_snippet}")
    motivators = [item for item in (motivator_signals or []) if isinstance(item, str) and item.strip()]
    if motivators:
        rationale.append(f"Motivator signals: {', '.join(motivators[:3])}")

    focus_label = _clean_phrase(thread_subject or path_focus or objective_query, max_chars=72)
    if not focus_label:
        return None, rationale

    motivator_suffix = f" Align to motivator: {motivators[0]}." if motivators else ""
    if latest_direction == "in":
        next_action = (
            f"Reply on \"{focus_label}\" with two specific meeting options and confirm the owner/date for the next milestone.{motivator_suffix}"
        )
    else:
        next_action = (
            f"Follow up on \"{focus_label}\" and ask for a concrete decision checkpoint with owner/date for next steps.{motivator_suffix}"
        )

    return next_action, rationale


def derive_objective_from_bundle(bundle: dict) -> tuple[str, dict]:
    proposed_next_action = _clean_phrase(bundle.get("proposed_next_action"), max_chars=150)
    opportunity_thread = bundle.get("opportunity_thread") if isinstance(bundle.get("opportunity_thread"), dict) else None

    thread_subject = None
    if opportunity_thread:
        subjects = opportunity_thread.get("recent_subjects")
        if isinstance(subjects, list):
            for value in subjects:
                thread_subject = _clean_phrase(value, max_chars=80)
                if thread_subject:
                    break

    if proposed_next_action and thread_subject:
        objective = f"Follow up on \"{thread_subject}\" and execute: {proposed_next_action}"
    elif proposed_next_action:
        objective = proposed_next_action
    else:
        recent_interactions = bundle.get("recent_interactions", [])
        recent_subject = None
        if isinstance(recent_interactions, list):
            for item in recent_interactions:
                if not isinstance(item, dict):
                    continue
                recent_subject = _clean_phrase(item.get("subject"), max_chars=80)
                if recent_subject:
                    break

        email_context_snippets = bundle.get("email_context_snippets", [])
        vector_context = _clean_phrase(email_context_snippets[0], max_chars=110) if isinstance(email_context_snippets, list) and email_context_snippets else None

        graph_context = None
        graph_path_snippets = bundle.get("graph_path_snippets", [])
        if isinstance(graph_path_snippets, list) and graph_path_snippets:
            graph_context = _claim_focus(graph_path_snippets[0])
        if not graph_context:
            graph_claim_snippets = bundle.get("graph_claim_snippets", [])
            graph_context = _claim_focus(graph_claim_snippets[0]) if isinstance(graph_claim_snippets, list) and graph_claim_snippets else None

        if recent_subject and graph_context:
            objective = f"Follow up on \"{recent_subject}\" and align next steps around {graph_context}"
        elif recent_subject and vector_context:
            objective = f"Follow up on \"{recent_subject}\" and confirm the next milestone"
        elif recent_subject:
            objective = f"Follow up on \"{recent_subject}\" and propose next steps"
        elif graph_context:
            objective = f"Reconnect and move forward on {graph_context}"
        elif vector_context:
            objective = "Reconnect using recent email context and define clear next steps"
        else:
            objective = "Reconnect on current priorities and confirm next steps"

    objective_clean = _clean_phrase(objective, max_chars=170) or "Reconnect on current priorities and confirm next steps"
    return objective_clean, {
        "opportunity_thread_subject": thread_subject,
        "proposed_next_action": proposed_next_action,
        "graph_context_snippet": _clean_phrase((bundle.get("graph_path_snippets") or [None])[0], max_chars=110),
    }


def build_retrieval_bundle(db: Session, contact_id: str, objective: str | None, allow_sensitive: bool) -> dict:
    now = datetime.now(timezone.utc)
    contact = db.scalar(select(ContactCache).where(ContactCache.contact_id == contact_id))
    all_interactions = db.scalars(select(Interaction).order_by(Interaction.timestamp.desc()).limit(700)).all()
    contact_interactions = [
        interaction
        for interaction in all_interactions
        if contact_id in (interaction.contact_ids_json or [])
    ]

    interaction_ids = [interaction.interaction_id for interaction in contact_interactions[:160]]
    snippet_map = _interaction_snippet_map(db, interaction_ids, max_chars=380)
    interaction_meta_by_id = _interaction_meta(contact_interactions, snippet_map)

    query = objective or (contact.display_name if contact else "follow up")
    objective_terms = set(_tokenize(query, max_tokens=24))

    raw_graph_paths = get_contact_graph_paths(
        contact_id,
        objective=query,
        max_hops=3,
        limit=14,
        include_uncertain=allow_sensitive,
        lookback_days=365,
    )
    graph_paths = _rank_graph_paths(raw_graph_paths, objective_terms)
    graph_path_snippets = _graph_path_snippets(graph_paths, limit=6)
    graph_focus_terms = _extract_graph_focus_terms(graph_paths)

    thread_candidates = _build_thread_candidates(contact_interactions, interaction_meta_by_id, graph_paths, now)
    active_thread = thread_candidates[0] if thread_candidates else None
    active_thread_id = str(active_thread.get("thread_id")) if isinstance(active_thread, dict) else None

    hybrid_query_parts: list[str] = [query]
    hybrid_query_parts.extend(str(path.get("path_text") or "") for path in graph_paths[:3])
    if active_thread:
        hybrid_query_parts.extend(str(item) for item in active_thread.get("recent_subjects", [])[:2])
    hybrid_query_parts.extend(graph_focus_terms[:6])
    graph_query = " ".join(part.strip() for part in hybrid_query_parts if isinstance(part, str) and part.strip())[:900]

    vector_chunks = search_chunks(db, query=graph_query or query, top_k=24, contact_id=contact_id)
    thread_chunks: list[dict] = []
    if active_thread and active_thread.get("recent_subjects"):
        thread_query = " ".join(
            [str(item) for item in active_thread.get("recent_subjects", [])[:2]] + graph_focus_terms[:4]
        ).strip()
        if thread_query:
            thread_chunks = search_chunks(db, query=thread_query[:500], top_k=14, contact_id=contact_id)

    merged_chunks = _merge_ranked_chunks(vector_chunks, thread_chunks)
    relevant_chunks = _rerank_chunks(
        merged_chunks,
        interaction_meta_by_id,
        objective_terms,
        set(graph_focus_terms),
        now,
        active_thread_id=active_thread_id,
        top_k=6,
    )

    settings = get_settings()
    if settings.graph_v2_enabled and settings.graph_v2_read_v2:
        accepted_claims = []
    else:
        try:
            accepted_claims = get_contact_claims(contact_id, status="accepted")
        except Exception:
            accepted_claims = []

    graph_claim_snippets: list[str] = []
    for claim in accepted_claims:
        if claim.get("sensitive") and not allow_sensitive:
            continue
        snippet = _claim_snippet(claim)
        if snippet:
            graph_claim_snippets.append(snippet)
        if len(graph_claim_snippets) >= 6:
            break

    assertion_trace = get_contact_assertion_evidence_trace_v2(contact_id, limit=18)
    context_signals_v2 = get_contact_context_signals_v2(contact_id, limit=12)
    motivator_signals = []
    for signal in context_signals_v2:
        claim_type = str(signal.get("claim_type") or "").lower()
        if claim_type not in {"preference", "opportunity", "commitment", "personal_detail", "topic"}:
            continue
        object_name = str(signal.get("object_name") or "").strip()
        if not object_name or object_name in motivator_signals:
            continue
        motivator_signals.append(object_name)
        if len(motivator_signals) >= 6:
            break

    email_context_snippets = _email_context_snippets(relevant_chunks)
    graph_metrics = get_contact_graph_metrics(contact_id)
    snapshots = get_latest_score_snapshots([contact_id])
    relationship_score_hint = None
    snapshot = snapshots.get(contact_id)
    if isinstance(snapshot, dict):
        try:
            relationship_score_hint = float(snapshot.get("relationship_score"))
        except (TypeError, ValueError):
            relationship_score_hint = None

    proposed_next_action, next_action_rationale = _build_next_action(
        active_thread=active_thread,
        graph_paths=graph_paths,
        relevant_chunks=relevant_chunks,
        objective_query=query,
        motivator_signals=motivator_signals,
    )

    selected_recent_interactions = _select_recent_interactions(contact_interactions, active_thread_id)
    if active_thread:
        active_thread_payload = {
            "thread_id": active_thread.get("thread_id"),
            "interaction_count": active_thread.get("interaction_count"),
            "latest_interaction_id": active_thread.get("latest_interaction_id"),
            "latest_interaction_at": _as_utc(active_thread.get("latest_interaction_at")).isoformat()
            if active_thread.get("latest_interaction_at")
            else None,
            "latest_direction": active_thread.get("latest_direction"),
            "recent_subjects": active_thread.get("recent_subjects") or [],
            "thread_score": active_thread.get("thread_score"),
            "rationale": active_thread.get("rationale") or [],
        }
    else:
        active_thread_payload = None

    return {
        "contact": {
            "contact_id": contact_id,
            "display_name": contact.display_name if contact else None,
            "primary_email": contact.primary_email if contact else None,
        },
        "recent_interactions": [
            {
                "interaction_id": i.interaction_id,
                "timestamp": i.timestamp.isoformat(),
                "subject": i.subject,
                "thread_id": i.thread_id,
                "direction": i.direction,
            }
            for i in selected_recent_interactions
        ],
        "recent_interactions_global": [
            {
                "interaction_id": i.interaction_id,
                "timestamp": i.timestamp.isoformat(),
                "subject": i.subject,
                "thread_id": i.thread_id,
                "direction": i.direction,
            }
            for i in contact_interactions[:5]
        ],
        "relevant_chunks": relevant_chunks,
        "email_context_snippets": email_context_snippets,
        "graph_claim_snippets": graph_claim_snippets,
        "graph_path_snippets": graph_path_snippets,
        "graph_paths": graph_paths,
        "graph_metrics": graph_metrics,
        "assertion_evidence_trace": assertion_trace,
        "context_signals_v2": context_signals_v2,
        "motivator_signals": motivator_signals,
        "relationship_score_hint": relationship_score_hint,
        "hybrid_graph_query": graph_query,
        "graph_focus_terms": graph_focus_terms,
        "opportunity_thread": active_thread_payload,
        "proposed_next_action": proposed_next_action,
        "next_action_rationale": next_action_rationale,
        "allow_sensitive": allow_sensitive,
        "objective": objective,
        "retrieval_asof": now.isoformat(),
    }
