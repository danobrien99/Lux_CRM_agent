from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.neo4j.queries import (
    get_contact_claims,
    get_contact_graph_metrics,
    get_contact_graph_paths,
    get_latest_score_snapshots,
)
from app.db.pg.models import ContactCache, Interaction
from app.services.embeddings.vector_store import search_chunks


def _clean_phrase(value: str | None, max_chars: int = 90) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).strip()
    if not normalized:
        return None
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip()}..."


def _claim_focus(claim_snippet: str | None) -> str | None:
    cleaned = _clean_phrase(claim_snippet, max_chars=70)
    if not cleaned:
        return None
    if ":" in cleaned:
        return cleaned.split(":", 1)[1].strip() or cleaned
    return cleaned


def _claim_snippet(claim: dict) -> str | None:
    claim_type = str(claim.get("claim_type") or "").strip()
    value_json = claim.get("value_json")
    if not isinstance(value_json, dict):
        return None

    if claim_type == "employment":
        company = value_json.get("company") or value_json.get("employer") or value_json.get("organization")
        title = value_json.get("title") or value_json.get("role")
        if isinstance(company, str) and company.strip():
            if isinstance(title, str) and title.strip():
                return f"Current role: {title.strip()} at {company.strip()}"
            return f"Current company: {company.strip()}"

    for key in ("location", "timezone", "focus_area", "priority", "goal"):
        value = value_json.get(key)
        if isinstance(value, str) and value.strip():
            return f"{key.replace('_', ' ').title()}: {value.strip()}"

    return None


def _email_context_snippets(relevant_chunks: list[dict]) -> list[str]:
    snippets: list[str] = []
    for chunk in relevant_chunks:
        text = chunk.get("text")
        if not isinstance(text, str):
            continue
        normalized = " ".join(text.split())
        if not normalized:
            continue
        snippets.append(normalized[:220])
        if len(snippets) >= 3:
            break
    return snippets


def _merge_ranked_chunks(*chunk_lists: list[dict], top_k: int) -> list[dict]:
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
    ranked = sorted(merged.values(), key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
    return ranked[:top_k]


def _graph_path_snippets(graph_paths: list[dict], limit: int = 5) -> list[str]:
    snippets: list[str] = []
    for path in graph_paths:
        if not isinstance(path, dict):
            continue
        text = _clean_phrase(path.get("path_text"), max_chars=180)
        if not text:
            continue
        snippets.append(text)
        if len(snippets) >= limit:
            break
    return snippets


def derive_objective_from_bundle(bundle: dict) -> tuple[str, dict]:
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

    objective_clean = _clean_phrase(objective, max_chars=140) or "Reconnect on current priorities and confirm next steps"
    return objective_clean, {
        "recent_subject": recent_subject,
        "vector_context_snippet": vector_context,
        "graph_context_snippet": graph_context,
    }


def build_retrieval_bundle(db: Session, contact_id: str, objective: str | None, allow_sensitive: bool) -> dict:
    contact = db.scalar(select(ContactCache).where(ContactCache.contact_id == contact_id))
    all_interactions = db.scalars(select(Interaction).order_by(Interaction.timestamp.desc()).limit(500)).all()
    interactions = [
        interaction
        for interaction in all_interactions
        if contact_id in (interaction.contact_ids_json or [])
    ][:3]
    query = objective or (contact.display_name if contact else "follow up")
    vector_chunks = search_chunks(db, query=query, top_k=5, contact_id=contact_id)
    graph_paths = get_contact_graph_paths(
        contact_id,
        objective=query,
        max_hops=3,
        limit=8,
        include_uncertain=allow_sensitive,
    )
    graph_path_snippets = _graph_path_snippets(graph_paths, limit=6)
    graph_query = " ".join(graph_path_snippets[:3]).strip()
    graph_vector_chunks = search_chunks(db, query=graph_query, top_k=4, contact_id=contact_id) if graph_query else []
    relevant_chunks = _merge_ranked_chunks(vector_chunks, graph_vector_chunks, top_k=6)

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
        if len(graph_claim_snippets) >= 5:
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
            }
            for i in interactions
        ],
        "relevant_chunks": relevant_chunks,
        "email_context_snippets": email_context_snippets,
        "graph_claim_snippets": graph_claim_snippets,
        "graph_path_snippets": graph_path_snippets,
        "graph_paths": graph_paths,
        "graph_metrics": graph_metrics,
        "relationship_score_hint": relationship_score_hint,
        "hybrid_graph_query": graph_query,
        "allow_sensitive": allow_sensitive,
        "objective": objective,
    }
