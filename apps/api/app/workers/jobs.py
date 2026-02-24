from __future__ import annotations

import copy
import json
import logging
import re
import uuid
from statistics import mean
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.neo4j.queries import (
    attach_contact_interaction,
    attach_internal_user_interaction_role,
    create_assertion_with_evidence_v2,
    create_extraction_event_v2,
    find_best_opportunity_for_interaction_v2,
    get_contact_claims,
    get_contact_company_links,
    get_contact_context_signals_v2,
    get_contact_graph_metrics,
    get_contact_graph_paths,
    get_open_case_counts_for_contact,
    link_opportunity_contacts_v2,
    link_engagement_to_opportunity_v2,
    merge_contact,
    merge_internal_user,
    merge_interaction,
    run_shacl_validation_v2,
    run_inference_rules_v2,
    upsert_case_contact_v2,
    upsert_case_opportunity_v2,
    upsert_contact_company_relation,
    upsert_relation_triple,
)
from app.db.pg.models import Chunk, ContactCache, Draft, Interaction, RawEvent, ResolutionTask
from app.db.pg.session import SessionLocal
from app.services.chunking.chunk_email import chunk_email_text
from app.services.chunking.chunk_transcript import chunk_transcript_text
from app.services.embeddings.vector_store import insert_chunk_embeddings, search_chunks
from app.services.extraction.cognee_client import extract_candidates
from app.services.extraction.cognee_mapper import candidates_to_claims, write_claims_with_evidence
from app.services.identity.internal_users import is_internal_email
from app.services.memory.contradiction import detect_contradictions
from app.services.memory.mem0_client import propose_memory_ops
from app.services.memory.mem0_mapper import build_mem0_bundle
from app.services.news.match_contacts import match_contacts_for_news
from app.services.ontology import relation_payload_from_claim
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

_LOW_SIGNAL_TEXT_TERMS = {
    "address",
    "along",
    "also",
    "away",
    "chance",
    "check",
    "find",
    "from",
    "great",
    "hello",
    "hi",
    "greetings",
    "thanks",
    "thank you",
}
_PERSISTABLE_GRAPH_CLAIM_TYPES = {
    "employment",
    "opportunity",
    "preference",
    "commitment",
    "personal_detail",
    "family",
    "education",
    "location",
    "topic",
    "relationship_signal",
}
_CASE_OPPORTUNITY_CLAIM_TYPES = {"opportunity", "commitment"}
_OPPORTUNITY_SUBJECT_KEYWORDS = {
    "proposal",
    "pricing",
    "quote",
    "budget",
    "contract",
    "sow",
    "scope",
    "pilot",
    "workshop",
    "next step",
    "next steps",
    "timeline",
    "commercial",
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _person_name_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _normalized_text(value).lower()).strip()


def _person_name_tokens(value: object) -> list[str]:
    return [tok for tok in _person_name_key(value).split(" ") if tok]


