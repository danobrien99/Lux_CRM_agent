from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.deps import get_db
from app.api.v1.schemas import (
    ContactProfile,
    ContactScoreDetailResponse,
    ContactScoreItem,
    InteractionSummary,
    InteractionSummaryRefreshResponse,
    ScoreComponentBreakdown,
    ScoreReason,
    ScoreTodayResponse,
    ScoreTrendPoint,
)
from app.core.config import get_settings
from app.db.neo4j.queries import (
    get_contact_claims,
    get_contact_graph_metrics,
    get_contact_graph_paths,
    get_contact_company_hint,
    get_contact_company_hints,
    get_contact_score_snapshots,
    get_latest_score_snapshots,
)
from app.db.pg.models import Chunk, ContactCache, Interaction
from app.services.prompts import render_prompt

router = APIRouter(prefix="/scores", tags=["scores"])
logger = logging.getLogger(__name__)


def _summary_cache_key(contact_id: str) -> str:
    return f"interaction_summary:v1:{contact_id}"


@lru_cache(maxsize=1)
def _summary_cache_client() -> Redis | None:
    settings = get_settings()
    if not settings.interaction_summary_cache_enabled:
        return None
    try:
        return Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        logger.exception("interaction_summary_cache_client_init_failed")
        return None


def get_cached_interaction_summary(contact_id: str) -> InteractionSummary | None:
    settings = get_settings()
    if not settings.interaction_summary_cache_enabled:
        return None

    client = _summary_cache_client()
    if client is None:
        return None

    try:
        raw = client.get(_summary_cache_key(contact_id))
    except Exception:
        logger.exception("interaction_summary_cache_read_failed", extra={"contact_id": contact_id})
        return None

    if not raw:
        return None

    try:
        payload = json.loads(raw)
        return InteractionSummary.model_validate(payload)
    except Exception:
        logger.exception("interaction_summary_cache_payload_invalid", extra={"contact_id": contact_id})
        return None


def _write_cached_interaction_summary(contact_id: str, summary: InteractionSummary) -> None:
    settings = get_settings()
    if not settings.interaction_summary_cache_enabled:
        return

    client = _summary_cache_client()
    if client is None:
        return

    try:
        client.setex(
            _summary_cache_key(contact_id),
            settings.interaction_summary_cache_ttl_seconds,
            json.dumps(summary.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":")),
        )
    except Exception:
        logger.exception("interaction_summary_cache_write_failed", extra={"contact_id": contact_id})


def invalidate_cached_interaction_summary(contact_id: str) -> None:
    client = _summary_cache_client()
    if client is None:
        return
    try:
        client.delete(_summary_cache_key(contact_id))
    except Exception:
        logger.exception("interaction_summary_cache_delete_failed", extra={"contact_id": contact_id})


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, default: int | None = 0) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_text(value: str, max_chars: int | None = None) -> str:
    normalized = " ".join(value.split()).strip()
    if max_chars is None or len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip()}..."


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if not raw:
        return None

    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _extract_recent_topics_from_text(excerpts: list[str], limit: int = 4) -> list[str]:
    if not excerpts:
        return ["General relationship follow-up"][:limit]

    text_blob = " ".join(excerpts).lower()
    keyword_map = [
        ("pricing", "Pricing and packaging"),
        ("proposal", "Proposal review"),
        ("contract", "Contract and legal terms"),
        ("renewal", "Renewal discussion"),
        ("timeline", "Timeline and milestones"),
        ("kickoff", "Project kickoff and rollout"),
        ("demo", "Product demo follow-up"),
        ("pilot", "Pilot planning"),
        ("budget", "Budget alignment"),
        ("integration", "Integration planning"),
    ]
    topics: list[str] = []
    for keyword, label in keyword_map:
        if keyword in text_blob and label not in topics:
            topics.append(label)
        if len(topics) >= limit:
            break

    if topics:
        return topics

    return ["General relationship follow-up"][:limit]


