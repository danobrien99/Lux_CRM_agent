from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.schemas import (
    DraftRequest,
    DraftObjectiveSuggestionResponse,
    DraftResponse,
    DraftRevisionRequest,
    DraftStatusUpdate,
    DraftStyleGuideUpdateResponse,
)
from app.db.pg.models import Draft
from app.services.drafting.citations import build_citations_from_bundle
from app.services.drafting.composer import compose_draft, compose_subject
from app.services.drafting.retriever import build_retrieval_bundle, derive_objective_from_bundle
from app.services.drafting.tone import resolve_tone_band
from app.services.prompts.style_learning import update_writing_style_guide_from_draft

router = APIRouter(prefix="/drafts", tags=["drafts"])
_NON_BLOCKING_INTERNAL_ASSERTION_CLAIM_TYPES = {"topic", "relationship_signal"}


def _estimate_relationship_score(bundle: dict) -> float:
    hint = bundle.get("relationship_score_hint")
    if isinstance(hint, (int, float)):
        return max(0.0, min(100.0, float(hint)))

    interaction_count = len(bundle.get("recent_interactions", []))
    chunk_count = len(bundle.get("relevant_chunks", []))
    claim_count = len(bundle.get("graph_claim_snippets", []))
    path_count = len(bundle.get("graph_paths", []))
    graph_metrics = bundle.get("graph_metrics") if isinstance(bundle.get("graph_metrics"), dict) else {}
    graph_reach = int(graph_metrics.get("entity_reach_2hop", 0) or 0)
    if interaction_count == 0 and chunk_count == 0:
        return 0.0
    return min(100.0, interaction_count * 16.0 + chunk_count * 6.0 + claim_count * 2.5 + path_count * 3.5 + graph_reach * 0.8)


def _draft_subject_from_record(draft: Draft) -> str:
    payload = draft.prompt_json or {}
    subject = payload.get("draft_subject")
    if isinstance(subject, str) and subject.strip():
        return subject.strip()
    return "(No subject)"


def _draft_objective_from_record(draft: Draft) -> str | None:
    payload = draft.prompt_json or {}
    objective = payload.get("objective")
    if isinstance(objective, str) and objective.strip():
        return objective.strip()
    return None


def _snippet(value: str, max_chars: int = 220) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip()}..."


def _contains_object_phrase(text: str, object_name: str) -> bool:
    """Word-boundary phrase check to avoid substring false positives."""
    normalized = " ".join(str(object_name or "").split()).strip()
    if not normalized:
        return False
    tokens = [token for token in normalized.casefold().split() if token]
    if not tokens:
        return False
    pattern = r"\b" + r"\s+".join(re.escape(token) for token in tokens) + r"\b"
    return re.search(pattern, text.casefold()) is not None


def _draft_policy_violations(bundle: dict[str, Any], draft_text: str) -> list[dict[str, Any]]:
    if not isinstance(draft_text, str) or not draft_text.strip():
        return []
    violations: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for item in bundle.get("internal_assertion_evidence_trace", []) or []:
        if not isinstance(item, dict):
            continue
        claim_type = str(item.get("claim_type") or "").strip().lower()
        if claim_type in _NON_BLOCKING_INTERNAL_ASSERTION_CLAIM_TYPES:
            # Low-signal contextual topics/relationship hints frequently overlap with names/generic words.
            continue
        object_name = str(item.get("object_name") or "").strip()
        if len(object_name) < 3:
            continue
        if not _contains_object_phrase(draft_text, object_name):
            continue
        status = str(item.get("status") or "proposed").strip().lower()
        confidence = float(item.get("confidence") or 0.0)
        if claim_type in {"personal_detail", "family"}:
            violation_type = "sensitive_assertion_leak"
        elif status not in {"accepted", "verified"} or confidence < 0.8:
            violation_type = "uncertain_assertion_leak"
        else:
            violation_type = "disallowed_assertion_leak"
        key = (violation_type, str(item.get("assertion_id") or ""), object_name.casefold())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        violations.append(
            {
                "type": violation_type,
                "assertion_id": item.get("assertion_id"),
                "claim_type": claim_type or None,
                "status": status,
                "confidence": round(confidence, 4),
                "object_name": object_name,
            }
        )
    return violations


