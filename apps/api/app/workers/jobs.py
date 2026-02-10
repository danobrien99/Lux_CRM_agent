from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.neo4j.queries import attach_contact_interaction, get_contact_claims, merge_interaction
from app.db.pg.models import Chunk, ContactCache, Draft, Interaction, RawEvent
from app.db.pg.session import SessionLocal
from app.services.chunking.chunk_email import chunk_email_text
from app.services.chunking.chunk_transcript import chunk_transcript_text
from app.services.embeddings.vector_store import insert_chunk_embeddings
from app.services.extraction.cognee_client import extract_candidates
from app.services.extraction.cognee_mapper import candidates_to_claims, write_claims_with_evidence
from app.services.memory.contradiction import detect_contradictions
from app.services.memory.mem0_client import propose_memory_ops
from app.services.memory.mem0_mapper import build_mem0_bundle
from app.services.news.match_contacts import match_contacts_for_news
from app.services.resolution.tasks import create_identity_resolution_task, create_resolution_task
from app.services.scoring.priority_score import compute_priority_score
from app.services.scoring.relationship_score import compute_relationship_score
from app.services.scoring.snapshots import persist_score_snapshot


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _resolve_contact_ids(db, participants_json: dict) -> tuple[list[str], list[str]]:
    emails: list[str] = []
    for bucket in ("from", "to", "cc"):
        for item in participants_json.get(bucket, []):
            email = (item.get("email") or "").lower()
            if email:
                emails.append(email)

    if not emails:
        return [], []

    unique_emails = sorted(set(emails))
    contacts = db.scalars(select(ContactCache).where(ContactCache.primary_email.in_(unique_emails))).all()
    matched_ids = sorted({c.contact_id for c in contacts})
    matched_emails = {c.primary_email.lower() for c in contacts}
    unresolved_emails = [email for email in unique_emails if email not in matched_emails]
    return matched_ids, unresolved_emails


def _create_chunks_for_interaction(interaction: Interaction, text: str) -> list[Chunk]:
    if interaction.type == "meeting":
        chunk_payloads = chunk_transcript_text(text)
    elif interaction.type == "news":
        chunk_payloads = [{"chunk_type": "news_paragraph", "text": text, "span_json": {"start": 0, "end": len(text)}}]
    else:
        chunk_payloads = chunk_email_text(text)

    records = []
    for payload in chunk_payloads:
        records.append(
            Chunk(
                interaction_id=interaction.interaction_id,
                chunk_type=payload["chunk_type"],
                text=payload["text"],
                span_json=payload["span_json"],
            )
        )
    return records


def _summarize_interaction_body(body_text: str) -> str:
    return body_text[:280].strip()


def _interactions_for_contact(db, contact_id: str) -> list[Interaction]:
    interactions = db.scalars(select(Interaction).order_by(Interaction.timestamp.desc()).limit(1000)).all()
    return [interaction for interaction in interactions if contact_id in (interaction.contact_ids_json or [])]


def _interaction_counts(contact_interactions: list[Interaction], now: datetime) -> tuple[int, int]:
    count_30 = sum(1 for interaction in contact_interactions if (now - _as_utc(interaction.timestamp)).days <= 30)
    count_90 = sum(1 for interaction in contact_interactions if (now - _as_utc(interaction.timestamp)).days <= 90)
    return count_30, count_90


def _claims_from_ops(ops: list[dict], default_evidence_refs: list[dict], interaction_id: str) -> list[dict]:
    claims: list[dict] = []
    for op in ops:
        claim = copy.deepcopy(op.get("claim") or {})
        if not claim:
            continue
        operation = op.get("op", "ADD")
        if operation == "REJECT":
            claim["status"] = "rejected"
        if operation not in {"ADD", "UPDATE", "SUPERSEDE", "REJECT"}:
            continue
        if not claim.get("evidence_refs"):
            claim["evidence_refs"] = [
                {
                    "interaction_id": interaction_id,
                    "chunk_id": ref["chunk_id"],
                    "span_json": ref["span_json"],
                }
                for ref in default_evidence_refs
            ]
        claims.append(claim)
    return claims


