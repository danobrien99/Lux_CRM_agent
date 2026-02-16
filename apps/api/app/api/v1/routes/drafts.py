from __future__ import annotations

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
                "predicates": item.get("predicates"),
            }
        )

    return {
        "objective_query": bundle.get("objective"),
        "recent_interactions": recent_interactions,
        "vector_chunks": relevant_chunks,
        "graph_claim_snippets": graph_claim_snippets,
        "graph_paths": graph_paths,
        "hybrid_graph_query": bundle.get("hybrid_graph_query"),
        "graph_metrics": bundle.get("graph_metrics"),
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
    bundle = build_retrieval_bundle(db, payload.contact_id, payload.objective, payload.allow_sensitive)
    if not (payload.objective or "").strip():
        suggested_objective, _ = derive_objective_from_bundle(bundle)
        bundle["objective"] = suggested_objective

    relationship_score = _estimate_relationship_score(bundle)
    tone = resolve_tone_band(relationship_score)
    draft_text = compose_draft(bundle, tone)
    draft_subject = compose_subject(bundle, tone)
    citations = build_citations_from_bundle(bundle)
    context_summary = {
        "display_name": bundle.get("contact", {}).get("display_name"),
        "primary_email": bundle.get("contact", {}).get("primary_email"),
        "recent_interactions": len(bundle.get("recent_interactions", [])),
        "relevant_chunks": len(bundle.get("relevant_chunks", [])),
        "graph_claim_snippets": len(bundle.get("graph_claim_snippets", [])),
        "graph_paths": len(bundle.get("graph_paths", [])),
    }
    retrieval_trace = _retrieval_trace_from_bundle(bundle)

    prompt_json = {
        "objective": bundle.get("objective"),
        "allow_sensitive": payload.allow_sensitive,
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