def _retrieval_trace_from_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    relevant_chunks = []
    for chunk in bundle.get("relevant_chunks", [])[:5]:
        if not isinstance(chunk, dict):
            continue
        text = str(chunk.get("text", "")).strip()
        relevant_chunks.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "interaction_id": chunk.get("interaction_id"),
                "score": chunk.get("score"),
                "retrieval_score": chunk.get("retrieval_score"),
                "thread_id": chunk.get("thread_id"),
                "timestamp": chunk.get("timestamp"),
                "recency_days": chunk.get("recency_days"),
                "snippet": _snippet(text) if text else "",
            }
        )

    recent_interactions = []
    for interaction in bundle.get("recent_interactions", [])[:3]:
        if not isinstance(interaction, dict):
            continue
        recent_interactions.append(
            {
                "interaction_id": interaction.get("interaction_id"),
                "timestamp": interaction.get("timestamp"),
                "subject": interaction.get("subject"),
                "thread_id": interaction.get("thread_id"),
                "direction": interaction.get("direction"),
            }
        )

    graph_claim_snippets = [
        snippet for snippet in bundle.get("graph_claim_snippets", [])[:5] if isinstance(snippet, str) and snippet.strip()
    ]
    graph_paths = []
    for item in bundle.get("graph_paths", [])[:6]:
        if not isinstance(item, dict):
            continue
        path_text = item.get("path_text")
        if not isinstance(path_text, str) or not path_text.strip():
            continue
        graph_paths.append(
            {
                "path_text": _snippet(path_text, max_chars=260),
                "hops": item.get("hops"),
                "avg_confidence": item.get("avg_confidence"),
                "uncertain_hops": item.get("uncertain_hops"),
                "opportunity_hits": item.get("opportunity_hits"),
                "recency_days": item.get("recency_days"),
                "retrieval_score": item.get("retrieval_score"),
                "latest_seen_at": item.get("latest_seen_at"),
                "predicates": item.get("predicates"),
            }
        )

    return {
        "retrieval_asof": bundle.get("retrieval_asof"),
        "objective_query": bundle.get("objective"),
        "recent_interactions": recent_interactions,
        "vector_chunks": relevant_chunks,
        "graph_claim_snippets": graph_claim_snippets,
        "graph_paths": graph_paths,
        "hybrid_graph_query": bundle.get("hybrid_graph_query"),
        "graph_focus_terms": bundle.get("graph_focus_terms"),
        "opportunity_thread": bundle.get("opportunity_thread"),
        "proposed_next_action": bundle.get("proposed_next_action"),
        "next_action_rationale": bundle.get("next_action_rationale"),
        "graph_metrics": bundle.get("graph_metrics"),
        "context_signals_v2": bundle.get("context_signals_v2", []),
        "motivator_signals": bundle.get("motivator_signals", []),
        "assertion_evidence_trace": bundle.get("assertion_evidence_trace", []),
    }


def _draft_retrieval_trace_from_record(draft: Draft) -> dict[str, Any] | None:
    payload = draft.prompt_json or {}
    retrieval_trace = payload.get("retrieval_trace")
    if isinstance(retrieval_trace, dict):
        return retrieval_trace
    return None


def _serialize_draft(draft: Draft, context_summary: dict | None = None) -> DraftResponse:
    return DraftResponse(
        draft_id=draft.draft_id,
        contact_id=draft.contact_id,
        tone_band=draft.tone_band,
        draft_subject=_draft_subject_from_record(draft),
        draft_text=draft.draft_text,
        citations_json=draft.citations_json,
        status=draft.status,
        objective=_draft_objective_from_record(draft),
        retrieval_trace=_draft_retrieval_trace_from_record(draft),
        context_summary=context_summary,
    )