def process_interaction(interaction_id: str) -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        interaction = db.scalar(select(Interaction).where(Interaction.interaction_id == interaction_id))
        if interaction is None:
            return

        raw_event = db.scalar(
            select(RawEvent)
            .where(
                RawEvent.source_system == interaction.source_system,
                RawEvent.external_id == interaction.external_id,
            )
        )
        body_text = ""
        if raw_event:
            body_text = raw_event.payload_json.get("body_plain", "")

        contact_ids, unresolved_emails = _resolve_contact_ids(db, interaction.participants_json)
        interaction.contact_ids_json = contact_ids
        merge_interaction(
            {
                "interaction_id": interaction.interaction_id,
                "type": interaction.type,
                "timestamp": interaction.timestamp.isoformat(),
                "source_system": interaction.source_system,
                "direction": interaction.direction,
            }
        )
        for contact_id in contact_ids:
            attach_contact_interaction(contact_id, interaction.interaction_id)

        for email in unresolved_emails:
            create_identity_resolution_task(
                db,
                email=email,
                payload_json={
                    "interaction_id": interaction.interaction_id,
                    "source_system": interaction.source_system,
                },
            )

        chunks = _create_chunks_for_interaction(interaction, body_text)
        for chunk in chunks:
            db.add(chunk)
        db.commit()
        for chunk in chunks:
            db.refresh(chunk)

        if chunks:
            insert_chunk_embeddings(db, chunks, settings.embedding_model)

        candidates = extract_candidates(interaction.interaction_id, body_text)
        proposed_claims = candidates_to_claims(candidates)

        evidence_refs = [
            {
                "chunk_id": c.chunk_id,
                "span_json": c.span_json,
                "quote_hash": f"{c.chunk_id}:{len(c.text)}",
            }
            for c in chunks[:3]
        ]

        for contact_id in contact_ids:
            existing_claims = get_contact_claims(contact_id, status="accepted")
            prepared_candidates = []
            for claim in proposed_claims:
                claim_copy = copy.deepcopy(claim)
                claim_copy["evidence_refs"] = [
                    {
                        "interaction_id": interaction.interaction_id,
                        "chunk_id": ref["chunk_id"],
                        "span_json": ref["span_json"],
                    }
                    for ref in evidence_refs
                ]
                prepared_candidates.append(claim_copy)

            bundle = build_mem0_bundle(
                interaction_summary=_summarize_interaction_body(body_text),
                recent_claims=existing_claims,
                cognee_candidates=prepared_candidates,
                auto_accept_threshold=settings.auto_accept_threshold,
                scope_ids={
                    "user_id": contact_id,
                    "agent_id": settings.mem0_agent_id,
                    "run_id": interaction.interaction_id,
                    "contact_id": contact_id,
                    "interaction_id": interaction.interaction_id,
                },
            )
            ops = propose_memory_ops(bundle)
            new_claims = _claims_from_ops(ops, evidence_refs, interaction.interaction_id)
            if new_claims:
                write_claims_with_evidence(contact_id, interaction.interaction_id, new_claims, evidence_refs)

            contradictions = detect_contradictions(existing_claims, new_claims)
            for issue in contradictions:
                create_resolution_task(
                    db,
                    contact_id=contact_id,
                    task_type=issue["task_type"],
                    proposed_claim_id=issue["proposed_claim"]["claim_id"],
                    current_claim_id=issue["current_claim"]["claim_id"],
                    payload_json={
                        "current_claim": issue["current_claim"],
                        "proposed_claim": issue["proposed_claim"],
                        "interaction_id": interaction.interaction_id,
                    },
                )

            now = datetime.now(timezone.utc)
            contact_interactions = _interactions_for_contact(db, contact_id)
            count_30, count_90 = _interaction_counts(contact_interactions, now)
            last_interaction = contact_interactions[0] if contact_interactions else None
            inactivity_days = (now - _as_utc(last_interaction.timestamp)).days if last_interaction else 365
            relationship, relationship_components = compute_relationship_score(
                last_interaction_at=_as_utc(last_interaction.timestamp) if last_interaction else None,
                interaction_count_30d=int(count_30),
                interaction_count_90d=int(count_90),
                warmth_delta=2.0,
                depth_count=len(existing_claims) + len(new_claims),
            )
            priority, priority_components = compute_priority_score(
                relationship_score=relationship,
                inactivity_days=inactivity_days,
                open_loops=0,
                trigger_score=0,
            )
            persist_score_snapshot(
                contact_id=contact_id,
                relationship_score=relationship,
                priority_score=priority,
                components_json={
                    "relationship": relationship_components,
                    "priority": priority_components,
                    "evidence_refs": evidence_refs,
                },
            )

        interaction.status = "processed"
        db.commit()
    finally:
        db.close()


