from __future__ import annotations

import copy
import logging
from statistics import mean
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.neo4j.queries import (
    attach_contact_interaction,
    get_contact_claims,
    get_contact_graph_metrics,
    get_contact_graph_paths,
    merge_interaction,
    upsert_relation_triple,
)
from app.db.pg.models import Chunk, ContactCache, Draft, Interaction, RawEvent
from app.db.pg.session import SessionLocal
from app.services.chunking.chunk_email import chunk_email_text
from app.services.chunking.chunk_transcript import chunk_transcript_text
from app.services.embeddings.vector_store import insert_chunk_embeddings, search_chunks
from app.services.extraction.cognee_client import extract_candidates
from app.services.extraction.cognee_mapper import candidates_to_claims, write_claims_with_evidence
from app.services.memory.contradiction import detect_contradictions
from app.services.memory.mem0_client import propose_memory_ops
from app.services.memory.mem0_mapper import build_mem0_bundle
from app.services.news.match_contacts import match_contacts_for_news
from app.services.resolution.tasks import (
    create_graph_relation_resolution_task,
    create_identity_resolution_task,
    create_resolution_task,
)
from app.services.scoring.priority_score import compute_priority_score
from app.services.scoring.relationship_score import compute_relationship_score
from app.services.scoring.content_signals import derive_warmth_depth_signals
from app.services.scoring.snapshots import persist_score_snapshot
from app.api.v1.routes.scores import refresh_cached_interaction_summary

logger = logging.getLogger(__name__)


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


def _derive_warmth_delta(contact_interactions: list[Interaction]) -> float:
    if not contact_interactions:
        return 0.0
    inbound_count = sum(1 for interaction in contact_interactions if interaction.direction == "in")
    outbound_count = sum(1 for interaction in contact_interactions if interaction.direction == "out")
    total = inbound_count + outbound_count
    if total == 0:
        return 0.0
    return ((outbound_count - inbound_count) / total) * 5.0


def _derive_depth_count(contact_interactions: list[Interaction]) -> int:
    thread_ids = {interaction.thread_id for interaction in contact_interactions if interaction.thread_id}
    return len(thread_ids)


def _derive_open_loop_count(contact_interactions: list[Interaction]) -> int:
    latest_direction_by_thread: dict[str, str] = {}
    for interaction in contact_interactions:
        thread_id = interaction.thread_id
        if not thread_id or thread_id in latest_direction_by_thread:
            continue
        latest_direction_by_thread[thread_id] = interaction.direction
    return sum(1 for direction in latest_direction_by_thread.values() if direction == "in")


def _derive_trigger_score(contact_interactions: list[Interaction], now: datetime) -> float:
    keywords = ("urgent", "asap", "deadline", "follow-up", "follow up", "action required", "time-sensitive")
    score = 0.0
    for interaction in contact_interactions:
        age_days = (now - _as_utc(interaction.timestamp)).days
        if age_days > 14:
            continue
        subject = (interaction.subject or "").lower()
        if any(keyword in subject for keyword in keywords):
            score += 5.0
    return min(15.0, score)


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


def _normalized_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _relation_payload_from_claim(claim: dict[str, object]) -> dict[str, object] | None:
    value_json = claim.get("value_json")
    if not isinstance(value_json, dict):
        return None

    claim_type = _normalized_text(claim.get("claim_type"))
    subject = _normalized_text(value_json.get("subject")) or "contact"

    predicate = _normalized_text(value_json.get("predicate"))
    if not predicate:
        if claim_type == "employment":
            predicate = "works_at"
        elif claim_type == "topic":
            predicate = "discussed_topic"
        else:
            predicate = "related_to"

    object_name = (
        _normalized_text(value_json.get("object"))
        or _normalized_text(value_json.get("company"))
        or _normalized_text(value_json.get("destination"))
        or _normalized_text(value_json.get("target"))
        or _normalized_text(value_json.get("label"))
    )
    if not object_name:
        return None

    if object_name.lower() == subject.lower():
        return None

    object_kind = _normalized_text(value_json.get("object_type")) or "Entity"
    if claim_type == "employment" and object_kind == "Entity":
        object_kind = "Company"
    if claim_type == "topic" and _normalized_text(value_json.get("label")):
        object_kind = "Topic"

    return {
        "subject_name": subject,
        "predicate": predicate,
        "object_name": object_name,
        "subject_kind": _normalized_text(value_json.get("subject_type")) or ("Contact" if subject == "contact" else "Entity"),
        "object_kind": object_kind,
    }