@router.post("", response_model=DraftResponse)
def create_draft(payload: DraftRequest, db: Session = Depends(get_db)) -> DraftResponse:
    bundle = build_retrieval_bundle(
        db,
        payload.contact_id,
        payload.objective,
        payload.allow_sensitive,
        opportunity_id=payload.opportunity_id,
        allow_uncertain_context=payload.allow_uncertain_context,
        allow_proposed_changes_in_external_text=payload.allow_proposed_changes_in_external_text,
    )
    if not (payload.objective or "").strip():
        suggested_objective, _ = derive_objective_from_bundle(bundle)
        bundle["objective"] = suggested_objective

    relationship_score = _estimate_relationship_score(bundle)
    tone = resolve_tone_band(relationship_score)
    draft_text = compose_draft(bundle, tone)
    policy_violations = _draft_policy_violations(bundle, draft_text)
    if policy_violations:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "Draft includes content outside the allowed evidence/policy scope",
                "violations": policy_violations,
                "policy_flags": bundle.get("policy_flags") or {},
            },
        )
    draft_subject = compose_subject(bundle, tone)
    citations = build_citations_from_bundle(bundle, draft_text=draft_text)
    context_summary = {
        "display_name": bundle.get("contact", {}).get("display_name"),
        "primary_email": bundle.get("contact", {}).get("primary_email"),
        "recent_interactions": len(bundle.get("recent_interactions", [])),
        "recent_interactions_global": len(bundle.get("recent_interactions_global", [])),
        "relevant_chunks": len(bundle.get("relevant_chunks", [])),
        "graph_claim_snippets": len(bundle.get("graph_claim_snippets", [])),
        "graph_paths": len(bundle.get("graph_paths", [])),
        "policy_flags": bundle.get("policy_flags") or {},
        "opportunity_id": (bundle.get("opportunity_context") or {}).get("opportunity_id")
        if isinstance(bundle.get("opportunity_context"), dict)
        else None,
        "active_thread_id": (bundle.get("opportunity_thread") or {}).get("thread_id")
        if isinstance(bundle.get("opportunity_thread"), dict)
        else None,
    }
    retrieval_trace = _retrieval_trace_from_bundle(bundle)

    prompt_json = {
        "objective": bundle.get("objective"),
        "opportunity_id": payload.opportunity_id,
        "allow_sensitive": payload.allow_sensitive,
        "allow_uncertain_context": payload.allow_uncertain_context,
        "allow_proposed_changes_in_external_text": payload.allow_proposed_changes_in_external_text,
        "applied_policy_flags": bundle.get("policy_flags") or {},
        "draft_subject": draft_subject,
        "retrieval_trace": retrieval_trace,
    }

    draft: Draft | None = None
    if payload.overwrite_draft_id:
        draft = db.scalar(
            select(Draft).where(Draft.draft_id == payload.overwrite_draft_id, Draft.contact_id == payload.contact_id)
        )
        if draft is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft to overwrite not found")
        draft.prompt_json = prompt_json
        draft.draft_text = draft_text
        draft.citations_json = citations
        draft.tone_band = tone["tone_band"]
        draft.status = "proposed"
    else:
        draft = Draft(
            contact_id=payload.contact_id,
            prompt_json=prompt_json,
            draft_text=draft_text,
            citations_json=citations,
            tone_band=tone["tone_band"],
            status="proposed",
        )
        db.add(draft)

    db.commit()
    db.refresh(draft)

    return _serialize_draft(draft, context_summary=context_summary)


@router.get("/latest", response_model=DraftResponse)
def get_latest_draft(contact_id: str, db: Session = Depends(get_db)) -> DraftResponse:
    draft = db.scalar(select(Draft).where(Draft.contact_id == contact_id).order_by(Draft.created_at.desc()))
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No saved draft found for contact")
    return _serialize_draft(draft, context_summary=None)


@router.get("/objective_suggestion", response_model=DraftObjectiveSuggestionResponse)
def suggest_objective(contact_id: str, allow_sensitive: bool = False, db: Session = Depends(get_db)) -> DraftObjectiveSuggestionResponse:
    bundle = build_retrieval_bundle(db, contact_id, objective=None, allow_sensitive=allow_sensitive)
    objective, source_summary = derive_objective_from_bundle(bundle)
    return DraftObjectiveSuggestionResponse(contact_id=contact_id, objective=objective, source_summary=source_summary)


@router.get("/{draft_id}", response_model=DraftResponse)
def get_draft(draft_id: str, db: Session = Depends(get_db)) -> DraftResponse:
    draft = db.scalar(select(Draft).where(Draft.draft_id == draft_id))
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    return _serialize_draft(draft, context_summary=None)


@router.post("/{draft_id}/status", response_model=DraftResponse)
def update_draft_status(draft_id: str, payload: DraftStatusUpdate, db: Session = Depends(get_db)) -> DraftResponse:
    draft = db.scalar(select(Draft).where(Draft.draft_id == draft_id))
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    draft.status = payload.status
    db.commit()
    db.refresh(draft)

    return _serialize_draft(draft, context_summary=None)


@router.post("/{draft_id}/revise", response_model=DraftResponse)
def revise_draft(draft_id: str, payload: DraftRevisionRequest, db: Session = Depends(get_db)) -> DraftResponse:
    draft = db.scalar(select(Draft).where(Draft.draft_id == draft_id))
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    draft.draft_text = payload.draft_body
    prompt_json = dict(draft.prompt_json or {})
    prompt_json["draft_subject"] = payload.draft_subject.strip() or "(No subject)"
    prompt_json["last_revised_at"] = datetime.now(timezone.utc).isoformat()
    draft.prompt_json = prompt_json
    draft.status = payload.status
    db.commit()
    db.refresh(draft)

    return _serialize_draft(draft, context_summary=None)


@router.post("/{draft_id}/update_writing_style", response_model=DraftStyleGuideUpdateResponse)
def update_writing_style(draft_id: str, db: Session = Depends(get_db)) -> DraftStyleGuideUpdateResponse:
    draft = db.scalar(select(Draft).where(Draft.draft_id == draft_id))
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")
    if draft.status not in {"edited", "approved"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Revise and save the draft first before updating the writing style guide.",
        )

    try:
        result = update_writing_style_guide_from_draft(db, draft)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return DraftStyleGuideUpdateResponse(
        draft_id=draft.draft_id,
        updated=bool(result.get("updated", False)),
        samples_used=int(result.get("samples_used", 0)),
        guide_path=str(result.get("guide_path", "")),
        status="updated",
    )