def process_news(interaction_id: str) -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        interaction = db.scalar(select(Interaction).where(Interaction.interaction_id == interaction_id))
        if interaction is None:
            return

        raw_event = db.scalar(
            select(RawEvent)
            .where(
                RawEvent.source_system == interaction.source_system,
                RawEvent.external_id == interaction.external_id,
            )
        )
        article_text = ""
        if raw_event:
            article_text = raw_event.payload_json.get("body_plain", "")

        existing_chunks = db.scalars(select(Chunk).where(Chunk.interaction_id == interaction.interaction_id)).all()
        if existing_chunks:
            chunks = existing_chunks
        else:
            chunks = _create_chunks_for_interaction(interaction, article_text)
            for chunk in chunks:
                db.add(chunk)
            db.commit()
            for chunk in chunks:
                db.refresh(chunk)

        if chunks:
            insert_chunk_embeddings(db, chunks, settings.embedding_model)

        # News match is computed and returned by API on request and intentionally not persisted.
        _ = match_contacts_for_news(db, article_text=article_text, max_results=10)
        interaction.status = "processed"
        db.commit()
    finally:
        db.close()


def recompute_scores() -> None:
    db = SessionLocal()
    try:
        contacts = db.scalars(select(ContactCache)).all()
        now = datetime.now(timezone.utc)
        for contact in contacts:
            contact_interactions = _interactions_for_contact(db, contact.contact_id)
            last = contact_interactions[0] if contact_interactions else None
            count_30, count_90 = _interaction_counts(contact_interactions, now)
            inactivity_days = (now - _as_utc(last.timestamp)).days if last else 999
            relationship, relationship_components = compute_relationship_score(
                last_interaction_at=_as_utc(last.timestamp) if last else None,
                interaction_count_30d=count_30,
                interaction_count_90d=count_90,
                warmth_delta=1.0,
                depth_count=2,
            )
            priority, priority_components = compute_priority_score(
                relationship_score=relationship,
                inactivity_days=inactivity_days,
                open_loops=1,
                trigger_score=0,
            )
            persist_score_snapshot(
                contact_id=contact.contact_id,
                relationship_score=relationship,
                priority_score=priority,
                components_json={
                    "relationship": relationship_components,
                    "priority": priority_components,
                    "evidence_refs": [],
                },
            )
    finally:
        db.close()


def cleanup_data() -> dict:
    settings = get_settings()
    if not settings.data_cleanup_enabled:
        return {"cleanup": "disabled"}

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        raw_cutoff = now - timedelta(days=settings.data_retention_raw_days)
        chunks_cutoff = now - timedelta(days=settings.data_retention_chunks_days)
        drafts_cutoff = now - timedelta(days=settings.data_retention_drafts_days)

        raw_deleted = db.execute(delete(RawEvent).where(RawEvent.received_at < raw_cutoff)).rowcount
        chunk_deleted = db.execute(delete(Chunk).where(Chunk.created_at < chunks_cutoff)).rowcount
        draft_deleted = db.execute(delete(Draft).where(Draft.created_at < drafts_cutoff)).rowcount

        db.commit()
        return {
            "raw_events_deleted": raw_deleted,
            "chunks_deleted": chunk_deleted,
            "drafts_deleted": draft_deleted,
        }
    finally:
        db.close()