def _persist_relation_claims_for_contact(
    *,
    db,
    contact_id: str,
    interaction: Interaction,
    claims: list[dict],
    auto_accept_threshold: float,
) -> dict[str, int]:
    persisted = 0
    uncertain = 0
    conflicts = 0
    seen_claim_ids: set[str] = set()
    interaction_iso = _as_utc(interaction.timestamp).isoformat()

    for claim in claims:
        claim_id = _normalized_text(claim.get("claim_id"))
        if not claim_id or claim_id in seen_claim_ids:
            continue

        relation_payload = _relation_payload_from_claim(claim)
        if relation_payload is None:
            continue

        seen_claim_ids.add(claim_id)
        claim_type = _normalized_text(claim.get("claim_type"))
        status = _normalized_text(claim.get("status")) or "proposed"
        confidence = float(claim.get("confidence", 0.0) or 0.0)
        source_system = _normalized_text(claim.get("source_system")) or "unknown"
        evidence_refs = claim.get("evidence_refs") if isinstance(claim.get("evidence_refs"), list) else []

        is_uncertain = status != "accepted" or confidence < auto_accept_threshold
        result = upsert_relation_triple(
            contact_id=contact_id,
            interaction_id=interaction.interaction_id,
            interaction_timestamp_iso=interaction_iso,
            subject_name=str(relation_payload["subject_name"]),
            predicate=str(relation_payload["predicate"]),
            object_name=str(relation_payload["object_name"]),
            claim_id=claim_id,
            confidence=confidence,
            status=status,
            source_system=source_system,
            uncertain=is_uncertain,
            evidence_refs=evidence_refs,
            subject_kind=str(relation_payload["subject_kind"]),
            object_kind=str(relation_payload["object_kind"]),
        )
        if not result.get("upserted"):
            continue

        persisted += 1
        conflict = result.get("conflict")
        predicate_norm = _normalized_text(result.get("predicate")).lower()
        high_value_predicate = predicate_norm not in {"discussed_topic", "related_to"}
        needs_review = bool(conflict) or (is_uncertain and (claim_type == "employment" or high_value_predicate) and confidence >= 0.35)
        if needs_review:
            uncertain += 1
            if conflict:
                conflicts += 1
            create_graph_relation_resolution_task(
                db,
                contact_id=contact_id,
                proposed_claim_id=claim_id,
                current_claim_id=str(conflict.get("claim_id")) if isinstance(conflict, dict) and conflict.get("claim_id") else None,
                payload_json={
                    "interaction_id": interaction.interaction_id,
                    "relation": {
                        "relation_id": result.get("relation_id"),
                        "subject": result.get("subject_name"),
                        "predicate": result.get("predicate"),
                        "object": result.get("object_name"),
                        "status": status,
                        "confidence": confidence,
                        "uncertain": is_uncertain,
                    },
                    "conflict": conflict,
                    "proposed_claim": {
                        "claim_id": claim_id,
                        "claim_type": claim.get("claim_type"),
                        "value_json": claim.get("value_json") if isinstance(claim.get("value_json"), dict) else {},
                        "status": status,
                        "confidence": confidence,
                    },
                    "reason": "Uncertain or conflicting graph relation extracted from interaction.",
                },
            )
    return {
        "persisted_relations": persisted,
        "uncertain_relations": uncertain,
        "conflicting_relations": conflicts,
    }