def _stub_priority_next_step(
    *,
    company_name: str | None,
    total_interactions: int,
    interaction_count_30d: int,
    recent_topics: list[str],
) -> str:
    company = (company_name or "").strip()
    contact_label = company or "this contact"
    topic = recent_topics[0] if recent_topics else None

    if topic:
        return (
            f"Stub: verify open opportunities in HubSpot for {contact_label}, then send a follow-up tied to "
            f"\"{topic}\" with one concrete next meeting ask."
        )
    if interaction_count_30d > 0:
        return (
            f"Stub: review the latest active thread for {contact_label}, confirm current opportunity stage in HubSpot, "
            "and propose the next milestone with date options."
        )
    if total_interactions > 0:
        return (
            f"Stub: re-engage {contact_label} with a short status check, then map the reply to a HubSpot opportunity "
            "once sync data is available."
        )
    return "Stub: no interactions yet. After HubSpot sync, prioritize the top open opportunity and send an intro touchpoint."


def _interaction_excerpt_map(
    db: Session,
    interaction_ids: list[str],
    *,
    max_chars_per_interaction: int,
) -> dict[str, str]:
    if not interaction_ids:
        return {}

    rows = db.execute(
        select(Chunk.interaction_id, Chunk.text)
        .where(Chunk.interaction_id.in_(interaction_ids))
        .order_by(Chunk.created_at.asc())
    ).all()

    by_interaction: dict[str, str] = {}
    for interaction_id, text in rows:
        normalized = _normalize_text(str(text or ""))
        if not normalized:
            continue

        existing = by_interaction.get(interaction_id, "")
        combined = f"{existing} {normalized}".strip() if existing else normalized
        if len(combined) > max_chars_per_interaction:
            combined = combined[:max_chars_per_interaction].rstrip()
        by_interaction[interaction_id] = combined

    return by_interaction


def _interaction_context_for_llm(db: Session, contact_interactions: list[Interaction]) -> list[dict[str, str]]:
    settings = get_settings()
    recent = contact_interactions[: settings.scoring_llm_max_interactions]
    interaction_ids = [interaction.interaction_id for interaction in recent]
    excerpt_map = _interaction_excerpt_map(
        db,
        interaction_ids,
        max_chars_per_interaction=settings.scoring_llm_snippet_chars,
    )

    context: list[dict[str, str]] = []
    for interaction in recent:
        excerpt = excerpt_map.get(interaction.interaction_id)
        if not excerpt:
            continue
        context.append(
            {
                "interaction_id": interaction.interaction_id,
                "timestamp": _as_utc(interaction.timestamp).isoformat(),
                "direction": interaction.direction,
                "excerpt": excerpt,
            }
        )
    return context


def _summarize_recent_interactions_with_openai(
    *,
    model: str,
    api_key: str,
    context_payload: dict[str, Any],
) -> tuple[str | None, list[str], str | None]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    messages = [
        {
            "role": "system",
            "content": render_prompt("interaction_summary_system"),
        },
        {
            "role": "user",
            "content": render_prompt("interaction_summary_user", context_json=json.dumps(context_payload, ensure_ascii=True)),
        },
    ]
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    content = (response.choices[0].message.content or "").strip()
    payload = _extract_json_object(content) or {}

    summary_raw = payload.get("summary")
    summary = _normalize_text(str(summary_raw), max_chars=420) if isinstance(summary_raw, str) and summary_raw.strip() else None

    topics_raw = payload.get("recent_topics")
    topics: list[str] = []
    if isinstance(topics_raw, list):
        for item in topics_raw:
            if not isinstance(item, str):
                continue
            cleaned = _normalize_text(item, max_chars=70)
            if not cleaned or cleaned in topics:
                continue
            topics.append(cleaned)
            if len(topics) >= 4:
                break

    next_step_raw = payload.get("priority_next_step")
    priority_next_step = (
        _normalize_text(next_step_raw, max_chars=260) if isinstance(next_step_raw, str) and next_step_raw.strip() else None
    )
    if priority_next_step and not priority_next_step.lower().startswith("stub:"):
        priority_next_step = f"Stub: {priority_next_step}"

    return summary, topics, priority_next_step