def _resolve_contact_ids(
    db, participants_json: dict
) -> tuple[list[str], list[str], dict[str, str], dict[str, str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    emails: list[str] = []
    display_names_by_email: dict[str, str] = {}
    internal_names_by_email: dict[str, str] = {}
    speaker_names_no_email: list[str] = []
    for bucket in ("from", "to", "cc"):
        for item in participants_json.get(bucket, []):
            email = (item.get("email") or "").lower()
            name_value = _normalized_text(item.get("name"))
            if email:
                emails.append(email)
                if is_internal_email(email):
                    if name_value and email not in internal_names_by_email:
                        internal_names_by_email[email] = name_value
                    continue
                if name_value and email not in display_names_by_email:
                    display_names_by_email[email] = name_value
            elif name_value:
                speaker_names_no_email.append(name_value)

    unique_emails = sorted(set(emails))
    contacts: list[ContactCache] = []
    if unique_emails:
        contacts = db.scalars(select(ContactCache).where(ContactCache.primary_email.in_(unique_emails))).all()
    matched_ids = sorted({c.contact_id for c in contacts if not is_internal_email(c.primary_email)})
    matched_emails = {c.primary_email.lower() for c in contacts if not is_internal_email(c.primary_email)}
    unresolved_emails = [email for email in unique_emails if email not in matched_emails and not is_internal_email(email)]

    name_match_provenance: dict[str, dict[str, Any]] = {}
    speaker_resolution_suggestions: list[dict[str, Any]] = []
    if speaker_names_no_email:
        all_contacts = db.scalars(select(ContactCache)).all()
        by_exact_name: dict[str, list[ContactCache]] = {}
        by_token_signature: dict[str, list[ContactCache]] = {}
        for contact in all_contacts:
            if is_internal_email(contact.primary_email):
                continue
            name_key = _person_name_key(contact.display_name)
            if name_key:
                by_exact_name.setdefault(name_key, []).append(contact)
            tokens = _person_name_tokens(contact.display_name)
            if len(tokens) >= 2:
                token_sig = f"{tokens[0]}|{tokens[-1]}"
                by_token_signature.setdefault(token_sig, []).append(contact)

        for speaker_name in sorted(set(speaker_names_no_email)):
            name_key = _person_name_key(speaker_name)
            tokens = _person_name_tokens(speaker_name)
            candidates = by_exact_name.get(name_key, []) if name_key else []
            provenance = None
            if len(candidates) == 1:
                matched_ids = sorted(set(matched_ids + [candidates[0].contact_id]))
                provenance = {
                    "match_method": "name_exact",
                    "speaker_name": speaker_name,
                    "contact_id": candidates[0].contact_id,
                    "confidence": 0.95,
                }
                name_match_provenance[candidates[0].contact_id] = provenance
                continue

            if len(tokens) >= 2:
                token_sig = f"{tokens[0]}|{tokens[-1]}"
                token_candidates = by_token_signature.get(token_sig, [])
                if len(token_candidates) == 1:
                    matched_ids = sorted(set(matched_ids + [token_candidates[0].contact_id]))
                    provenance = {
                        "match_method": "name_first_last_unique",
                        "speaker_name": speaker_name,
                        "contact_id": token_candidates[0].contact_id,
                        "confidence": 0.75,
                    }
                    name_match_provenance[token_candidates[0].contact_id] = provenance
                    continue
                if token_candidates:
                    speaker_resolution_suggestions.append(
                        {
                            "speaker_name": speaker_name,
                            "match_method": "name_ambiguous",
                            "candidates": [
                                {
                                    "contact_id": candidate.contact_id,
                                    "display_name": candidate.display_name,
                                    "primary_email": candidate.primary_email,
                                    "confidence": 0.55,
                                }
                                for candidate in token_candidates[:5]
                            ],
                        }
                    )
                    continue

            speaker_resolution_suggestions.append(
                {
                    "speaker_name": speaker_name,
                    "match_method": "name_no_match",
                    "candidates": [],
                }
            )

    return (
        matched_ids,
        unresolved_emails,
        display_names_by_email,
        internal_names_by_email,
        name_match_provenance,
        speaker_resolution_suggestions,
    )


def _enqueue_speaker_resolution_tasks(db, interaction: Interaction, speaker_resolution_suggestions: list[dict[str, Any]]) -> int:
    created = 0
    for speaker in speaker_resolution_suggestions:
        speaker_name = _normalized_text(speaker.get("speaker_name"))
        if not speaker_name:
            continue
        proposed_claim_id = f"speaker_identity:{interaction.interaction_id}:{_normalized_compact(speaker_name).replace(' ', '_')}"
        existing_task = db.scalar(
            select(ResolutionTask).where(
                ResolutionTask.proposed_claim_id == proposed_claim_id,
                ResolutionTask.status == "open",
            )
        )
        if existing_task is not None:
            continue
        create_resolution_task(
            db,
            contact_id="",
            task_type="speaker_identity_resolution",
            proposed_claim_id=proposed_claim_id,
            current_claim_id=None,
            payload_json={
                "interaction_id": interaction.interaction_id,
                "speaker_name": speaker_name,
                "match_method": speaker.get("match_method"),
                "candidates": speaker.get("candidates") or [],
                "entity_status": "provisional",
                "promotion_reason": "speaker_name_only_unresolved",
                "gate_results": {
                    "source_system": interaction.source_system,
                    "match_method": speaker.get("match_method"),
                    "candidate_count": len(speaker.get("candidates") or []),
                },
            },
        )
        created += 1
    return created


def _interaction_contact_hint(raw_payload_json: dict | None) -> dict[str, str | None]:
    payload = raw_payload_json if isinstance(raw_payload_json, dict) else {}
    contact_id = _normalized_text(payload.get("contact_id")) or None
    primary_email = _normalized_text(payload.get("primary_email")).lower() or None
    display_name = _normalized_text(payload.get("contact_display_name")) or None
    company_name = _normalized_text(payload.get("contact_company")) or None
    return {
        "contact_id": contact_id,
        "primary_email": primary_email,
        "display_name": display_name,
        "company_name": company_name,
    }


def _interaction_backfill_mode(raw_payload_json: dict | None) -> str | None:
    payload = raw_payload_json if isinstance(raw_payload_json, dict) else {}
    raw_mode = _normalized_text(payload.get("backfill_contact_mode") or payload.get("backfillMode"))
    if not raw_mode:
        return None
    normalized = raw_mode.strip().lower()
    if normalized in {"skip_previously_processed", "reprocess_all"}:
        return normalized
    return None


def _seed_or_resolve_hint_contact(
    db,
    *,
    hint_contact_id: str | None,
    hint_primary_email: str | None,
    hint_display_name: str | None,
) -> ContactCache | None:
    contact: ContactCache | None = None
    normalized_contact_id = _normalized_text(hint_contact_id) or None
    normalized_email = _normalized_text(hint_primary_email).lower() or None
    normalized_display_name = _normalized_text(hint_display_name) or None

    if normalized_contact_id:
        contact = db.scalar(select(ContactCache).where(ContactCache.contact_id == normalized_contact_id))
    if contact is None and normalized_email:
        contact = db.scalar(select(ContactCache).where(ContactCache.primary_email == normalized_email))

    if normalized_email and is_internal_email(normalized_email):
        return None
    if contact is not None and is_internal_email(contact.primary_email):
        return None

    changed = False
    if contact is None and normalized_contact_id and normalized_email:
        contact = ContactCache(
            contact_id=normalized_contact_id,
            primary_email=normalized_email,
            display_name=normalized_display_name,
            owner_user_id=None,
            use_sensitive_in_drafts=False,
        )
        db.add(contact)
        changed = True
    elif contact is not None:
        if normalized_email and contact.primary_email != normalized_email:
            contact.primary_email = normalized_email
            changed = True
        if normalized_display_name and not _normalized_text(contact.display_name):
            contact.display_name = normalized_display_name
            changed = True

    if changed:
        db.commit()
        db.refresh(contact)

    return contact


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


def _scope_claim_id(base_claim_id: str, *, contact_id: str, interaction_id: str) -> str:
    seed = f"{contact_id}:{interaction_id}:{base_claim_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _claims_from_ops(
    ops: list[dict],
    default_evidence_refs: list[dict],
    interaction_id: str,
    contact_id: str,
) -> list[dict]:
    claims: list[dict] = []
    for op in ops:
        claim = copy.deepcopy(op.get("claim") or {})
        if not claim:
            continue
        base_claim_id = _normalized_text(claim.get("claim_id")) or str(uuid.uuid4())
        claim["claim_id"] = _scope_claim_id(
            base_claim_id,
            contact_id=contact_id,
            interaction_id=interaction_id,
        )
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


def _normalized_compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _normalized_text(value).lower()).strip()


def _claim_type_name(claim: dict[str, Any]) -> str:
    return _normalized_text(claim.get("claim_type")).lower()


def _claim_value_text(claim: dict[str, Any]) -> str:
    value_json = claim.get("value_json") if isinstance(claim.get("value_json"), dict) else {}
    return (
        _normalized_text(value_json.get("object"))
        or _normalized_text(value_json.get("company"))
        or _normalized_text(value_json.get("label"))
        or _normalized_text(value_json.get("target"))
        or _normalized_text(value_json.get("destination"))
    )