def _hybrid_graph_vector_signals(db, contact_id: str, objective_seed: str) -> tuple[dict[str, int], float, list[dict]]:
    graph_metrics = get_contact_graph_metrics(contact_id)
    graph_paths = get_contact_graph_paths(contact_id, objective=objective_seed, max_hops=3, limit=6, include_uncertain=False)
    graph_query = " ".join(
        path.get("path_text", "")
        for path in graph_paths
        if isinstance(path, dict) and isinstance(path.get("path_text"), str) and path.get("path_text")
    ).strip()

    vector_alignment = 0.0
    if graph_query:
        path_chunks = search_chunks(db, query=graph_query[:650], top_k=4, contact_id=contact_id)
        scores = [float(chunk.get("score", 0.0) or 0.0) for chunk in path_chunks if isinstance(chunk, dict)]
        if scores:
            vector_alignment = float(mean(scores))

    return graph_metrics, vector_alignment, graph_paths


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

            graph_relation_stats = _persist_relation_claims_for_contact(
                db=db,
                contact_id=contact_id,
                interaction=interaction,
                claims=prepared_candidates + new_claims,
                auto_accept_threshold=settings.auto_accept_threshold,
            )

            now = datetime.now(timezone.utc)
            contact_interactions = _interactions_for_contact(db, contact_id)
            count_30, count_90 = _interaction_counts(contact_interactions, now)
            last_interaction = contact_interactions[0] if contact_interactions else None
            inactivity_days = (now - _as_utc(last_interaction.timestamp)).days if last_interaction else 365
            heuristic_warmth_delta = _derive_warmth_delta(contact_interactions)
            heuristic_depth_count = _derive_depth_count(contact_interactions)
            warmth_delta, depth_count, warmth_depth_meta = derive_warmth_depth_signals(
                db=db,
                contact_interactions=contact_interactions,
                heuristic_warmth_delta=heuristic_warmth_delta,
                heuristic_depth_count=heuristic_depth_count,
            )
            open_loops = _derive_open_loop_count(contact_interactions)
            trigger_score = _derive_trigger_score(contact_interactions, now)
            objective_seed = " ".join(
                part
                for part in [
                    _normalized_text(interaction.subject),
                    _normalized_text(body_text[:180]),
                ]
                if part
            ) or "relationship follow up"
            graph_metrics, vector_alignment, graph_paths = _hybrid_graph_vector_signals(db, contact_id, objective_seed)
            graph_warmth_bonus = min(
                5.0,
                graph_metrics.get("recent_relation_count", 0) * 0.35 + vector_alignment * 4.0,
            )
            graph_depth_bonus = min(
                10,
                int(
                    round(
                        graph_metrics.get("entity_reach_2hop", 0) * 0.40
                        + graph_metrics.get("path_count_2hop", 0) * 0.20
                    )
                ),
            )
            graph_trigger_bonus = min(
                8.0,
                graph_metrics.get("opportunity_edge_count", 0) * 1.5
                + graph_metrics.get("recent_relation_count", 0) * 0.25
                + graph_metrics.get("uncertain_relation_count", 0) * 0.35,
            )
            warmth_for_score = warmth_delta + graph_warmth_bonus
            depth_for_score = depth_count
            if warmth_depth_meta.get("source") != "llm":
                depth_for_score = depth_count + len(existing_claims) + len(new_claims)
            depth_for_score += graph_depth_bonus
            relationship, relationship_components = compute_relationship_score(
                last_interaction_at=_as_utc(last_interaction.timestamp) if last_interaction else None,
                interaction_count_30d=int(count_30),
                interaction_count_90d=int(count_90),
                warmth_delta=warmth_for_score,
                depth_count=depth_for_score,
            )
            relationship_components["warmth_depth_source"] = warmth_depth_meta
            relationship_components["heuristic_warmth_delta"] = heuristic_warmth_delta
            relationship_components["heuristic_depth_count"] = heuristic_depth_count
            relationship_components["interaction_count_30d"] = int(count_30)
            relationship_components["interaction_count_90d"] = int(count_90)
            relationship_components["graph_warmth_bonus"] = round(graph_warmth_bonus, 3)
            relationship_components["graph_depth_bonus"] = int(graph_depth_bonus)
            relationship_components["graph_vector_alignment"] = round(vector_alignment, 4)
            relationship_components["graph_path_count_2hop"] = int(graph_metrics.get("path_count_2hop", 0))
            relationship_components["graph_entity_reach_2hop"] = int(graph_metrics.get("entity_reach_2hop", 0))
            relationship_components["graph_relation_count"] = int(graph_metrics.get("direct_relation_count", 0))
            relationship_components["graph_path_samples"] = [
                path.get("path_text")
                for path in graph_paths[:3]
                if isinstance(path, dict) and isinstance(path.get("path_text"), str)
            ]
            trigger_for_score = min(15.0, trigger_score + graph_trigger_bonus)
            priority, priority_components = compute_priority_score(
                relationship_score=relationship,
                inactivity_days=inactivity_days,
                open_loops=open_loops,
                trigger_score=trigger_for_score,
            )
            priority_components["inactivity_days"] = inactivity_days
            priority_components["open_loop_count"] = open_loops
            priority_components["trigger_score"] = trigger_score
            priority_components["graph_trigger_bonus"] = round(graph_trigger_bonus, 3)
            priority_components["graph_recent_relation_count"] = int(graph_metrics.get("recent_relation_count", 0))
            priority_components["graph_uncertain_relation_count"] = int(graph_metrics.get("uncertain_relation_count", 0))
            priority_components["graph_opportunity_edge_count"] = int(graph_metrics.get("opportunity_edge_count", 0))
            priority_components["graph_priority_trigger_input"] = round(trigger_for_score, 3)
            priority_components["last_interaction_id"] = last_interaction.interaction_id if last_interaction else None
            persist_score_snapshot(
                contact_id=contact_id,
                relationship_score=relationship,
                priority_score=priority,
                components_json={
                    "relationship": relationship_components,
                    "priority": priority_components,
                    "evidence_refs": evidence_refs,
                    "graph": {
                        "metrics": graph_metrics,
                        "paths": graph_paths[:4],
                        "relation_persistence": graph_relation_stats,
                    },
                },
            )
            try:
                refresh_cached_interaction_summary(db, contact_id)
            except Exception:
                logger.exception(
                    "interaction_summary_cache_refresh_failed_after_interaction",
                    extra={"contact_id": contact_id, "interaction_id": interaction.interaction_id},
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
            heuristic_warmth_delta = _derive_warmth_delta(contact_interactions)
            heuristic_depth_count = _derive_depth_count(contact_interactions)
            warmth_delta, depth_count, warmth_depth_meta = derive_warmth_depth_signals(
                db=db,
                contact_interactions=contact_interactions,
                heuristic_warmth_delta=heuristic_warmth_delta,
                heuristic_depth_count=heuristic_depth_count,
            )
            open_loops = _derive_open_loop_count(contact_interactions)
            trigger_score = _derive_trigger_score(contact_interactions, now)
            objective_seed = " ".join(
                value for value in [_normalized_text(contact.display_name), _normalized_text(contact.primary_email)] if value
            ) or "relationship follow up"
            graph_metrics, vector_alignment, graph_paths = _hybrid_graph_vector_signals(db, contact.contact_id, objective_seed)
            graph_warmth_bonus = min(
                5.0,
                graph_metrics.get("recent_relation_count", 0) * 0.35 + vector_alignment * 4.0,
            )
            graph_depth_bonus = min(
                10,
                int(
                    round(
                        graph_metrics.get("entity_reach_2hop", 0) * 0.40
                        + graph_metrics.get("path_count_2hop", 0) * 0.20
                    )
                ),
            )
            graph_trigger_bonus = min(
                8.0,
                graph_metrics.get("opportunity_edge_count", 0) * 1.5
                + graph_metrics.get("recent_relation_count", 0) * 0.25
                + graph_metrics.get("uncertain_relation_count", 0) * 0.35,
            )
            warmth_for_score = warmth_delta + graph_warmth_bonus
            depth_for_score = depth_count + graph_depth_bonus
            relationship, relationship_components = compute_relationship_score(
                last_interaction_at=_as_utc(last.timestamp) if last else None,
                interaction_count_30d=count_30,
                interaction_count_90d=count_90,
                warmth_delta=warmth_for_score,
                depth_count=depth_for_score,
            )
            relationship_components["warmth_depth_source"] = warmth_depth_meta
            relationship_components["heuristic_warmth_delta"] = heuristic_warmth_delta
            relationship_components["heuristic_depth_count"] = heuristic_depth_count
            relationship_components["interaction_count_30d"] = int(count_30)
            relationship_components["interaction_count_90d"] = int(count_90)
            relationship_components["graph_warmth_bonus"] = round(graph_warmth_bonus, 3)
            relationship_components["graph_depth_bonus"] = int(graph_depth_bonus)
            relationship_components["graph_vector_alignment"] = round(vector_alignment, 4)
            relationship_components["graph_path_count_2hop"] = int(graph_metrics.get("path_count_2hop", 0))
            relationship_components["graph_entity_reach_2hop"] = int(graph_metrics.get("entity_reach_2hop", 0))
            relationship_components["graph_relation_count"] = int(graph_metrics.get("direct_relation_count", 0))
            relationship_components["graph_path_samples"] = [
                path.get("path_text")
                for path in graph_paths[:3]
                if isinstance(path, dict) and isinstance(path.get("path_text"), str)
            ]
            trigger_for_score = min(15.0, trigger_score + graph_trigger_bonus)
            priority, priority_components = compute_priority_score(
                relationship_score=relationship,
                inactivity_days=inactivity_days,
                open_loops=open_loops,
                trigger_score=trigger_for_score,
            )
            priority_components["inactivity_days"] = inactivity_days
            priority_components["open_loop_count"] = open_loops
            priority_components["trigger_score"] = trigger_score
            priority_components["graph_trigger_bonus"] = round(graph_trigger_bonus, 3)
            priority_components["graph_recent_relation_count"] = int(graph_metrics.get("recent_relation_count", 0))
            priority_components["graph_uncertain_relation_count"] = int(graph_metrics.get("uncertain_relation_count", 0))
            priority_components["graph_opportunity_edge_count"] = int(graph_metrics.get("opportunity_edge_count", 0))
            priority_components["graph_priority_trigger_input"] = round(trigger_for_score, 3)
            priority_components["last_interaction_id"] = last.interaction_id if last else None
            persist_score_snapshot(
                contact_id=contact.contact_id,
                relationship_score=relationship,
                priority_score=priority,
                components_json={
                    "relationship": relationship_components,
                    "priority": priority_components,
                    "evidence_refs": [],
                    "graph": {
                        "metrics": graph_metrics,
                        "paths": graph_paths[:4],
                    },
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