def _normalize_components(snapshot: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    payload = snapshot or {}
    components_json = payload.get("components_json")
    if not isinstance(components_json, dict):
        return {}, {}, {}

    relationship = dict(components_json.get("relationship") or {})
    priority = dict(components_json.get("priority") or {})
    graph = dict(components_json.get("graph") or {})

    warmth_depth_source = relationship.get("warmth_depth_source")
    if warmth_depth_source is not None:
        relationship["warmth_depth_source_label"] = _warmth_depth_source_label(warmth_depth_source)

    if "inactivity_component" not in priority and "inactivity" in priority:
        priority["inactivity_component"] = priority.get("inactivity")

    if "open_loop_count" not in priority and "open_loops" in priority:
        open_loop_component = _coerce_float(priority.get("open_loops"), 0.0)
        priority["open_loop_count"] = max(0, int(round(open_loop_component / 5.0)))

    if "trigger_score" not in priority and "triggers" in priority:
        priority["trigger_score"] = _coerce_float(priority.get("triggers"), 0.0)

    if graph:
        metrics = graph.get("metrics")
        if isinstance(metrics, dict):
            graph["metrics"] = metrics
    return relationship, priority, graph


def _warmth_depth_source_label(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, dict):
        source = str(raw.get("source") or "unknown").strip()
        model = str(raw.get("model") or "").strip()
        if model:
            return f"{source} ({model})"
        return source
    return "unknown"


def _derive_why_now(
    relationship_score: float,
    relationship_components: dict[str, Any],
    priority_components: dict[str, Any],
) -> str:
    open_loop_count = _coerce_int(priority_components.get("open_loop_count"), 0) or 0
    trigger_score = _coerce_float(priority_components.get("trigger_score"), 0.0)
    days_since_last = _coerce_int(relationship_components.get("days_since_last"), None)

    if relationship_score <= 0.0 and days_since_last is None:
        return "No stored relationship score yet. Process new interactions to create a score snapshot."
    if open_loop_count > 0:
        return f"{open_loop_count} open inbound thread(s) need follow-up."
    if trigger_score > 0:
        return "Recent trigger language suggests timely outreach."
    if days_since_last is not None and days_since_last >= 30:
        return f"{days_since_last} days since last interaction."
    if days_since_last is not None and days_since_last >= 14:
        return f"{days_since_last} days since last interaction. Reconnect to maintain momentum."
    return "Maintain momentum from recent activity."


def _build_score_reason(
    asof: str,
    relationship_components: dict[str, Any],
    priority_components: dict[str, Any],
    graph_components: dict[str, Any] | None = None,
) -> ScoreReason:
    days_since_last = _coerce_int(relationship_components.get("days_since_last"), None)
    open_loop_count = _coerce_int(priority_components.get("open_loop_count"), 0) or 0
    trigger_score = _coerce_float(priority_components.get("trigger_score"), 0.0)

    highlights: list[str] = []
    if days_since_last is not None:
        highlights.append(f"{days_since_last} days since last interaction")
    if open_loop_count > 0:
        highlights.append(f"{open_loop_count} open loop(s)")
    if trigger_score > 0:
        highlights.append(f"trigger score {round(trigger_score, 1)}")

    summary = f"Stored score snapshot from {asof}."
    if highlights:
        summary = f"{summary} Highlights: {', '.join(highlights)}."

    return ScoreReason(
        summary=summary,
        evidence_refs=[
            {"component": "relationship", "values": relationship_components},
            {"component": "priority", "values": priority_components},
            {"component": "graph", "values": graph_components or {}},
            {"snapshot_asof": asof},
        ],
    )


def _build_score_item(
    *,
    contact_id: str,
    display_name: str | None,
    primary_email: str | None,
    company: str | None,
    snapshot: dict[str, Any] | None,
) -> ContactScoreItem:
    if snapshot is None:
        return ContactScoreItem(
            contact_id=contact_id,
            display_name=display_name,
            primary_email=primary_email,
            company=company,
            relationship_score=0.0,
            priority_score=0.0,
            why_now="No stored score snapshot yet. Ingest interactions to generate scores.",
            reasons=[
                ScoreReason(
                    summary="No score snapshot found for this contact.",
                    evidence_refs=[],
                )
            ],
        )

    asof = str(snapshot.get("asof") or "unknown")
    relationship_components, priority_components, graph_components = _normalize_components(snapshot)
    relationship_score = round(_coerce_float(snapshot.get("relationship_score"), 0.0), 2)
    priority_score = round(_coerce_float(snapshot.get("priority_score"), 0.0), 2)
    why_now = _derive_why_now(relationship_score, relationship_components, priority_components)

    return ContactScoreItem(
        contact_id=contact_id,
        display_name=display_name,
        primary_email=primary_email,
        company=company,
        relationship_score=relationship_score,
        priority_score=priority_score,
        why_now=why_now,
        reasons=[_build_score_reason(asof, relationship_components, priority_components, graph_components)],
    )


def _interactions_for_contact(db: Session, contact_id: str) -> list[Interaction]:
    interactions = db.scalars(select(Interaction).order_by(Interaction.timestamp.desc()).limit(1000)).all()
    return [interaction for interaction in interactions if contact_id in (interaction.contact_ids_json or [])]


@router.get("/today", response_model=ScoreTodayResponse)
def today_scores(limit: int = 50, db: Session = Depends(get_db)) -> ScoreTodayResponse:
    contacts = db.scalars(select(ContactCache)).all()
    contact_ids = [contact.contact_id for contact in contacts]
    company_hints = get_contact_company_hints(contact_ids)
    snapshots_by_contact = get_latest_score_snapshots(contact_ids)

    items: list[ContactScoreItem] = []
    for contact in contacts:
        items.append(
            _build_score_item(
                contact_id=contact.contact_id,
                display_name=contact.display_name,
                primary_email=contact.primary_email,
                company=company_hints.get(contact.contact_id),
                snapshot=snapshots_by_contact.get(contact.contact_id),
            )
        )

    items.sort(key=lambda item: item.priority_score, reverse=True)
    return ScoreTodayResponse(asof=datetime.now(timezone.utc), items=items[:limit])


def _extract_company_name(contact_id: str) -> str | None:
    try:
        accepted_claims = get_contact_claims(contact_id, status="accepted")
    except Exception:
        accepted_claims = []
    for claim in accepted_claims:
        if str(claim.get("claim_type") or "") != "employment":
            continue
        value_json = claim.get("value_json") or {}
        if not isinstance(value_json, dict):
            continue
        for key in ("company", "employer", "organization", "org", "target", "destination", "object"):
            value = value_json.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    try:
        return get_contact_company_hint(contact_id)
    except Exception:
        return None


def _build_interaction_summary(
    db: Session,
    contact_interactions: list[Interaction],
    now: datetime,
    *,
    contact_id: str,
    display_name: str | None,
    company_name: str | None,
) -> InteractionSummary:
    total_interactions = len(contact_interactions)
    interaction_count_30d = sum(1 for interaction in contact_interactions if (now - _as_utc(interaction.timestamp)).days <= 30)
    interaction_count_90d = sum(1 for interaction in contact_interactions if (now - _as_utc(interaction.timestamp)).days <= 90)
    inbound_count = sum(1 for interaction in contact_interactions if interaction.direction == "in")
    outbound_count = sum(1 for interaction in contact_interactions if interaction.direction == "out")
    last_interaction = contact_interactions[0] if contact_interactions else None

    seen_subjects: set[str] = set()
    recent_subjects: list[str] = []
    for interaction in contact_interactions:
        subject = (interaction.subject or "").strip()
        if not subject:
            continue
        normalized = subject.lower()
        if normalized in seen_subjects:
            continue
        seen_subjects.add(normalized)
        recent_subjects.append(subject)
        if len(recent_subjects) >= 3:
            break

    summary_source = "heuristic"
    priority_next_step_source = "stub_email_contact_context"
    context_for_llm = _interaction_context_for_llm(db, contact_interactions)
    context_excerpts = [entry.get("excerpt", "") for entry in context_for_llm if entry.get("excerpt")]
    recent_topics = _extract_recent_topics_from_text(context_excerpts, limit=4)
    graph_paths = get_contact_graph_paths(
        contact_id,
        objective=" ".join(context_excerpts[:2]) if context_excerpts else None,
        max_hops=2,
        limit=4,
        include_uncertain=False,
        lookback_days=365,
    )
    graph_metrics = get_contact_graph_metrics(contact_id)
    graph_topic_hints: list[str] = []
    for path in graph_paths:
        path_text = path.get("path_text") if isinstance(path, dict) else None
        if not isinstance(path_text, str):
            continue
        cleaned = _normalize_text(path_text, max_chars=72)
        if not cleaned:
            continue
        graph_topic_hints.append(cleaned)
        if len(graph_topic_hints) >= 2:
            break
    if graph_topic_hints:
        for hint in graph_topic_hints:
            if hint not in recent_topics:
                recent_topics.append(hint)
            if len(recent_topics) >= 4:
                break
    priority_next_step = _stub_priority_next_step(
        company_name=company_name,
        total_interactions=total_interactions,
        interaction_count_30d=interaction_count_30d,
        recent_topics=recent_topics,
    )

    if total_interactions == 0:
        brief = "No past interactions have been ingested for this contact yet."
    else:
        last_iso = _as_utc(last_interaction.timestamp).date().isoformat() if last_interaction else "unknown date"
        brief = (
            f"{total_interactions} interactions captured. "
            f"Last interaction date: {last_iso}. "
            f"Inbound: {inbound_count}, outbound: {outbound_count}."
        )
        if graph_metrics.get("path_count_2hop", 0) > 0:
            brief = (
                f"{brief} "
                f"Graph paths: {int(graph_metrics.get('path_count_2hop', 0))}, "
                f"entities in reach: {int(graph_metrics.get('entity_reach_2hop', 0))}."
            )
        if graph_metrics.get("opportunity_edge_count", 0) > 0:
            priority_next_step = (
                f"Stub: prioritize {int(graph_metrics.get('opportunity_edge_count', 0))} graph-linked opportunity signal(s), "
                "confirm opportunity stage in HubSpot, and send one date-driven next-step email."
            )
            priority_next_step_source = "stub_graph"

        settings = get_settings()
        if context_for_llm and settings.llm_provider.strip().lower() == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if api_key:
                llm_payload = {
                    "contact": {
                        "contact_id": contact_id,
                        "display_name": display_name,
                        "company": company_name,
                    },
                    "interaction_stats": {
                        "total_interactions": total_interactions,
                        "interaction_count_30d": interaction_count_30d,
                        "interaction_count_90d": interaction_count_90d,
                        "inbound_count": inbound_count,
                        "outbound_count": outbound_count,
                    },
                    "graph_signals": {
                        "metrics": graph_metrics,
                        "paths": graph_paths[:3],
                    },
                    "recent_interaction_excerpts": context_for_llm,
                }
                try:
                    llm_summary, llm_topics, llm_next_step = _summarize_recent_interactions_with_openai(
                        model=settings.llm_model,
                        api_key=api_key,
                        context_payload=llm_payload,
                    )
                    if llm_summary:
                        brief = llm_summary
                        summary_source = "llm"
                    if llm_topics:
                        recent_topics = llm_topics
                    if llm_next_step:
                        priority_next_step = llm_next_step
                        priority_next_step_source = "stub_llm"
                except Exception:
                    logger.exception(
                        "interaction_summary_llm_failed_fallback_heuristic",
                        extra={"contact_id": contact_id, "llm_model": settings.llm_model},
                    )

    return InteractionSummary(
        total_interactions=total_interactions,
        interaction_count_30d=interaction_count_30d,
        interaction_count_90d=interaction_count_90d,
        inbound_count=inbound_count,
        outbound_count=outbound_count,
        last_interaction_at=_as_utc(last_interaction.timestamp) if last_interaction else None,
        last_subject=last_interaction.subject if last_interaction else None,
        recent_subjects=recent_subjects,
        recent_topics=recent_topics,
        priority_next_step=priority_next_step,
        summary_source=summary_source,
        priority_next_step_source=priority_next_step_source,
        brief=brief,
    )


def refresh_cached_interaction_summary(
    db: Session,
    contact_id: str,
    *,
    display_name: str | None = None,
    company_name: str | None = None,
) -> InteractionSummary:
    contact = db.get(ContactCache, contact_id)
    now = datetime.now(timezone.utc)
    resolved_display_name = display_name
    if resolved_display_name is None and contact is not None:
        resolved_display_name = contact.display_name

    resolved_company = company_name if company_name is not None else _extract_company_name(contact_id)
    contact_interactions = _interactions_for_contact(db, contact_id)
    summary = _build_interaction_summary(
        db,
        contact_interactions,
        now,
        contact_id=contact_id,
        display_name=resolved_display_name,
        company_name=resolved_company,
    )
    _write_cached_interaction_summary(contact_id, summary)
    return summary


def _build_score_components(current: ContactScoreItem | None) -> ScoreComponentBreakdown | None:
    if current is None or not current.reasons:
        return None

    relationship: dict[str, Any] = {}
    priority: dict[str, Any] = {}
    for evidence in current.reasons[0].evidence_refs:
        component = evidence.get("component")
        values = evidence.get("values")
        if component == "relationship" and isinstance(values, dict):
            relationship.update(values)
        elif component == "priority" and isinstance(values, dict):
            priority.update(values)

    return ScoreComponentBreakdown(relationship=relationship, priority=priority)


def _build_trend(snapshots: list[dict[str, Any]]) -> list[ScoreTrendPoint]:
    trend: list[ScoreTrendPoint] = []
    for snapshot in reversed(snapshots):
        asof = snapshot.get("asof")
        if not isinstance(asof, str):
            continue
        relationship_components, priority_components, graph_components = _normalize_components(snapshot)
        trend.append(
            ScoreTrendPoint(
                asof=asof,
                relationship_score=round(_coerce_float(snapshot.get("relationship_score"), 0.0), 2),
                priority_score=round(_coerce_float(snapshot.get("priority_score"), 0.0), 2),
                components=[
                    {"component": "relationship", "values": relationship_components},
                    {"component": "priority", "values": priority_components},
                    {"component": "graph", "values": graph_components},
                ],
            )
        )
    return trend


@router.get("/contact/{contact_id}", response_model=ContactScoreDetailResponse)
def contact_score_detail(contact_id: str, db: Session = Depends(get_db)) -> ContactScoreDetailResponse:
    contact = db.get(ContactCache, contact_id)
    company_name = _extract_company_name(contact_id)

    interaction_summary = get_cached_interaction_summary(contact_id)
    if interaction_summary is None:
        interaction_summary = refresh_cached_interaction_summary(
            db,
            contact_id,
            display_name=contact.display_name if contact else None,
            company_name=company_name,
        )

    snapshots = get_contact_score_snapshots(contact_id=contact_id, limit=30)
    latest_snapshot = snapshots[0] if snapshots else None

    current = _build_score_item(
        contact_id=contact_id,
        display_name=contact.display_name if contact else None,
        primary_email=contact.primary_email if contact else None,
        company=company_name,
        snapshot=latest_snapshot,
    )
    if latest_snapshot is None:
        current = None

    score_components = _build_score_components(current)

    profile: ContactProfile | None = None
    if contact:
        profile = ContactProfile(
            contact_id=contact.contact_id,
            display_name=contact.display_name,
            primary_email=contact.primary_email,
            owner_user_id=contact.owner_user_id,
            company=company_name,
        )
    elif current:
        profile = ContactProfile(
            contact_id=current.contact_id,
            display_name=current.display_name,
            primary_email=current.primary_email,
            owner_user_id=None,
            company=current.company,
        )

    trend = _build_trend(snapshots)
    return ContactScoreDetailResponse(
        contact_id=contact_id,
        profile=profile,
        interaction_summary=interaction_summary,
        score_components=score_components,
        trend=trend,
        current=current,
    )


@router.post("/contact/{contact_id}/refresh_summary", response_model=InteractionSummaryRefreshResponse)
def refresh_contact_interaction_summary(contact_id: str, db: Session = Depends(get_db)) -> InteractionSummaryRefreshResponse:
    contact = db.get(ContactCache, contact_id)
    company_name = _extract_company_name(contact_id)
    summary = refresh_cached_interaction_summary(
        db,
        contact_id,
        display_name=contact.display_name if contact else None,
        company_name=company_name,
    )
    return InteractionSummaryRefreshResponse(
        contact_id=contact_id,
        refreshed=True,
        interaction_summary=summary,
    )