def _chunk_evidence_ref(interaction_id: str, chunk: Chunk) -> dict[str, Any]:
    return {
        "interaction_id": interaction_id,
        "chunk_id": chunk.chunk_id,
        "span_json": chunk.span_json,
        "quote_hash": f"{chunk.chunk_id}:{len(chunk.text)}",
    }


def _claim_evidence_refs_for_chunks(
    claim: dict[str, Any],
    *,
    interaction_id: str,
    chunks: list[Chunk],
    default_limit: int = 2,
) -> list[dict[str, Any]]:
    if not chunks:
        return []
    value_json = claim.get("value_json") if isinstance(claim.get("value_json"), dict) else {}
    evidence_spans = value_json.get("evidence_spans") if isinstance(value_json.get("evidence_spans"), list) else []

    matched: list[Chunk] = []
    for chunk in chunks:
        chunk_span = chunk.span_json if isinstance(chunk.span_json, dict) else {}
        chunk_start = chunk_span.get("start")
        chunk_end = chunk_span.get("end")
        if not isinstance(chunk_start, int) or not isinstance(chunk_end, int):
            continue
        for span in evidence_spans:
            if not isinstance(span, dict):
                continue
            span_start = span.get("start")
            span_end = span.get("end")
            if not isinstance(span_start, int) or not isinstance(span_end, int):
                continue
            if max(chunk_start, span_start) < min(chunk_end, span_end):
                matched.append(chunk)
                break

    if not matched:
        target_text = _normalized_compact(
            _claim_value_text(claim)
            or value_json.get("object")
            or value_json.get("label")
            or value_json.get("predicate")
        )
        target_terms = {tok for tok in target_text.split(" ") if tok and len(tok) >= 3}
        if target_terms:
            scored: list[tuple[int, Chunk]] = []
            for chunk in chunks[:8]:
                chunk_terms = {tok for tok in _normalized_compact(chunk.text).split(" ") if tok}
                overlap = len(target_terms & chunk_terms)
                if overlap <= 0:
                    continue
                scored.append((overlap, chunk))
            scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
            matched = [chunk for _, chunk in scored[: max(1, default_limit)]]

    if not matched:
        matched = chunks[: max(1, default_limit)]

    refs: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    for chunk in matched:
        if chunk.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(chunk.chunk_id)
        refs.append(_chunk_evidence_ref(interaction_id, chunk))
        if len(refs) >= max(1, default_limit):
            break
    return refs


def _is_low_signal_text(value: object) -> bool:
    text = _normalized_text(value)
    if not text:
        return True
    raw_tokens = re.findall(r"[A-Za-z0-9]+", text)
    compact = _normalized_compact(text)
    if not compact:
        return True
    if compact in _LOW_SIGNAL_TEXT_TERMS:
        return True
    tokens = [tok for tok in compact.split(" ") if tok]
    if len(tokens) == 1:
        token = tokens[0]
        raw_token = raw_tokens[0] if len(raw_tokens) == 1 else token
        if token in _LOW_SIGNAL_TEXT_TERMS:
            return True
        if token.isalpha() and len(token) <= 3:
            # Keep short mixed/upper-case acronyms (e.g., PwC, IBM) as valid signals.
            if isinstance(raw_token, str) and any(ch.isupper() for ch in raw_token):
                return False
            return True
    return False


def _is_persistable_graph_claim(claim: dict[str, Any]) -> bool:
    claim_type = _claim_type_name(claim)
    if not claim_type:
        return False
    if claim_type not in _PERSISTABLE_GRAPH_CLAIM_TYPES:
        return False
    text = _claim_value_text(claim)
    if not text:
        return False
    if claim_type in {"opportunity", "commitment", "preference"} and _is_low_signal_text(text):
        return False
    return True


def _claim_ontology_supported(claim: dict[str, Any]) -> bool:
    if "ontology_supported" not in claim:
        return True
    return bool(claim.get("ontology_supported"))


def _is_crm_promotable_claim(claim: dict[str, Any]) -> bool:
    return _is_persistable_graph_claim(claim) and _claim_ontology_supported(claim)


def _filter_graph_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [claim for claim in claims if isinstance(claim, dict) and _is_persistable_graph_claim(claim)]


def _filter_crm_promotable_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [claim for claim in claims if isinstance(claim, dict) and _is_crm_promotable_claim(claim)]


