from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.schemas import DraftRequest, DraftResponse, DraftStatusUpdate
from app.db.pg.models import Draft
from app.services.drafting.citations import build_citations_from_bundle
from app.services.drafting.composer import compose_draft
from app.services.drafting.retriever import build_retrieval_bundle
from app.services.drafting.tone import resolve_tone_band

router = APIRouter(prefix="/drafts", tags=["drafts"])


def _estimate_relationship_score(bundle: dict) -> float:
    chunk_count = len(bundle.get("relevant_chunks", []))
    return min(100.0, 35.0 + chunk_count * 12.0)


@router.post("", response_model=DraftResponse)
def create_draft(payload: DraftRequest, db: Session = Depends(get_db)) -> DraftResponse:
    bundle = build_retrieval_bundle(db, payload.contact_id, payload.objective, payload.allow_sensitive)
    relationship_score = _estimate_relationship_score(bundle)
    tone = resolve_tone_band(relationship_score)
    draft_text = compose_draft(bundle, tone)
    citations = build_citations_from_bundle(bundle)

    draft = Draft(
        contact_id=payload.contact_id,
        prompt_json={"objective": payload.objective, "allow_sensitive": payload.allow_sensitive},
        draft_text=draft_text,
        citations_json=citations,
        tone_band=tone["tone_band"],
        status="proposed",
    )
    db.add(draft)
    db.commit()
    db.refresh(draft)

    return DraftResponse(
        draft_id=draft.draft_id,
        contact_id=draft.contact_id,
        tone_band=draft.tone_band,
        draft_text=draft.draft_text,
        citations_json=draft.citations_json,
        status=draft.status,
    )


@router.get("/{draft_id}", response_model=DraftResponse)
def get_draft(draft_id: str, db: Session = Depends(get_db)) -> DraftResponse:
    draft = db.scalar(select(Draft).where(Draft.draft_id == draft_id))
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    return DraftResponse(
        draft_id=draft.draft_id,
        contact_id=draft.contact_id,
        tone_band=draft.tone_band,
        draft_text=draft.draft_text,
        citations_json=draft.citations_json,
        status=draft.status,
    )


@router.post("/{draft_id}/status", response_model=DraftResponse)
def update_draft_status(draft_id: str, payload: DraftStatusUpdate, db: Session = Depends(get_db)) -> DraftResponse:
    draft = db.scalar(select(Draft).where(Draft.draft_id == draft_id))
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Draft not found")

    draft.status = payload.status
    db.commit()
    db.refresh(draft)

    return DraftResponse(
        draft_id=draft.draft_id,
        contact_id=draft.contact_id,
        tone_band=draft.tone_band,
        draft_text=draft.draft_text,
        citations_json=draft.citations_json,
        status=draft.status,
    )