def _split_claim_pipelines(claims: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    graph_context_claims = _filter_graph_claims(claims)
    crm_promotable_claims = _filter_crm_promotable_claims(graph_context_claims)
    return graph_context_claims, crm_promotable_claims


def _claim_evidence_refs(claim: dict[str, Any], *, max_refs: int = 4) -> list[dict[str, Any]]:
    refs = claim.get("evidence_refs")
    if not isinstance(refs, list):
        return []
    result: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        result.append(
            {
                "interaction_id": ref.get("interaction_id"),
                "chunk_id": ref.get("chunk_id"),
                "span_json": ref.get("span_json"),
            }
        )
        if len(result) >= max(1, max_refs):
            break
    return result


def _contradiction_task_payload(issue: dict[str, Any], *, interaction_id: str) -> dict[str, Any]:
    current_claim = issue.get("current_claim") if isinstance(issue.get("current_claim"), dict) else {}
    proposed_claim = issue.get("proposed_claim") if isinstance(issue.get("proposed_claim"), dict) else {}
    task_type = _normalized_text(issue.get("task_type")) or "claim_discrepancy"
    current_type = _normalized_text(current_claim.get("claim_type")) or "claim"
    proposed_type = _normalized_text(proposed_claim.get("claim_type")) or "claim"
    current_value = current_claim.get("value_json") if isinstance(current_claim.get("value_json"), dict) else {}
    proposed_value = proposed_claim.get("value_json") if isinstance(proposed_claim.get("value_json"), dict) else {}
    summary = (
        f"{task_type}: {current_type} changed from "
        f"{_normalized_text(current_value.get('object') or current_value.get('company') or current_value.get('label') or current_value)} "
        f"to {_normalized_text(proposed_value.get('object') or proposed_value.get('company') or proposed_value.get('label') or proposed_value)}"
    ).strip()
    return {
        "summary": summary,
        "current_claim": current_claim,
        "proposed_claim": proposed_claim,
        "interaction_id": interaction_id,
        "evidence_refs": {
            "current": _claim_evidence_refs(current_claim),
            "proposed": _claim_evidence_refs(proposed_claim),
        },
    }


def _claim_identity(claim: dict[str, object]) -> tuple[str, str]:
    claim_id = _normalized_text(claim.get("claim_id"))
    claim_type = _normalized_text(claim.get("claim_type")).lower() or "topic"
    value_json = claim.get("value_json")
    if isinstance(value_json, dict):
        try:
            serialized_value = json.dumps(value_json, sort_keys=True, ensure_ascii=True)
        except Exception:
            serialized_value = str(value_json)
    else:
        serialized_value = "{}"
    return claim_id, f"{claim_type}:{serialized_value}"


def _dedupe_claims(existing_claims: list[dict], candidate_claims: list[dict]) -> list[dict]:
    existing_ids: set[str] = set()
    existing_fingerprints: set[str] = set()
    for claim in existing_claims:
        claim_id, fingerprint = _claim_identity(claim)
        if claim_id:
            existing_ids.add(claim_id)
        existing_fingerprints.add(fingerprint)

    deduped: list[dict] = []
    for claim in candidate_claims:
        claim_id, fingerprint = _claim_identity(claim)
        if claim_id and claim_id in existing_ids:
            continue
        if fingerprint in existing_fingerprints:
            continue
        if claim_id:
            existing_ids.add(claim_id)
        existing_fingerprints.add(fingerprint)
        deduped.append(claim)
    return deduped


def _merge_relation_stats(*stats: dict[str, int]) -> dict[str, Any]:
    merged = {
        "persisted_relations": 0,
        "uncertain_relations": 0,
        "conflicting_relations": 0,
    }
    for entry in stats:
        for key in merged:
            merged[key] += int(entry.get(key, 0))
    return merged


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
        if not _is_crm_promotable_claim(claim):
            continue
        claim_id = _normalized_text(claim.get("claim_id"))
        if not claim_id or claim_id in seen_claim_ids:
            continue

        relation_payload = relation_payload_from_claim(claim)
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
        high_value_predicate = bool(relation_payload.get("high_value"))
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


def _hybrid_graph_vector_signals(db, contact_id: str, objective_seed: str) -> tuple[dict[str, Any], float, list[dict]]:
    graph_metrics = get_contact_graph_metrics(contact_id)
    graph_paths = get_contact_graph_paths(
        contact_id,
        objective=objective_seed,
        max_hops=3,
        limit=6,
        include_uncertain=False,
        lookback_days=365,
    )
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


def _extract_company_hint_from_claims(claims: list[dict]) -> str | None:
    for claim in claims:
        if not _claim_ontology_supported(claim):
            continue
        claim_type = _claim_type_name(claim)
        if claim_type not in {"employment", "opportunity", "commitment"}:
            continue
        value_json = claim.get("value_json") if isinstance(claim.get("value_json"), dict) else {}
        for key in ("company", "organization", "org", "target", "destination", "object"):
            value = value_json.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            candidate = value.strip()
            if _is_low_signal_text(candidate):
                continue
            return candidate
    return None


def _extract_opportunity_title(interaction: Interaction, claims: list[dict]) -> str:
    for claim in claims:
        claim_type = _claim_type_name(claim)
        if claim_type not in {"opportunity", "commitment"}:
            continue
        value_json = claim.get("value_json") if isinstance(claim.get("value_json"), dict) else {}
        for key in ("object", "label", "target"):
            value = value_json.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            candidate = value.strip()
            if _is_low_signal_text(candidate):
                continue
            return candidate[:160]
    subject = _normalized_text(interaction.subject)
    if subject and not _is_low_signal_text(subject):
        return subject[:160]
    return "Autodiscovered opportunity"


def _extract_motivator_signals(claims: list[dict]) -> list[str]:
    motivator_types = {"preference", "commitment", "opportunity", "personal_detail", "family", "education", "location"}
    signals: list[str] = []
    for claim in claims:
        if not _claim_ontology_supported(claim) and _claim_type_name(claim) == "topic":
            continue
        claim_type = _claim_type_name(claim)
        if claim_type not in motivator_types:
            continue
        value_json = claim.get("value_json") if isinstance(claim.get("value_json"), dict) else {}
        text = (
            _normalized_text(value_json.get("object"))
            or _normalized_text(value_json.get("label"))
            or _normalized_text(value_json.get("company"))
            or _normalized_text(value_json.get("predicate"))
        )
        if not text or _is_low_signal_text(text) or text in signals:
            continue
        signals.append(text)
        if len(signals) >= 8:
            break
    return signals


def _has_case_opportunity_signal(interaction: Interaction, claims: list[dict]) -> bool:
    for claim in claims:
        if not _claim_ontology_supported(claim) and _claim_type_name(claim) == "topic":
            continue
        claim_type = _claim_type_name(claim)
        if claim_type not in _CASE_OPPORTUNITY_CLAIM_TYPES:
            continue
        text = _claim_value_text(claim)
        if text and not _is_low_signal_text(text):
            return True

    subject = _normalized_text(interaction.subject).lower()
    if subject:
        for keyword in _OPPORTUNITY_SUBJECT_KEYWORDS:
            if keyword in subject:
                return True
    return False


def process_interaction(interaction_id: str) -> None:
    settings = get_settings()
    db = SessionLocal()
    interaction: Interaction | None = None
    try:
        interaction = db.scalar(select(Interaction).where(Interaction.interaction_id == interaction_id))
        if interaction is None:
            return
        interaction.processing_error = None

        raw_event = db.scalar(
            select(RawEvent)
            .where(
                RawEvent.source_system == interaction.source_system,
                RawEvent.external_id == interaction.external_id,
            )
        )
        body_text = ""
        raw_payload_json: dict[str, Any] = {}
        if raw_event:
            raw_payload_json = raw_event.payload_json if isinstance(raw_event.payload_json, dict) else {}
            body_text = raw_payload_json.get("body_plain", "")
        backfill_contact_mode = _interaction_backfill_mode(raw_payload_json)
        skip_mem0_for_backfill = bool(
            interaction.source_system == "gmail"
            and backfill_contact_mode in {"skip_previously_processed", "reprocess_all"}
        )

        hint = _interaction_contact_hint(raw_payload_json)
        hint_primary_email = _normalized_text(hint.get("primary_email")).lower() or None
        if hint_primary_email and is_internal_email(hint_primary_email):
            hint = {"contact_id": None, "primary_email": None, "display_name": None, "company_name": None}
            hinted_contact = None
        else:
            hinted_contact = _seed_or_resolve_hint_contact(
                db,
                hint_contact_id=hint.get("contact_id"),
                hint_primary_email=hint.get("primary_email"),
                hint_display_name=hint.get("display_name"),
            )

        (
            contact_ids,
            unresolved_emails,
            participant_names,
            internal_participant_names,
            name_match_provenance,
            speaker_resolution_suggestions,
        ) = _resolve_contact_ids(
            db, interaction.participants_json
        )
        if name_match_provenance or speaker_resolution_suggestions:
            participants_payload = dict(interaction.participants_json or {})
            participants_payload["match_provenance"] = {
                "contact_matches": name_match_provenance,
                "speaker_resolution_suggestions": speaker_resolution_suggestions,
            }
            interaction.participants_json = participants_payload
        hinted_contact_id = hinted_contact.contact_id if hinted_contact else (_normalized_text(hint.get("contact_id")) or None)
        hinted_email = _normalized_text(hint.get("primary_email")).lower() or None
        if hinted_contact_id and hinted_contact_id not in contact_ids:
            contact_ids = sorted(set(contact_ids + [hinted_contact_id]))
        if hinted_email:
            unresolved_emails = [email for email in unresolved_emails if email != hinted_email]
            if hinted_contact_id and hint.get("display_name") and hinted_email not in participant_names:
                participant_names[hinted_email] = _normalized_text(hint.get("display_name"))

        hinted_company_name = _normalized_text(hint.get("company_name"))
        if hinted_contact_id and hinted_company_name:
            existing_company_links = get_contact_company_links(hinted_contact_id)
            contact_sync_companies = [
                _normalized_text(item.get("company_name"))
                for item in existing_company_links
                if _normalized_text(item.get("source")) == "contact_sync" and _normalized_text(item.get("company_name"))
            ]
            should_apply_backfill_company_hint = True
            if contact_sync_companies:
                normalized_hint_company = hinted_company_name.casefold()
                if any(company.casefold() == normalized_hint_company for company in contact_sync_companies):
                    should_apply_backfill_company_hint = False
                else:
                    should_apply_backfill_company_hint = False
                    logger.info(
                        "Skipping backfill company hint because contact_sync company already exists.",
                        extra={
                            "contact_id": hinted_contact_id,
                            "hint_company": hinted_company_name,
                            "contact_sync_companies": contact_sync_companies,
                            "interaction_id": interaction.interaction_id,
                        },
                    )
            if should_apply_backfill_company_hint:
                resolved_email = (
                    _normalized_text(hinted_contact.primary_email).lower()
                    if hinted_contact and hinted_contact.primary_email
                    else hinted_email
                    or ""
                )
                resolved_display_name = (
                    _normalized_text(hinted_contact.display_name)
                    if hinted_contact and hinted_contact.display_name
                    else _normalized_text(hint.get("display_name"))
                    or participant_names.get(hinted_email or "", None)
                )
                merge_contact(
                    {
                        "contact_id": hinted_contact_id,
                        "primary_email": resolved_email,
                        "display_name": resolved_display_name or None,
                        "first_name": None,
                        "last_name": None,
                        "company": hinted_company_name or None,
                        "owner_user_id": None,
                    }
                )
                upsert_contact_company_relation(
                    contact_id=hinted_contact_id,
                    company_name=hinted_company_name,
                    source_system="contact_sheet_backfill_hint",
                    confidence=0.99,
                )
        provisional_contact_ids: list[str] = []
        merge_interaction(
            {
                "interaction_id": interaction.interaction_id,
                "type": interaction.type,
                "timestamp": interaction.timestamp.isoformat(),
                "source_system": interaction.source_system,
                "direction": interaction.direction,
                "thread_id": interaction.thread_id,
                "subject": interaction.subject,
            }
        )

        seen_internal_pairs: set[tuple[str, str]] = set()
        for bucket in ("from", "to", "cc"):
            for participant in interaction.participants_json.get(bucket, []):
                participant_email = _normalized_text(participant.get("email")).lower()
                if not participant_email or not is_internal_email(participant_email):
                    continue
                merge_internal_user(
                    {
                        "internal_user_id": None,
                        "primary_email": participant_email,
                        "display_name": internal_participant_names.get(participant_email)
                        or _normalized_text(participant.get("name"))
                        or None,
                    }
                )
                pair = (participant_email, bucket)
                if pair in seen_internal_pairs:
                    continue
                seen_internal_pairs.add(pair)
                attach_internal_user_interaction_role(participant_email, interaction.interaction_id, bucket)

        for email in unresolved_emails:
            case_contact = upsert_case_contact_v2(
                email=email,
                interaction_id=interaction.interaction_id,
                display_name=participant_names.get(email),
                promotion_reason="auto_discovered_from_interaction",
                gate_results={
                    "is_internal_email": False,
                    "autocreate": True,
                    "source_system": interaction.source_system,
                },
            )
            provisional_contact_id = _normalized_text(case_contact.get("provisional_contact_id"))
            if provisional_contact_id:
                provisional_contact_ids.append(provisional_contact_id)
                existing_cache = db.scalar(select(ContactCache).where(ContactCache.contact_id == provisional_contact_id))
                if existing_cache is None:
                    db.add(
                        ContactCache(
                            contact_id=provisional_contact_id,
                            primary_email=email,
                            display_name=participant_names.get(email),
                            owner_user_id=None,
                            use_sensitive_in_drafts=False,
                        )
                    )
                else:
                    existing_cache.primary_email = email
                    if participant_names.get(email):
                        existing_cache.display_name = participant_names.get(email)

        _enqueue_speaker_resolution_tasks(db, interaction, speaker_resolution_suggestions)

        all_contact_ids = sorted(set(contact_ids + provisional_contact_ids))
        interaction.contact_ids_json = all_contact_ids
        for contact_id in all_contact_ids:
            attach_contact_interaction(contact_id, interaction.interaction_id)

        chunks = _create_chunks_for_interaction(interaction, body_text)
        for chunk in chunks:
            db.add(chunk)
        db.commit()
        for chunk in chunks:
            db.refresh(chunk)

        if chunks:
            insert_chunk_embeddings(db, chunks, settings.embedding_model)

        try:
            candidates = extract_candidates(interaction.interaction_id, body_text)
        except Exception as exc:
            logger.exception(
                "interaction_cognee_extraction_failed",
                extra={"interaction_id": interaction.interaction_id},
            )
            create_extraction_event_v2(
                interaction_id=interaction.interaction_id,
                stage="interaction_processing",
                status="failed",
                extractor="cognee",
                source_system=interaction.source_system,
                error_message=str(exc),
            )
            create_resolution_task(
                db,
                contact_id=all_contact_ids[0] if all_contact_ids else "",
                task_type="extraction_failure",
                proposed_claim_id=f"extract_fail:{interaction.interaction_id}",
                current_claim_id=None,
                payload_json={
                    "interaction_id": interaction.interaction_id,
                    "source_system": interaction.source_system,
                    "entity_status": "rejected",
                    "promotion_reason": "extraction_failed",
                    "gate_results": {
                        "stage": "extract_candidates",
                        "error": str(exc),
                    },
                },
            )
            raise RuntimeError(f"Cognee extraction failed for interaction {interaction.interaction_id}") from exc
        proposed_claims = candidates_to_claims(candidates)
        graph_claim_candidates, crm_promotable_claim_candidates = _split_claim_pipelines(proposed_claims)

        evidence_refs = [_chunk_evidence_ref(interaction.interaction_id, c) for c in chunks[:3]]

        if settings.graph_v2_enabled:
            company_hint = _extract_company_hint_from_claims(crm_promotable_claim_candidates)
            opportunity_title = _extract_opportunity_title(interaction, graph_claim_candidates)
            motivator_signals = _extract_motivator_signals(graph_claim_candidates)
            if _has_case_opportunity_signal(interaction, graph_claim_candidates):
                best_opportunity = find_best_opportunity_for_interaction_v2(
                    thread_id=interaction.thread_id,
                    company_name=company_hint,
                    contact_ids=all_contact_ids,
                )
                if best_opportunity and best_opportunity.get("meets_threshold"):
                    opportunity_id = str(best_opportunity.get("opportunity_id"))
                    link_engagement_to_opportunity_v2(
                        interaction_id=interaction.interaction_id,
                        opportunity_id=opportunity_id,
                        source="matcher_v2",
                        score=float(best_opportunity.get("score", 0.0) or 0.0),
                    )
                    link_opportunity_contacts_v2(opportunity_id, all_contact_ids)
                else:
                    upsert_case_opportunity_v2(
                        interaction_id=interaction.interaction_id,
                        title=opportunity_title,
                        company_name=company_hint,
                        thread_id=interaction.thread_id,
                        promotion_reason="insufficient_match_to_existing_opportunity",
                        gate_results={
                            "entity_status": "provisional",
                            "promotion_reason": "insufficient_match_to_existing_opportunity",
                            "match_threshold": settings.graph_v2_case_opportunity_threshold,
                            "best_match": best_opportunity,
                            "case_gate": "passed",
                        },
                        motivators=motivator_signals,
                        contact_ids=all_contact_ids,
                    )
            else:
                logger.info(
                    "case_opportunity_skipped_low_signal",
                    extra={
                        "interaction_id": interaction.interaction_id,
                        "subject": interaction.subject,
                        "contact_ids": all_contact_ids,
                    },
                )

        for contact_id in all_contact_ids:
            prepared_candidates = []
            for claim in graph_claim_candidates:
                claim_copy = copy.deepcopy(claim)
                base_claim_id = _normalized_text(claim_copy.get("claim_id")) or str(uuid.uuid4())
                claim_copy["claim_id"] = _scope_claim_id(
                    base_claim_id,
                    contact_id=contact_id,
                    interaction_id=interaction.interaction_id,
                )
                claim_copy["evidence_refs"] = [
                    {
                        "interaction_id": interaction.interaction_id,
                        "chunk_id": ref["chunk_id"],
                        "span_json": ref.get("span_json", {}),
                    }
                    for ref in _claim_evidence_refs_for_chunks(
                        claim_copy,
                        interaction_id=interaction.interaction_id,
                        chunks=chunks,
                        default_limit=2,
                    )
                ]
                prepared_candidates.append(claim_copy)

            if prepared_candidates and not evidence_refs:
                create_resolution_task(
                    db,
                    contact_id=contact_id,
                    task_type="missing_evidence",
                    proposed_claim_id=f"missing_evidence:{interaction.interaction_id}:{contact_id}",
                    current_claim_id=None,
                    payload_json={
                        "interaction_id": interaction.interaction_id,
                        "contact_id": contact_id,
                        "entity_status": "rejected",
                        "promotion_reason": "missing_evidence_refs",
                        "gate_results": {"required_fields": ["interaction_id", "chunk_id", "span_json"]},
                    },
                )

            if prepared_candidates and evidence_refs:
                write_claims_with_evidence(contact_id, interaction.interaction_id, prepared_candidates, evidence_refs)
                for claim in prepared_candidates:
                    create_assertion_with_evidence_v2(
                        interaction_id=interaction.interaction_id,
                        contact_id=contact_id,
                        claim=claim,
                        evidence_refs=(
                            claim.get("evidence_refs") if isinstance(claim.get("evidence_refs"), list) and claim.get("evidence_refs") else evidence_refs
                        ),
                        source_system="cognee",
                        extractor="cognee",
                        entity_status="provisional",
                        gate_results={
                            "stage": "cognee_claims",
                            "has_required_evidence": True,
                        },
                    )
            graph_relation_stats_cognee = _persist_relation_claims_for_contact(
                db=db,
                contact_id=contact_id,
                interaction=interaction,
                claims=prepared_candidates,
                auto_accept_threshold=settings.auto_accept_threshold,
            )

            accepted_claims_after_cognee = get_contact_claims(contact_id, status="accepted")
            if skip_mem0_for_backfill:
                logger.info(
                    "interaction_mem0_skipped_for_backfill",
                    extra={
                        "interaction_id": interaction.interaction_id,
                        "contact_id": contact_id,
                        "backfill_contact_mode": backfill_contact_mode,
                    },
                )
                ops = []
            else:
                bundle = build_mem0_bundle(
                    interaction_summary=_summarize_interaction_body(body_text),
                    recent_claims=accepted_claims_after_cognee,
                    candidate_claims=prepared_candidates,
                    auto_accept_threshold=settings.auto_accept_threshold,
                    scope_ids={
                        "user_id": contact_id,
                        "agent_id": settings.mem0_agent_id,
                        "run_id": interaction.interaction_id,
                        "contact_id": contact_id,
                        "interaction_id": interaction.interaction_id,
                    },
                )
                try:
                    ops = propose_memory_ops(bundle)
                except Exception:
                    logger.exception(
                        "interaction_mem0_ops_failed",
                        extra={
                            "interaction_id": interaction.interaction_id,
                            "contact_id": contact_id,
                        },
                    )
                    ops = []
            new_claims = _claims_from_ops(
                ops,
                evidence_refs,
                interaction.interaction_id,
                contact_id,
            )
            new_claims = _dedupe_claims(prepared_candidates, new_claims)
            new_claims, new_claims_crm_promotable = _split_claim_pipelines(new_claims)
            if new_claims and evidence_refs:
                write_claims_with_evidence(contact_id, interaction.interaction_id, new_claims, evidence_refs)
                for claim in new_claims:
                    create_assertion_with_evidence_v2(
                        interaction_id=interaction.interaction_id,
                        contact_id=contact_id,
                        claim=claim,
                        evidence_refs=(
                            claim.get("evidence_refs") if isinstance(claim.get("evidence_refs"), list) and claim.get("evidence_refs") else evidence_refs
                        ),
                        source_system="mem0",
                        extractor="mem0",
                        entity_status="provisional",
                        gate_results={
                            "stage": "mem0_claims",
                            "has_required_evidence": True,
                        },
                    )

            contradictions = detect_contradictions(accepted_claims_after_cognee, new_claims)
            for issue in contradictions:
                existing_conflict_task = db.scalar(
                    select(ResolutionTask).where(
                        ResolutionTask.status == "open",
                        ResolutionTask.task_type == issue["task_type"],
                        ResolutionTask.contact_id == contact_id,
                        ResolutionTask.proposed_claim_id == issue["proposed_claim"]["claim_id"],
                        ResolutionTask.current_claim_id == issue["current_claim"]["claim_id"],
                    )
                )
                if existing_conflict_task is not None:
                    continue
                create_resolution_task(
                    db,
                    contact_id=contact_id,
                    task_type=issue["task_type"],
                    proposed_claim_id=issue["proposed_claim"]["claim_id"],
                    current_claim_id=issue["current_claim"]["claim_id"],
                    payload_json=_contradiction_task_payload(issue, interaction_id=interaction.interaction_id),
                )

            graph_relation_stats_mem0 = _persist_relation_claims_for_contact(
                db=db,
                contact_id=contact_id,
                interaction=interaction,
                claims=new_claims_crm_promotable,
                auto_accept_threshold=settings.auto_accept_threshold,
            )
            graph_relation_stats = _merge_relation_stats(graph_relation_stats_cognee, graph_relation_stats_mem0)
            graph_relation_stats["by_stage"] = {
                "cognee": graph_relation_stats_cognee,
                "mem0": graph_relation_stats_mem0,
            }

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
            context_signals_v2 = get_contact_context_signals_v2(contact_id, limit=10) if settings.graph_v2_enabled else []
            open_case_counts = get_open_case_counts_for_contact(contact_id) if settings.graph_v2_enabled else {
                "open_case_contacts": 0,
                "open_case_opportunities": 0,
            }
            motivator_signal_count = sum(
                1 for signal in context_signals_v2 if _normalized_text(signal.get("claim_type")) in {"preference", "opportunity", "commitment"}
            )
            graph_warmth_bonus = min(
                5.0,
                graph_metrics.get("recent_relation_count", 0) * 0.35 + vector_alignment * 4.0 + motivator_signal_count * 0.25,
            )
            graph_depth_bonus = min(
                10,
                int(
                    round(
                        graph_metrics.get("entity_reach_2hop", 0) * 0.40
                        + graph_metrics.get("path_count_2hop", 0) * 0.20
                        + len(context_signals_v2) * 0.30
                    )
                ),
            )
            graph_trigger_bonus = min(
                8.0,
                graph_metrics.get("recent_opportunity_edge_count", 0) * 1.8
                + graph_metrics.get("recent_relation_count", 0) * 0.25
                + graph_metrics.get("uncertain_relation_count", 0) * 0.35
                - graph_metrics.get("stale_opportunity_edge_count", 0) * 0.35,
            )
            warmth_for_score = warmth_delta + graph_warmth_bonus
            depth_for_score = depth_count
            if warmth_depth_meta.get("source") != "llm":
                depth_for_score = depth_count + len(accepted_claims_after_cognee) + len(new_claims)
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
            relationship_components["graph_latest_relation_at"] = graph_metrics.get("latest_relation_at")
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
            open_case_penalty = min(
                15.0,
                open_case_counts.get("open_case_contacts", 0) * 1.5
                + open_case_counts.get("open_case_opportunities", 0) * 2.5,
            )
            priority = max(0.0, round(priority - open_case_penalty, 2))
            priority_components["inactivity_days"] = inactivity_days
            priority_components["open_loop_count"] = open_loops
            priority_components["trigger_score"] = trigger_score
            priority_components["graph_trigger_bonus"] = round(graph_trigger_bonus, 3)
            priority_components["graph_recent_relation_count"] = int(graph_metrics.get("recent_relation_count", 0))
            priority_components["graph_uncertain_relation_count"] = int(graph_metrics.get("uncertain_relation_count", 0))
            priority_components["graph_opportunity_edge_count"] = int(graph_metrics.get("opportunity_edge_count", 0))
            priority_components["graph_recent_opportunity_edge_count"] = int(graph_metrics.get("recent_opportunity_edge_count", 0))
            priority_components["graph_stale_opportunity_edge_count"] = int(graph_metrics.get("stale_opportunity_edge_count", 0))
            priority_components["graph_priority_trigger_input"] = round(trigger_for_score, 3)
            priority_components["open_case_penalty"] = round(open_case_penalty, 3)
            priority_components["open_case_contacts"] = int(open_case_counts.get("open_case_contacts", 0))
            priority_components["open_case_opportunities"] = int(open_case_counts.get("open_case_opportunities", 0))
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
                        "context_signals_v2": context_signals_v2[:8],
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

        if settings.shacl_validation_enabled and settings.shacl_validation_on_write:
            shacl_result = run_shacl_validation_v2(interaction_id=interaction.interaction_id)
            if shacl_result.get("enabled") and not shacl_result.get("valid", False):
                create_resolution_task(
                    db,
                    contact_id=all_contact_ids[0] if all_contact_ids else "",
                    task_type="shacl_validation_failure",
                    proposed_claim_id=f"shacl_fail:{interaction.interaction_id}",
                    current_claim_id=None,
                    payload_json={
                        "interaction_id": interaction.interaction_id,
                        "entity_status": "rejected",
                        "promotion_reason": "shacl_validation_failure",
                        "gate_results": shacl_result,
                    },
                )
                raise RuntimeError(f"SHACL validation failed for interaction {interaction.interaction_id}")

        interaction.status = "processed"
        interaction.processing_error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        if interaction is not None:
            try:
                interaction.status = "failed"
                interaction.processing_error = str(exc)[:4000]
                db.commit()
            except Exception:
                db.rollback()
        raise
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
    settings = get_settings()
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
            context_signals_v2 = get_contact_context_signals_v2(contact.contact_id, limit=10) if settings.graph_v2_enabled else []
            open_case_counts = get_open_case_counts_for_contact(contact.contact_id) if settings.graph_v2_enabled else {
                "open_case_contacts": 0,
                "open_case_opportunities": 0,
            }
            motivator_signal_count = sum(
                1
                for signal in context_signals_v2
                if _normalized_text(signal.get("claim_type")) in {"preference", "opportunity", "commitment"}
            )
            graph_warmth_bonus = min(
                5.0,
                graph_metrics.get("recent_relation_count", 0) * 0.35 + vector_alignment * 4.0 + motivator_signal_count * 0.25,
            )
            graph_depth_bonus = min(
                10,
                int(
                    round(
                        graph_metrics.get("entity_reach_2hop", 0) * 0.40
                        + graph_metrics.get("path_count_2hop", 0) * 0.20
                        + len(context_signals_v2) * 0.30
                    )
                ),
            )
            graph_trigger_bonus = min(
                8.0,
                graph_metrics.get("recent_opportunity_edge_count", 0) * 1.8
                + graph_metrics.get("recent_relation_count", 0) * 0.25
                + graph_metrics.get("uncertain_relation_count", 0) * 0.35
                - graph_metrics.get("stale_opportunity_edge_count", 0) * 0.35,
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
            relationship_components["graph_latest_relation_at"] = graph_metrics.get("latest_relation_at")
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
            open_case_penalty = min(
                15.0,
                open_case_counts.get("open_case_contacts", 0) * 1.5
                + open_case_counts.get("open_case_opportunities", 0) * 2.5,
            )
            priority = max(0.0, round(priority - open_case_penalty, 2))
            priority_components["inactivity_days"] = inactivity_days
            priority_components["open_loop_count"] = open_loops
            priority_components["trigger_score"] = trigger_score
            priority_components["graph_trigger_bonus"] = round(graph_trigger_bonus, 3)
            priority_components["graph_recent_relation_count"] = int(graph_metrics.get("recent_relation_count", 0))
            priority_components["graph_uncertain_relation_count"] = int(graph_metrics.get("uncertain_relation_count", 0))
            priority_components["graph_opportunity_edge_count"] = int(graph_metrics.get("opportunity_edge_count", 0))
            priority_components["graph_recent_opportunity_edge_count"] = int(graph_metrics.get("recent_opportunity_edge_count", 0))
            priority_components["graph_stale_opportunity_edge_count"] = int(graph_metrics.get("stale_opportunity_edge_count", 0))
            priority_components["graph_priority_trigger_input"] = round(trigger_for_score, 3)
            priority_components["open_case_penalty"] = round(open_case_penalty, 3)
            priority_components["open_case_contacts"] = int(open_case_counts.get("open_case_contacts", 0))
            priority_components["open_case_opportunities"] = int(open_case_counts.get("open_case_opportunities", 0))
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
                        "context_signals_v2": context_signals_v2[:8],
                    },
                },
            )
    finally:
        db.close()


def run_inference() -> dict[str, Any]:
    settings = get_settings()
    return run_inference_rules_v2(
        min_confidence=settings.graph_v2_inference_min_confidence,
        max_age_days=settings.graph_v2_inference_max_age_days,
    )


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
