from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings
from app.db.neo4j.driver import neo4j_session
from app.services.ontology.runtime_contract import (
    lpg_node_ontology_class,
    lpg_node_neo4j_label_identifier,
    lpg_relationship_ontology_predicate,
    lpg_relationship_neo4j_type_identifier,
    ontology_term_to_neo4j_identifier,
    predicate_token_to_ontology_property,
)


_CONTACT_RELATION_ALIASES = {
    "contact",
    "this contact",
    "recipient",
    "prospect",
    "lead",
    "person",
}
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
}
_LOW_SIGNAL_GRAPH_PATH_TERMS = {
    "call",
    "catchup",
    "chat",
    "discussion",
    "email",
    "follow up",
    "followup",
    "hello",
    "hi",
    "message",
    "note",
    "thanks",
    "update",
}
_LEGACY_SCORING_PREDICATE_EXCLUSIONS = {
    "discussed_topic",
    "contains",
}


def _v2_graph_reads_enabled() -> bool:
    settings = get_settings()
    return bool(settings.graph_v2_enabled and settings.graph_v2_read_v2)


def _v2_physical_projection_writes_enabled() -> bool:
    settings = get_settings()
    return bool(settings.graph_v2_enabled and settings.graph_v2_dual_write)


def _ontology_native_physical_mode_enabled() -> bool:
    settings = get_settings()
    return bool(settings.graph_v2_enabled and settings.graph_v2_read_v2 and not settings.graph_v2_dual_write)


def _legacy_graph_writes_enabled() -> bool:
    settings = get_settings()
    if not settings.graph_v2_enabled:
        return True
    # During cutover, dual-write keeps legacy projection alive. Once V2 reads are enabled and
    # dual-write is disabled, stop emitting the legacy projection.
    if settings.graph_v2_read_v2 and not settings.graph_v2_dual_write:
        return False
    return True


def _ont_class_for_v2_label(label: str, *, engagement_type: str | None = None) -> str:
    return lpg_node_ontology_class(label, engagement_type=engagement_type) or ""


def _ont_predicate_for_rel(rel_type: str) -> str:
    return lpg_relationship_ontology_predicate(rel_type) or ""


def _ont_label_ident_for_v2_label(label: str, *, engagement_type: str | None = None) -> str:
    return lpg_node_neo4j_label_identifier(label, engagement_type=engagement_type) or ""


def _ont_rel_ident_for_rel(rel_type: str) -> str:
    return lpg_relationship_neo4j_type_identifier(rel_type) or ""


_HS_CONTACT_LABEL = _ont_label_ident_for_v2_label("CRMContact")
_HS_COMPANY_LABEL = _ont_label_ident_for_v2_label("CRMCompany")
_HS_DEAL_LABEL = _ont_label_ident_for_v2_label("CRMOpportunity")
_HS_ENGAGEMENT_BASE_LABEL = _ont_label_ident_for_v2_label("CRMEngagement")
_HS_INTERNAL_USER_LABEL = ontology_term_to_neo4j_identifier("hs:InternalUser") or ""
_HS_PERSON_LABEL = ontology_term_to_neo4j_identifier("hs:Person") or ""
_HS_SCORE_SNAPSHOT_LABEL = _ont_label_ident_for_v2_label("ScoreSnapshot")
_HS_ASSERTION_LABEL = _ont_label_ident_for_v2_label("KGAssertion")
_HS_EXTRACTION_EVENT_LABEL = _ont_label_ident_for_v2_label("ExtractionEvent")
_HS_SOURCE_ARTIFACT_LABEL = _ont_label_ident_for_v2_label("EvidenceChunk")
_HS_CASE_CONTACT_LABEL = _ont_label_ident_for_v2_label("CaseContact")
_HS_CASE_OPPORTUNITY_LABEL = _ont_label_ident_for_v2_label("CaseOpportunity")

_HS_WORKS_AT_REL = _ont_rel_ident_for_rel("WORKS_AT")
_HS_ENGAGED_WITH_REL = _ont_rel_ident_for_rel("ENGAGED_WITH")
_HS_INVOLVES_CONTACT_REL = _ont_rel_ident_for_rel("INVOLVES_CONTACT")
_HS_OPPORTUNITY_FOR_COMPANY_REL = _ont_rel_ident_for_rel("OPPORTUNITY_FOR_COMPANY")
_HS_ASSERTION_OBJECT_REL = _ont_rel_ident_for_rel("ASSERTS_ABOUT_CONTACT") or _ont_rel_ident_for_rel("ASSERTS_ABOUT_COMPANY")
_HS_SOURCE_ARTIFACT_REL = _ont_rel_ident_for_rel("SUPPORTED_BY")
_HS_EXTRACTION_EVENT_REL = _ont_rel_ident_for_rel("FROM_EXTRACTION_EVENT")
_HS_HAS_CASE_CONTACT_REL = _ont_rel_ident_for_rel("HAS_CASE_CONTACT")
_HS_HAS_CASE_OPPORTUNITY_REL = _ont_rel_ident_for_rel("HAS_CASE_OPPORTUNITY")
_HS_TARGETS_CONTACT_REL = _ont_rel_ident_for_rel("TARGETS_CONTACT")
_HS_PROMOTED_TO_REL = _ont_rel_ident_for_rel("PROMOTED_TO")
_HS_HAS_SCORE_REL = _ont_rel_ident_for_rel("HAS_SCORE")
_HS_AUTHORED_BY_REL = ontology_term_to_neo4j_identifier("hs:authoredBy") or ""
_HS_SENT_TO_REL = ontology_term_to_neo4j_identifier("hs:sentTo") or ""
_HS_CC_TO_REL = ontology_term_to_neo4j_identifier("hs:ccTo") or ""

# HAS_ASSERTION is a V2 convenience edge (Engagement -> Assertion); the ontology-native
# relationship is stored in the opposite direction (Assertion -> Engagement) as hs:derivedFromEngagement.
_HS_DERIVED_FROM_ENGAGEMENT_REL = _ont_rel_ident_for_rel("HAS_ASSERTION")


def _engagement_ontology_label_expr(engagement_type: str | None) -> str:
    labels = [_HS_ENGAGEMENT_BASE_LABEL]
    subtype = _ont_label_ident_for_v2_label("CRMEngagement", engagement_type=engagement_type)
    if subtype and subtype not in labels:
        labels.append(subtype)
    return ":".join(label for label in labels if label)


def _adopt_ontology_node_into_v2_label(
    session,
    *,
    v2_label: str,
    ontology_label_identifier: str,
    key_property: str,
    key_value: str | None,
) -> None:
    if session is None:
        return
    if not _v2_physical_projection_writes_enabled():
        return
    if not v2_label or not ontology_label_identifier or not key_property:
        return
    if not isinstance(key_value, str) or not key_value.strip():
        return
    _session_run(session, 
        f"""
        MATCH (n:{ontology_label_identifier} {{{key_property}: $key_value}})
        SET n:{v2_label}
        """,
        key_value=key_value,
    )


_V2_READ_LABEL_REPLACEMENTS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [
            (old, new)
            for old, new in (
        (":CRMContact", f":{_HS_CONTACT_LABEL}" if _HS_CONTACT_LABEL else ""),
        (":CRMCompany", f":{_HS_COMPANY_LABEL}" if _HS_COMPANY_LABEL else ""),
        (":CRMOpportunity", f":{_HS_DEAL_LABEL}" if _HS_DEAL_LABEL else ""),
        (":CRMEngagement", f":{_HS_ENGAGEMENT_BASE_LABEL}" if _HS_ENGAGEMENT_BASE_LABEL else ""),
        (":ScoreSnapshot", f":{_HS_SCORE_SNAPSHOT_LABEL}" if _HS_SCORE_SNAPSHOT_LABEL else ""),
        (":KGAssertion", f":{_HS_ASSERTION_LABEL}" if _HS_ASSERTION_LABEL else ""),
        (":EvidenceChunk", f":{_HS_SOURCE_ARTIFACT_LABEL}" if _HS_SOURCE_ARTIFACT_LABEL else ""),
        (":ExtractionEvent", f":{_HS_EXTRACTION_EVENT_LABEL}" if _HS_EXTRACTION_EVENT_LABEL else ""),
        (":CaseContact", f":{_HS_CASE_CONTACT_LABEL}" if _HS_CASE_CONTACT_LABEL else ""),
        (":CaseOpportunity", f":{_HS_CASE_OPPORTUNITY_LABEL}" if _HS_CASE_OPPORTUNITY_LABEL else ""),
            )
            if new
        ],
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

_V2_READ_REL_REPLACEMENTS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [
            (old, new)
            for old, new in (
        (":WORKS_AT", f":{_HS_WORKS_AT_REL}" if _HS_WORKS_AT_REL else ""),
        (":ENGAGED_WITH", f":{_HS_ENGAGED_WITH_REL}" if _HS_ENGAGED_WITH_REL else ""),
        (":INVOLVES_CONTACT", f":{_HS_INVOLVES_CONTACT_REL}" if _HS_INVOLVES_CONTACT_REL else ""),
        (":OPPORTUNITY_FOR_COMPANY", f":{_HS_OPPORTUNITY_FOR_COMPANY_REL}" if _HS_OPPORTUNITY_FOR_COMPANY_REL else ""),
        (":OPPORTUNITY_FOR_COMPANY_DERIVED", f":{_HS_OPPORTUNITY_FOR_COMPANY_REL}" if _HS_OPPORTUNITY_FOR_COMPANY_REL else ""),
        (":ENGAGED_COMPANY_DERIVED", f":{_ont_rel_ident_for_rel('ENGAGED_COMPANY_DERIVED')}" if _ont_rel_ident_for_rel("ENGAGED_COMPANY_DERIVED") else ""),
        (":ENGAGED_OPPORTUNITY", f":{_ont_rel_ident_for_rel('ENGAGED_OPPORTUNITY')}" if _ont_rel_ident_for_rel("ENGAGED_OPPORTUNITY") else ""),
        (":ENGAGED_OPPORTUNITY_DERIVED", f":{_ont_rel_ident_for_rel('ENGAGED_OPPORTUNITY_DERIVED')}" if _ont_rel_ident_for_rel("ENGAGED_OPPORTUNITY_DERIVED") else ""),
        (":PROMOTED_TO", f":{_HS_PROMOTED_TO_REL}" if _HS_PROMOTED_TO_REL else ""),
        (":HAS_SCORE", f":{_HS_HAS_SCORE_REL}" if _HS_HAS_SCORE_REL else ""),
        (":TARGETS_CONTACT", f":{_HS_TARGETS_CONTACT_REL}" if _HS_TARGETS_CONTACT_REL else ""),
        (":HAS_CASE_CONTACT", f":{_HS_HAS_CASE_CONTACT_REL}" if _HS_HAS_CASE_CONTACT_REL else ""),
        (":HAS_CASE_OPPORTUNITY", f":{_HS_HAS_CASE_OPPORTUNITY_REL}" if _HS_HAS_CASE_OPPORTUNITY_REL else ""),
        # HAS_ASSERTION intentionally excluded: ontology-native relation direction is reversed.
        (":SUPPORTED_BY", f":{_HS_SOURCE_ARTIFACT_REL}" if _HS_SOURCE_ARTIFACT_REL else ""),
        (":FROM_EXTRACTION_EVENT", f":{_HS_EXTRACTION_EVENT_REL}" if _HS_EXTRACTION_EVENT_REL else ""),
        (":ASSERTS_ABOUT_CONTACT", f":{_HS_ASSERTION_OBJECT_REL}" if _HS_ASSERTION_OBJECT_REL else ""),
        (":ASSERTS_ABOUT_COMPANY", f":{_HS_ASSERTION_OBJECT_REL}" if _HS_ASSERTION_OBJECT_REL else ""),
            )
            if new
        ],
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

_V2_READ_REL_BARE_REPLACEMENTS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        [
            (old.lstrip(":"), new.lstrip(":"))
            for old, new in _V2_READ_REL_REPLACEMENTS
            if old.startswith(":") and new.startswith(":")
        ],
        key=lambda item: len(item[0]),
        reverse=True,
    )
)

_HS_BACKTICKED_IDENTIFIER_RE = re.compile(r"`hs:[A-Za-z][A-Za-z0-9_]*`")


def _ontify_v2_read_cypher(query: str) -> str:
    if not _v2_graph_reads_enabled():
        return query
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"__HSID_{len(protected) - 1}__"

    transformed = _HS_BACKTICKED_IDENTIFIER_RE.sub(_protect, query)
    for old, new in _V2_READ_LABEL_REPLACEMENTS:
        transformed = transformed.replace(old, new)
    for old, new in _V2_READ_REL_REPLACEMENTS:
        transformed = transformed.replace(old, new)
    for old, new in _V2_READ_REL_BARE_REPLACEMENTS:
        transformed = transformed.replace(f"|{old}", f"|{new}")
    for idx, original in enumerate(protected):
        transformed = transformed.replace(f"__HSID_{idx}__", original)
    return transformed


def _session_run(session, query: str, *args, **kwargs):
    if isinstance(query, str) and _ontology_native_physical_mode_enabled():
        query = _ontify_v2_read_cypher(query)
    return session.run(query, *args, **kwargs)


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _normalize_key(value: Any) -> str:
    text = _normalize_text(value).lower()
    return re.sub(r"\s+", " ", text).strip()


def _email_domain(value: Any) -> str:
    text = _normalize_text(value).lower()
    if "@" not in text:
        return ""
    return text.rsplit("@", 1)[-1].strip()


def _is_domain_like_label(value: Any) -> bool:
    text = _normalize_text(value).lower()
    if not text or " " in text or "@" in text:
        return False
    return bool(re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", text))


def _prefer_contact_company_for_alias_candidate(session, *, contact_id: str, candidate_company: str) -> str:
    candidate = _normalize_text(candidate_company)
    if not candidate or not contact_id:
        return candidate

    rows = _session_run(session, 
        """
        MATCH (c:CRMContact {external_id: $contact_id})
        OPTIONAL MATCH (c)-[r:WORKS_AT]->(co:CRMCompany)
        RETURN c.primary_email AS primary_email,
               c.company AS contact_company,
               co.name AS edge_company_name,
               coalesce(r.source, "") AS relation_source
        ORDER BY CASE coalesce(r.source, "")
            WHEN "contact_sync" THEN 0
            WHEN "contact_sheet_backfill_hint" THEN 1
            ELSE 9
        END ASC
        LIMIT 5
        """,
        contact_id=contact_id,
    ).data()
    if not rows:
        return candidate

    primary_email = _normalize_text(rows[0].get("primary_email"))
    fallback_contact_company = _normalize_text(rows[0].get("contact_company"))
    preferred_company = ""
    for row in rows:
        edge_company_name = _normalize_text(row.get("edge_company_name"))
        relation_source = _normalize_text(row.get("relation_source"))
        if edge_company_name and relation_source in {"contact_sync", "contact_sheet_backfill_hint"}:
            preferred_company = edge_company_name
            break
    if not preferred_company:
        preferred_company = fallback_contact_company
    if not preferred_company:
        return candidate

    if _normalize_key(candidate) == _normalize_key(preferred_company):
        return preferred_company

    email_domain = _email_domain(primary_email)
    if email_domain and _is_domain_like_label(candidate) and candidate.lower() == email_domain:
        return preferred_company

    return candidate


def get_contact_company_links(contact_id: str) -> list[dict[str, str]]:
    normalized_contact_id = _normalize_text(contact_id)
    if not normalized_contact_id:
        return []
    with neo4j_session() as session:
        if session is None:
            return []
        rows = _session_run(session, 
            """
            MATCH (c:CRMContact {external_id: $contact_id})
            OPTIONAL MATCH (c)-[r:WORKS_AT]->(co:CRMCompany)
            RETURN co.name AS company_name,
                   co.external_id AS company_external_id,
                   coalesce(r.source, "") AS source
            """,
            contact_id=normalized_contact_id,
        ).data()
    results: list[dict[str, str]] = []
    for row in rows:
        company_name = _normalize_text(row.get("company_name"))
        company_external_id = _normalize_text(row.get("company_external_id"))
        source = _normalize_text(row.get("source"))
        if not company_name:
            continue
        results.append(
            {
                "company_name": company_name,
                "company_external_id": company_external_id,
                "source": source,
            }
        )
    return results


def _normalize_predicate(value: Any) -> str:
    text = _normalize_key(value)
    if not text:
        return "related_to"
    normalized = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return normalized or "related_to"


def _contact_entity_id(contact_id: str) -> str:
    return f"contact:{contact_id}"


def _stable_entity_id(name: str, kind: str) -> str:
    payload = f"entity:{kind.lower()}:{_normalize_key(name)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def _relation_id(
    *,
    contact_id: str,
    interaction_id: str,
    claim_id: str | None,
    subject_name: str,
    predicate_norm: str,
    object_name: str,
) -> str:
    if isinstance(claim_id, str) and claim_id.strip():
        return f"claim:{claim_id.strip()}"
    payload = f"{contact_id}:{interaction_id}:{_normalize_key(subject_name)}:{predicate_norm}:{_normalize_key(object_name)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, payload))


def _extract_keywords(text: str | None, max_keywords: int = 8) -> list[str]:
    if not isinstance(text, str):
        return []
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{2,}", text.lower())
    keywords: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen or token in _STOPWORDS:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= max_keywords:
            break
    return keywords


def _is_contact_alias(value: str, *, contact_email: str | None = None, contact_display_name: str | None = None) -> bool:
    normalized = _normalize_key(value)
    if not normalized:
        return False
    aliases = set(_CONTACT_RELATION_ALIASES)
    if contact_email:
        aliases.add(_normalize_key(contact_email))
    if contact_display_name:
        aliases.add(_normalize_key(contact_display_name))
    return normalized in aliases


def _build_path_text(node_names: list[str], predicates: list[str]) -> str:
    if not node_names:
        return ""
    if len(node_names) == 1 or not predicates:
        return node_names[0]
    parts = [node_names[0]]
    for idx, predicate in enumerate(predicates):
        if idx + 1 >= len(node_names):
            break
        parts.append(f"-[{predicate}]->")
        parts.append(node_names[idx + 1])
    return " ".join(part for part in parts if part)


def merge_contact(contact: dict[str, Any]) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        settings = get_settings()
        if _legacy_graph_writes_enabled():
            _session_run(session, 
                """
                MERGE (c:Contact {contact_id: $contact_id})
                SET c.primary_email = $primary_email,
                    c.display_name = $display_name,
                    c.first_name = $first_name,
                    c.last_name = $last_name,
                    c.company = $company,
                    c.owner_user_id = $owner_user_id
                """,
                **contact,
            )
        if settings.graph_v2_enabled:
            _adopt_ontology_node_into_v2_label(
                session,
                v2_label="CRMContact",
                ontology_label_identifier=_HS_CONTACT_LABEL,
                key_property="external_id",
                key_value=contact.get("contact_id"),
            )
            _session_run(session, 
                """
                MERGE (c:CRMContact {external_id: $contact_id})
                SET c.contact_id = $contact_id,
                    c.primary_email = $primary_email,
                    c.display_name = $display_name,
                    c.first_name = $first_name,
                    c.last_name = $last_name,
                    c.company = $company,
                    c.owner_user_id = $owner_user_id,
                    c.ont_class = $contact_ont_class,
                    c.entity_status = "canonical",
                    c.updated_at = datetime($updated_at)
                """,
                updated_at=datetime.now(timezone.utc).isoformat(),
                contact_ont_class=_ont_class_for_v2_label("CRMContact"),
                **contact,
            )
            if _HS_CONTACT_LABEL:
                _session_run(session, 
                    f"""
                    MATCH (c:CRMContact {{external_id: $contact_id}})
                    SET c:{_HS_CONTACT_LABEL}
                    """,
                    contact_id=contact["contact_id"],
                )
            company_name = _normalize_text(contact.get("company"))
            if company_name:
                company_external_id = f"company:auto:{_normalize_key(company_name)}"
                _adopt_ontology_node_into_v2_label(
                    session,
                    v2_label="CRMCompany",
                    ontology_label_identifier=_HS_COMPANY_LABEL,
                    key_property="external_id",
                    key_value=company_external_id,
                )
                _session_run(session, 
                    """
                    MATCH (c:CRMContact {external_id: $contact_id})
                    OPTIONAL MATCH (c)-[old:WORKS_AT]->(oldCo:CRMCompany)
                    WHERE coalesce(old.source, "") IN ["contact_sync"]
                      AND oldCo.external_id <> $company_external_id
                    DELETE old
                    WITH c
                    MERGE (co:CRMCompany {external_id: $company_external_id})
                    ON CREATE SET co.name = $company_name,
                                  co.ont_class = $company_ont_class,
                                  co.entity_status = "provisional",
                                  co.created_at = datetime($created_at)
                    SET co.updated_at = datetime($updated_at),
                        co.ont_class = $company_ont_class
                    MERGE (c)-[r:WORKS_AT]->(co)
                    SET r.source = "contact_sync",
                        r.ont_predicate = $works_at_ont_predicate,
                        r.updated_at = datetime($updated_at)
                    """,
                    contact_id=contact["contact_id"],
                    company_external_id=company_external_id,
                    company_name=company_name,
                    company_ont_class=_ont_class_for_v2_label("CRMCompany"),
                    works_at_ont_predicate=_ont_predicate_for_rel("WORKS_AT"),
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
                if _HS_COMPANY_LABEL and _HS_WORKS_AT_REL and _HS_CONTACT_LABEL:
                    _session_run(session, 
                        f"""
                        MATCH (c:CRMContact {{external_id: $contact_id}})-[w:WORKS_AT]->(co:CRMCompany {{external_id: $company_external_id}})
                        SET c:{_HS_CONTACT_LABEL}
                        SET co:{_HS_COMPANY_LABEL}
                        MERGE (c)-[r:{_HS_WORKS_AT_REL}]->(co)
                        SET r.source = coalesce(w.source, "contact_sync"),
                            r.ont_predicate = $works_at_ont_predicate,
                            r.updated_at = datetime($updated_at)
                        """,
                        contact_id=contact["contact_id"],
                        company_external_id=company_external_id,
                        works_at_ont_predicate=_ont_predicate_for_rel("WORKS_AT"),
                        updated_at=datetime.now(timezone.utc).isoformat(),
                    )


def merge_interaction(interaction: dict[str, Any]) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        settings = get_settings()
        if _legacy_graph_writes_enabled():
            _session_run(session, 
                """
                MERGE (i:Interaction {interaction_id: $interaction_id})
                SET i.type = $type,
                    i.timestamp = datetime($timestamp),
                    i.source_system = $source_system,
                    i.direction = $direction,
                    i.thread_id = coalesce($thread_id, i.thread_id),
                    i.subject = coalesce($subject, i.subject)
                """,
                **interaction,
            )
        if settings.graph_v2_enabled:
            engagement_label_expr = _engagement_ontology_label_expr(interaction.get("type"))
            _adopt_ontology_node_into_v2_label(
                session,
                v2_label="CRMEngagement",
                ontology_label_identifier=_HS_ENGAGEMENT_BASE_LABEL,
                key_property="external_id",
                key_value=interaction.get("interaction_id"),
            )
            _session_run(session, 
                """
                MERGE (e:CRMEngagement {external_id: $interaction_id})
                SET e.interaction_id = $interaction_id,
                    e.engagement_type = $type,
                    e.occurred_at = datetime($timestamp),
                    e.source_system = $source_system,
                    e.direction = $direction,
                    e.thread_id = coalesce($thread_id, e.thread_id),
                    e.subject = coalesce($subject, e.subject),
                    e.ont_class = $engagement_ont_class,
                    e.ont_class_base = $engagement_ont_class_base,
                    e.entity_status = "canonical",
                    e.updated_at = datetime($updated_at)
                """,
                updated_at=datetime.now(timezone.utc).isoformat(),
                engagement_ont_class=_ont_class_for_v2_label("CRMEngagement", engagement_type=interaction.get("type")),
                engagement_ont_class_base=_ont_class_for_v2_label("CRMEngagement"),
                **interaction,
            )
            if engagement_label_expr:
                _session_run(session, 
                    """
                    MATCH (e:CRMEngagement {external_id: $interaction_id})
                    SET e:__HS_ENGAGEMENT_LABELS__
                    """.replace("__HS_ENGAGEMENT_LABELS__", engagement_label_expr),
                    interaction_id=interaction["interaction_id"],
                )


def merge_internal_user(internal_user: dict[str, Any]) -> None:
    internal_user_id = _normalize_text(internal_user.get("internal_user_id"))
    primary_email = _normalize_text(internal_user.get("primary_email")).lower()
    display_name = _normalize_text(internal_user.get("display_name")) or None
    if not primary_email or not _HS_INTERNAL_USER_LABEL:
        return
    external_id = internal_user_id or f"internal:{primary_email}"
    with neo4j_session() as session:
        if session is None:
            return
        person_label_expr = f":{_HS_PERSON_LABEL}" if _HS_PERSON_LABEL else ""
        _session_run(
            session,
            f"""
            MERGE (u:{_HS_INTERNAL_USER_LABEL} {{external_id: $external_id}})
            SET u.internal_user_id = coalesce($internal_user_id, u.internal_user_id),
                u.primary_email = $primary_email,
                u.display_name = coalesce($display_name, u.display_name),
                u.entity_status = "canonical",
                u.is_internal = true,
                u.ont_class = "hs:InternalUser",
                u.updated_at = datetime($updated_at)
            SET u{person_label_expr}
            """,
            external_id=external_id,
            internal_user_id=internal_user_id or None,
            primary_email=primary_email,
            display_name=display_name,
            updated_at=_now_iso(),
        )


def attach_internal_user_interaction_role(internal_user_email: str, interaction_id: str, role: str) -> None:
    email = _normalize_text(internal_user_email).lower()
    if not email or not interaction_id or not _HS_INTERNAL_USER_LABEL or not _HS_ENGAGEMENT_BASE_LABEL:
        return
    rel_ident = ""
    normalized_role = _normalize_text(role).lower()
    if normalized_role == "from":
        rel_ident = _HS_AUTHORED_BY_REL or _HS_ENGAGED_WITH_REL
    elif normalized_role == "to":
        rel_ident = _HS_SENT_TO_REL or _HS_ENGAGED_WITH_REL
    elif normalized_role == "cc":
        rel_ident = _HS_CC_TO_REL or _HS_ENGAGED_WITH_REL
    else:
        rel_ident = _HS_ENGAGED_WITH_REL
    if not rel_ident:
        return
    external_id = f"internal:{email}"
    with neo4j_session() as session:
        if session is None:
            return
        _session_run(
            session,
            f"""
            MATCH (e:{_HS_ENGAGEMENT_BASE_LABEL} {{external_id: $interaction_id}})
            MERGE (u:{_HS_INTERNAL_USER_LABEL} {{external_id: $external_id}})
            ON CREATE SET u.primary_email = $email,
                          u.entity_status = "canonical",
                          u.is_internal = true,
                          u.ont_class = "hs:InternalUser",
                          u.created_at = datetime($created_at)
            SET u.updated_at = datetime($updated_at)
            {f"SET u:{_HS_PERSON_LABEL}" if _HS_PERSON_LABEL else ""}
            MERGE (e)-[r:{rel_ident}]->(u)
            """,
            interaction_id=interaction_id,
            external_id=external_id,
            email=email,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )


def attach_contact_interaction(contact_id: str, interaction_id: str) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        settings = get_settings()
        if _legacy_graph_writes_enabled():
            _session_run(session, 
                """
                MATCH (c:Contact {contact_id: $contact_id})
                MATCH (i:Interaction {interaction_id: $interaction_id})
                MERGE (c)-[:PARTICIPATED_IN]->(i)
                """,
                contact_id=contact_id,
                interaction_id=interaction_id,
            )
        if settings.graph_v2_enabled:
            is_provisional = contact_id.startswith("contact:provisional:")
            _adopt_ontology_node_into_v2_label(
                session,
                v2_label="CRMContact",
                ontology_label_identifier=_HS_CONTACT_LABEL,
                key_property="external_id",
                key_value=contact_id,
            )
            _session_run(session, 
                """
                MERGE (c:CRMContact {external_id: $contact_id})
                ON CREATE SET c.entity_status = CASE WHEN $is_provisional THEN "provisional" ELSE "canonical" END,
                              c.created_at = datetime($created_at),
                              c.ont_class = $contact_ont_class
                SET c.updated_at = datetime($updated_at),
                    c.ont_class = coalesce(c.ont_class, $contact_ont_class)
                WITH c
                MATCH (e:CRMEngagement {external_id: $interaction_id})
                MERGE (e)-[r:ENGAGED_WITH]->(c)
                SET r.ont_predicate = $engaged_with_ont_predicate
                """,
                contact_id=contact_id,
                interaction_id=interaction_id,
                is_provisional=is_provisional,
                created_at=_now_iso(),
                updated_at=_now_iso(),
                contact_ont_class=_ont_class_for_v2_label("CRMContact"),
                engaged_with_ont_predicate=_ont_predicate_for_rel("ENGAGED_WITH"),
            )
            if _HS_CONTACT_LABEL and _HS_ENGAGED_WITH_REL and _HS_ENGAGEMENT_BASE_LABEL:
                _session_run(session, 
                    f"""
                    MATCH (e:CRMEngagement {{external_id: $interaction_id}})
                    MATCH (c:CRMContact {{external_id: $contact_id}})
                    SET c:{_HS_CONTACT_LABEL}
                    SET e:{_HS_ENGAGEMENT_BASE_LABEL}
                    MERGE (e)-[r:{_HS_ENGAGED_WITH_REL}]->(c)
                    SET r.ont_predicate = $engaged_with_ont_predicate
                    """,
                    interaction_id=interaction_id,
                    contact_id=contact_id,
                    engaged_with_ont_predicate=_ont_predicate_for_rel("ENGAGED_WITH"),
                )


def upsert_contact_as_entity(contact_id: str) -> dict[str, str]:
    with neo4j_session() as session:
        if session is None:
            return {
                "entity_id": _contact_entity_id(contact_id),
                "display_name": contact_id,
                "primary_email": "",
            }
        rows = _session_run(session, 
            """
            MERGE (c:Contact {contact_id: $contact_id})
            SET c.display_name = coalesce(c.display_name, $fallback_display_name)
            MERGE (e:Entity {entity_id: $entity_id})
            SET e.name = coalesce(c.display_name, c.primary_email, c.contact_id),
                e.normalized_name = toLower(coalesce(c.display_name, c.primary_email, c.contact_id)),
                e.kind = "Contact",
                e.contact_id = c.contact_id,
                e.updated_at = datetime($updated_at)
            MERGE (c)-[:AS_ENTITY]->(e)
            RETURN coalesce(c.display_name, c.contact_id) AS display_name,
                   coalesce(c.primary_email, "") AS primary_email
            """,
            contact_id=contact_id,
            entity_id=_contact_entity_id(contact_id),
            fallback_display_name=contact_id,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ).data()
    row = rows[0] if rows else {}
    return {
        "entity_id": _contact_entity_id(contact_id),
        "display_name": _normalize_text(row.get("display_name")) or contact_id,
        "primary_email": _normalize_text(row.get("primary_email")),
    }


def upsert_relation_triple(
    *,
    contact_id: str,
    interaction_id: str,
    interaction_timestamp_iso: str | None,
    subject_name: str,
    predicate: str,
    object_name: str,
    claim_id: str | None,
    confidence: float,
    status: str,
    source_system: str,
    uncertain: bool,
    evidence_refs: list[dict[str, Any]] | None = None,
    subject_kind: str | None = None,
    object_kind: str | None = None,
) -> dict[str, Any]:
    if not _legacy_graph_writes_enabled():
        return {"upserted": False}
    contact_entity = upsert_contact_as_entity(contact_id)
    display_name = contact_entity.get("display_name") or contact_id
    primary_email = contact_entity.get("primary_email") or ""

    subject_clean = _normalize_text(subject_name) or "contact"
    object_clean = _normalize_text(object_name)
    if not object_clean:
        return {"upserted": False}

    predicate_clean = _normalize_text(predicate) or "related_to"
    predicate_norm = _normalize_predicate(predicate_clean)

    subject_is_contact = _is_contact_alias(
        subject_clean,
        contact_email=primary_email,
        contact_display_name=display_name,
    )
    object_is_contact = _is_contact_alias(
        object_clean,
        contact_email=primary_email,
        contact_display_name=display_name,
    )

    if subject_is_contact:
        subject_entity_id = _contact_entity_id(contact_id)
        resolved_subject_name = display_name
        resolved_subject_kind = "Contact"
    else:
        resolved_subject_name = subject_clean
        resolved_subject_kind = _normalize_text(subject_kind) or "Entity"
        subject_entity_id = _stable_entity_id(resolved_subject_name, resolved_subject_kind)

    if object_is_contact:
        object_entity_id = _contact_entity_id(contact_id)
        resolved_object_name = display_name
        resolved_object_kind = "Contact"
    else:
        resolved_object_name = object_clean
        resolved_object_kind = _normalize_text(object_kind) or (
            "Company" if predicate_norm in {"works_at", "employment_change", "employed_by"} else "Entity"
        )
        object_entity_id = _stable_entity_id(resolved_object_name, resolved_object_kind)

    relation_id = _relation_id(
        contact_id=contact_id,
        interaction_id=interaction_id,
        claim_id=claim_id,
        subject_name=resolved_subject_name,
        predicate_norm=predicate_norm,
        object_name=resolved_object_name,
    )
    seen_at = interaction_timestamp_iso or datetime.now(timezone.utc).isoformat()
    evidence_json = json.dumps(evidence_refs or [], ensure_ascii=True, separators=(",", ":"))

    with neo4j_session() as session:
        if session is None:
            return {"upserted": False}

        if not subject_is_contact:
            _session_run(session, 
                """
                MERGE (s:Entity {entity_id: $entity_id})
                SET s.name = $name,
                    s.normalized_name = $normalized_name,
                    s.kind = $kind,
                    s.updated_at = datetime($updated_at)
                """,
                entity_id=subject_entity_id,
                name=resolved_subject_name,
                normalized_name=_normalize_key(resolved_subject_name),
                kind=resolved_subject_kind,
                updated_at=seen_at,
            )

        if not object_is_contact:
            _session_run(session, 
                """
                MERGE (o:Entity {entity_id: $entity_id})
                SET o.name = $name,
                    o.normalized_name = $normalized_name,
                    o.kind = $kind,
                    o.updated_at = datetime($updated_at)
                """,
                entity_id=object_entity_id,
                name=resolved_object_name,
                normalized_name=_normalize_key(resolved_object_name),
                kind=resolved_object_kind,
                updated_at=seen_at,
            )

        _session_run(session, 
            """
            MATCH (sub:Entity {entity_id: $subject_entity_id})
            MATCH (obj:Entity {entity_id: $object_entity_id})
            MERGE (sub)-[r:RELATES_TO {relation_id: $relation_id}]->(obj)
            SET r.contact_id = $contact_id,
                r.interaction_id = $interaction_id,
                r.claim_id = $claim_id,
                r.predicate = $predicate,
                r.predicate_norm = $predicate_norm,
                r.subject_name = $subject_name,
                r.object_name = $object_name,
                r.confidence = $confidence,
                r.status = $status,
                r.uncertain = $uncertain,
                r.source_system = $source_system,
                r.evidence_json = $evidence_json,
                r.first_seen_at = CASE
                    WHEN r.first_seen_at IS NULL THEN datetime($seen_at)
                    ELSE r.first_seen_at
                END,
                r.last_seen_at = datetime($seen_at)
            """,
            subject_entity_id=subject_entity_id,
            object_entity_id=object_entity_id,
            relation_id=relation_id,
            contact_id=contact_id,
            interaction_id=interaction_id,
            claim_id=claim_id,
            predicate=predicate_clean,
            predicate_norm=predicate_norm,
            subject_name=resolved_subject_name,
            object_name=resolved_object_name,
            confidence=float(confidence),
            status=_normalize_text(status) or "proposed",
            uncertain=bool(uncertain),
            source_system=_normalize_text(source_system) or "unknown",
            evidence_json=evidence_json,
            seen_at=seen_at,
        )

        conflict_rows = _session_run(session, 
            """
            MATCH (sub:Entity {entity_id: $subject_entity_id})-[r:RELATES_TO]->(other:Entity)
            WHERE r.contact_id = $contact_id
              AND r.predicate_norm = $predicate_norm
              AND coalesce(r.status, "proposed") = "accepted"
              AND other.entity_id <> $object_entity_id
              AND r.relation_id <> $relation_id
            RETURN r.relation_id AS relation_id,
                   r.claim_id AS claim_id,
                   other.name AS object_name,
                   coalesce(r.confidence, 0.0) AS confidence
            ORDER BY confidence DESC
            LIMIT 1
            """,
            subject_entity_id=subject_entity_id,
            contact_id=contact_id,
            predicate_norm=predicate_norm,
            object_entity_id=object_entity_id,
            relation_id=relation_id,
        ).data()

    conflict = None
    if conflict_rows:
        row = conflict_rows[0]
        conflict = {
            "relation_id": row.get("relation_id"),
            "claim_id": row.get("claim_id"),
            "object_name": row.get("object_name"),
            "confidence": _as_float(row.get("confidence"), 0.0),
        }

    return {
        "upserted": True,
        "relation_id": relation_id,
        "subject_entity_id": subject_entity_id,
        "object_entity_id": object_entity_id,
        "subject_name": resolved_subject_name,
        "object_name": resolved_object_name,
        "predicate": predicate_clean,
        "predicate_norm": predicate_norm,
        "conflict": conflict,
    }


def upsert_contact_company_relation(
    *,
    contact_id: str,
    company_name: str,
    source_system: str = "contacts_registry",
    confidence: float = 0.98,
) -> dict[str, Any]:
    company = _normalize_text(company_name)
    if not company:
        return {"upserted": False}

    claim_id = f"company-hint:{contact_id}:{_normalize_key(company)}"
    result = upsert_relation_triple(
        contact_id=contact_id,
        interaction_id=f"{source_system}:{contact_id}:company_hint",
        interaction_timestamp_iso=datetime.now(timezone.utc).isoformat(),
        subject_name="contact",
        predicate="works_at",
        object_name=company,
        claim_id=claim_id,
        confidence=confidence,
        status="accepted",
        source_system=source_system,
        uncertain=False,
        evidence_refs=[{"source": "contact_cache.company", "value": company}],
        subject_kind="Contact",
        object_kind="Company",
    )
    if not result.get("upserted"):
        return result

    # Contact registry sync is authoritative for current employer in the legacy graph as well.
    if _normalize_text(source_system) == "contacts_registry":
        with neo4j_session() as session:
            if session is not None:
                _session_run(session, 
                    """
                    MATCH (:Contact {contact_id: $contact_id})-[:AS_ENTITY]->(sub:Entity)
                    MATCH (sub)-[r:RELATES_TO {predicate_norm: "works_at"}]->(:Entity)
                    WHERE r.contact_id = $contact_id
                      AND coalesce(r.source_system, "") = "contacts_registry"
                      AND coalesce(r.status, "proposed") = "accepted"
                      AND r.relation_id <> $relation_id
                    SET r.status = "superseded",
                        r.superseded_by_relation_id = $relation_id,
                        r.superseded_at = datetime($updated_at)
                    """,
                    contact_id=contact_id,
                    relation_id=result.get("relation_id"),
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
    return result


def create_claim_with_evidence(
    contact_id: str,
    interaction_id: str,
    claim: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
) -> None:
    if not _legacy_graph_writes_enabled():
        return
    if not evidence_refs:
        raise ValueError("Claim write rejected: at least one evidence reference is required.")
    for ref in evidence_refs:
        if not isinstance(ref, dict) or not ref.get("chunk_id") or ref.get("span_json") is None:
            raise ValueError("Claim write rejected: invalid evidence reference.")
    claim_payload = dict(claim)
    claim_payload["value_json"] = _json_text(claim_payload.get("value_json"))
    now_iso = datetime.utcnow().isoformat()
    with neo4j_session() as session:
        if session is None:
            return
        _session_run(session, 
            """
            MATCH (c:Contact {contact_id: $contact_id})
            MATCH (i:Interaction {interaction_id: $interaction_id})
            MERGE (cl:Claim {claim_id: $claim_id})
            SET cl.claim_type = $claim_type,
                cl.value_json = $value_json,
                cl.status = $status,
                cl.sensitive = $sensitive,
                cl.valid_from = $valid_from,
                cl.valid_to = $valid_to,
                cl.confidence = $confidence,
                cl.created_at = datetime($created_at),
                cl.source_system = $source_system
            MERGE (i)-[:HAS_CLAIM]->(cl)
            MERGE (c)-[:HAS_CLAIM]->(cl)
            """,
            contact_id=contact_id,
            interaction_id=interaction_id,
            created_at=now_iso,
            **claim_payload,
        )
        for ref in evidence_refs:
            _session_run(session, 
                """
                MATCH (cl:Claim {claim_id: $claim_id})
                MERGE (e:Evidence {evidence_id: $evidence_id})
                SET e.interaction_id = $interaction_id,
                    e.chunk_id = $chunk_id,
                    e.span_json = $span_json,
                    e.quote_hash = $quote_hash
                MERGE (cl)-[:SUPPORTED_BY]->(e)
                """,
                claim_id=claim["claim_id"],
                evidence_id=ref["evidence_id"],
                interaction_id=ref["interaction_id"],
                chunk_id=ref["chunk_id"],
                span_json=_json_text(ref.get("span_json", {})),
                quote_hash=ref.get("quote_hash", ""),
            )


def upsert_score_snapshot(contact_id: str, asof: str, relationship_score: float, priority_score: float, components_json: dict[str, Any]) -> None:
    components_json_text = json.dumps(components_json or {}, ensure_ascii=True, separators=(",", ":"))
    with neo4j_session() as session:
        if session is None:
            return
        settings = get_settings()
        if settings.graph_v2_enabled and settings.graph_v2_read_v2:
            _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                MERGE (c:CRMContact {external_id: $contact_id})
                MERGE (s:ScoreSnapshot {contact_id: $contact_id, asof: $asof})
                SET s.relationship_score = $relationship_score,
                    s.priority_score = $priority_score,
                    s.components_json = $components_json
                MERGE (c)-[:HAS_SCORE]->(s)
                """
                ),
                contact_id=contact_id,
                asof=asof,
                relationship_score=relationship_score,
                priority_score=priority_score,
                components_json=components_json_text,
            )
            if not settings.graph_v2_dual_write:
                return
        _session_run(session, 
            """
            MATCH (c:Contact {contact_id: $contact_id})
            MERGE (s:ScoreSnapshot {contact_id: $contact_id, asof: $asof})
            SET s.relationship_score = $relationship_score,
                s.priority_score = $priority_score,
                s.components_json = $components_json
            MERGE (c)-[:HAS_SCORE]->(s)
            """,
            contact_id=contact_id,
            asof=asof,
            relationship_score=relationship_score,
            priority_score=priority_score,
            components_json=components_json_text,
        )


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_components_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _as_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def get_latest_score_snapshots(contact_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not contact_ids:
        return {}

    with neo4j_session() as session:
        if session is None:
            return {}
        if _v2_graph_reads_enabled():
            rows = _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                UNWIND $contact_ids AS cid
                OPTIONAL MATCH (c:CRMContact {external_id: cid})-[:HAS_SCORE]->(s:ScoreSnapshot)
                WITH cid AS contact_id, s
                ORDER BY contact_id, s.asof DESC
                WITH contact_id, collect(s)[0] AS latest
                RETURN contact_id,
                       latest.asof AS asof,
                       latest.relationship_score AS relationship_score,
                       latest.priority_score AS priority_score,
                       latest.components_json AS components_json
                """
                ),
                contact_ids=contact_ids,
            ).data()
        else:
            rows = _session_run(session, 
                """
                UNWIND $contact_ids AS cid
                OPTIONAL MATCH (c:Contact {contact_id: cid})-[:HAS_SCORE]->(s:ScoreSnapshot)
                WITH cid AS contact_id, s
                ORDER BY contact_id, s.asof DESC
                WITH contact_id, collect(s)[0] AS latest
                RETURN contact_id,
                       latest.asof AS asof,
                       latest.relationship_score AS relationship_score,
                       latest.priority_score AS priority_score,
                       latest.components_json AS components_json
                """,
                contact_ids=contact_ids,
            ).data()

    results: dict[str, dict[str, Any]] = {}
    for row in rows:
        contact_id = row.get("contact_id")
        asof = row.get("asof")
        if not isinstance(contact_id, str) or not isinstance(asof, str):
            continue
        results[contact_id] = {
            "asof": asof,
            "relationship_score": _as_float(row.get("relationship_score")),
            "priority_score": _as_float(row.get("priority_score")),
            "components_json": _as_components_json(row.get("components_json")),
        }
    return results


def get_contact_score_snapshots(contact_id: str, limit: int = 30) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return []
        if _v2_graph_reads_enabled():
            rows = _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                MATCH (c:CRMContact {external_id: $contact_id})-[:HAS_SCORE]->(s:ScoreSnapshot)
                RETURN s.asof AS asof,
                       s.relationship_score AS relationship_score,
                       s.priority_score AS priority_score,
                       s.components_json AS components_json
                ORDER BY s.asof DESC
                LIMIT $limit
                """
                ),
                contact_id=contact_id,
                limit=max(1, limit),
            ).data()
        else:
            rows = _session_run(session, 
                """
                MATCH (c:Contact {contact_id: $contact_id})-[:HAS_SCORE]->(s:ScoreSnapshot)
                RETURN s.asof AS asof,
                       s.relationship_score AS relationship_score,
                       s.priority_score AS priority_score,
                       s.components_json AS components_json
                ORDER BY s.asof DESC
                LIMIT $limit
                """,
                contact_id=contact_id,
                limit=max(1, limit),
            ).data()

    snapshots: list[dict[str, Any]] = []
    for row in rows:
        asof = row.get("asof")
        if not isinstance(asof, str):
            continue
        snapshots.append(
            {
                "asof": asof,
                "relationship_score": _as_float(row.get("relationship_score")),
                "priority_score": _as_float(row.get("priority_score")),
                "components_json": _as_components_json(row.get("components_json")),
            }
        )
    return snapshots


def upsert_next_step_suggestion_v2(
    *,
    scope_type: str,
    scope_id: str,
    summary: str,
    suggestion_type: str,
    source: str,
    confidence: float,
    contact_id: str | None = None,
    opportunity_id: str | None = None,
    case_id: str | None = None,
    due_at: str | None = None,
    freshness_score: float | None = None,
    priority_score: float | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
) -> str:
    normalized_scope_type = _normalize_text(scope_type).lower()
    normalized_scope_id = _normalize_text(scope_id)
    if not normalized_scope_type or not normalized_scope_id:
        return ""
    suggestion_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"next-step:{normalized_scope_type}:{normalized_scope_id}:{_normalize_text(suggestion_type)}",
        )
    )
    with neo4j_session() as session:
        if session is None:
            return suggestion_id
        if not _v2_graph_reads_enabled():
            return suggestion_id
        _session_run(
            session,
            _ontify_v2_read_cypher(
                """
            MERGE (ns:NextStepSuggestion {suggestion_id: $suggestion_id})
            SET ns.scope_type = $scope_type,
                ns.scope_id = $scope_id,
                ns.summary = $summary,
                ns.suggestion_type = $suggestion_type,
                ns.source = $source,
                ns.confidence = $confidence,
                ns.contact_id = $contact_id,
                ns.opportunity_id = $opportunity_id,
                ns.case_id = $case_id,
                ns.due_at = CASE WHEN $due_at IS NULL OR $due_at = "" THEN ns.due_at ELSE $due_at END,
                ns.freshness_score = $freshness_score,
                ns.priority_score = $priority_score,
                ns.evidence_refs_json = $evidence_refs_json,
                ns.updated_at = datetime($updated_at)
            """
            ),
            suggestion_id=suggestion_id,
            scope_type=normalized_scope_type,
            scope_id=normalized_scope_id,
            summary=_normalize_text(summary, max_chars=1200),
            suggestion_type=_normalize_text(suggestion_type, max_chars=80),
            source=_normalize_text(source, max_chars=80),
            confidence=float(confidence or 0.0),
            contact_id=_normalize_text(contact_id) or None,
            opportunity_id=_normalize_text(opportunity_id) or None,
            case_id=_normalize_text(case_id) or None,
            due_at=_normalize_text(due_at) or None,
            freshness_score=float(freshness_score) if freshness_score is not None else None,
            priority_score=float(priority_score) if priority_score is not None else None,
            evidence_refs_json=json.dumps(evidence_refs or [], ensure_ascii=True, separators=(",", ":")),
            updated_at=_now_iso(),
        )
        if contact_id:
            _session_run(
                session,
                _ontify_v2_read_cypher(
                    """
                MATCH (c:CRMContact {external_id: $contact_id})
                MATCH (ns:NextStepSuggestion {suggestion_id: $suggestion_id})
                MERGE (c)-[:HAS_NEXT_STEP]->(ns)
                """
                ),
                contact_id=_normalize_text(contact_id),
                suggestion_id=suggestion_id,
            )
        if opportunity_id:
            _session_run(
                session,
                _ontify_v2_read_cypher(
                    """
                MATCH (opp:CRMOpportunity {external_id: $opportunity_id})
                MATCH (ns:NextStepSuggestion {suggestion_id: $suggestion_id})
                MERGE (opp)-[:HAS_NEXT_STEP]->(ns)
                """
                ),
                opportunity_id=_normalize_text(opportunity_id),
                suggestion_id=suggestion_id,
            )
        if case_id:
            _session_run(
                session,
                _ontify_v2_read_cypher(
                    """
                MATCH (co:CaseOpportunity {case_id: $case_id})
                MATCH (ns:NextStepSuggestion {suggestion_id: $suggestion_id})
                MERGE (co)-[:HAS_NEXT_STEP]->(ns)
                """
                ),
                case_id=_normalize_text(case_id),
                suggestion_id=suggestion_id,
            )
    return suggestion_id


def get_latest_next_step_suggestions_v2(
    *,
    contact_id: str | None = None,
    opportunity_id: str | None = None,
    case_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not _v2_graph_reads_enabled():
        return []
    with neo4j_session() as session:
        if session is None:
            return []
        rows = _session_run(
            session,
            _ontify_v2_read_cypher(
                """
            MATCH (ns:NextStepSuggestion)
            WHERE ($contact_id IS NULL OR coalesce(ns.contact_id, "") = $contact_id)
              AND ($opportunity_id IS NULL OR coalesce(ns.opportunity_id, "") = $opportunity_id)
              AND ($case_id IS NULL OR coalesce(ns.case_id, "") = $case_id)
            RETURN ns.suggestion_id AS suggestion_id,
                   ns.scope_type AS scope_type,
                   ns.scope_id AS scope_id,
                   ns.summary AS summary,
                   ns.suggestion_type AS suggestion_type,
                   ns.source AS source,
                   ns.confidence AS confidence,
                   ns.contact_id AS contact_id,
                   ns.opportunity_id AS opportunity_id,
                   ns.case_id AS case_id,
                   ns.due_at AS due_at,
                   ns.freshness_score AS freshness_score,
                   ns.priority_score AS priority_score,
                   ns.evidence_refs_json AS evidence_refs_json,
                   ns.updated_at AS updated_at
            ORDER BY ns.updated_at DESC
            LIMIT $limit
            """
            ),
            contact_id=_normalize_text(contact_id) or None,
            opportunity_id=_normalize_text(opportunity_id) or None,
            case_id=_normalize_text(case_id) or None,
            limit=max(1, limit),
        ).data()
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "suggestion_id": row.get("suggestion_id"),
                "scope_type": row.get("scope_type"),
                "scope_id": row.get("scope_id"),
                "summary": row.get("summary"),
                "type": row.get("suggestion_type"),
                "source": row.get("source"),
                "confidence": _as_float(row.get("confidence")),
                "contact_id": row.get("contact_id"),
                "opportunity_id": row.get("opportunity_id"),
                "case_id": row.get("case_id"),
                "due_at": row.get("due_at"),
                "freshness_score": _as_float(row.get("freshness_score")),
                "priority_score": _as_float(row.get("priority_score")),
                "evidence_refs": [item for item in _as_json_list(row.get("evidence_refs_json")) if isinstance(item, dict)],
                "updated_at": row.get("updated_at"),
            }
        )
    return results


def get_contact_claims(contact_id: str, status: str | None = None) -> list[dict[str, Any]]:
    if _v2_graph_reads_enabled():
        with neo4j_session() as session:
            if session is None:
                return []
            rows = _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                MATCH (canonical:CRMContact {external_id: $contact_id})
                OPTIONAL MATCH (alias:CRMContact)-[:PROMOTED_TO]->(canonical)
                WITH collect(DISTINCT canonical) + collect(DISTINCT alias) AS contacts
                UNWIND contacts AS contact
                WITH DISTINCT contact
                WHERE contact IS NOT NULL
                MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(contact)
                WHERE ($status IS NULL OR coalesce(a.status, "proposed") = $status)
                RETURN a.assertion_id AS claim_id,
                       a.claim_type AS claim_type,
                       a.value_json AS value_json,
                       a.status AS status,
                       a.sensitive AS sensitive,
                       null AS valid_from,
                       null AS valid_to,
                       a.confidence AS confidence,
                       a.source_system AS source_system,
                       a.updated_at AS updated_at
                ORDER BY a.updated_at DESC
                """
                ),
                contact_id=contact_id,
                status=status,
            ).data()
        results: list[dict[str, Any]] = []
        for row in rows:
            claim_id = row.get("claim_id")
            if not isinstance(claim_id, str) or not claim_id.strip():
                continue
            value_json = row.get("value_json")
            if isinstance(value_json, str):
                try:
                    parsed = json.loads(value_json)
                except Exception:
                    parsed = {}
            else:
                parsed = value_json if isinstance(value_json, dict) else {}
            if not isinstance(parsed, dict):
                parsed = {}
            results.append(
                {
                    "claim_id": claim_id,
                    "claim_type": _normalize_text(row.get("claim_type")) or "topic",
                    "value_json": parsed,
                    "status": _normalize_text(row.get("status")) or "proposed",
                    "sensitive": bool(row.get("sensitive", False)),
                    "valid_from": None,
                    "valid_to": None,
                    "confidence": _as_float(row.get("confidence")),
                    "source_system": _normalize_text(row.get("source_system")) or "unknown",
                }
            )
        return results
    with neo4j_session() as session:
        if session is None:
            return []

        if status:
            rows = _session_run(session, 
                """
                MATCH (c:Contact {contact_id: $contact_id})-[:HAS_CLAIM]->(cl:Claim)
                WHERE cl.status = $status
                RETURN cl.claim_id AS claim_id,
                       cl.claim_type AS claim_type,
                       cl.value_json AS value_json,
                       cl.status AS status,
                       cl.sensitive AS sensitive,
                       cl.valid_from AS valid_from,
                       cl.valid_to AS valid_to,
                       cl.confidence AS confidence,
                       cl.source_system AS source_system
                ORDER BY cl.created_at DESC
                """,
                contact_id=contact_id,
                status=status,
            ).data()
        else:
            rows = _session_run(session, 
                """
                MATCH (c:Contact {contact_id: $contact_id})-[:HAS_CLAIM]->(cl:Claim)
                RETURN cl.claim_id AS claim_id,
                       cl.claim_type AS claim_type,
                       cl.value_json AS value_json,
                       cl.status AS status,
                       cl.sensitive AS sensitive,
                       cl.valid_from AS valid_from,
                       cl.valid_to AS valid_to,
                       cl.confidence AS confidence,
                       cl.source_system AS source_system
                ORDER BY cl.created_at DESC
                """,
                contact_id=contact_id,
            ).data()

    claims: list[dict[str, Any]] = []
    for row in rows:
        claims.append(
            {
                "claim_id": row.get("claim_id"),
                "claim_type": row.get("claim_type"),
                "value_json": _as_components_json(row.get("value_json")),
                "status": row.get("status"),
                "sensitive": bool(row.get("sensitive", False)),
                "valid_from": row.get("valid_from"),
                "valid_to": row.get("valid_to"),
                "confidence": float(row.get("confidence", 0.0)),
                "source_system": row.get("source_system") or "mem0",
            }
        )
    return claims


def get_claim_by_id(claim_id: str) -> dict[str, Any] | None:
    with neo4j_session() as session:
        if session is None:
            return None
        rows = _session_run(session, 
            """
            MATCH (cl:Claim {claim_id: $claim_id})
            OPTIONAL MATCH (c:Contact)-[:HAS_CLAIM]->(cl)
            RETURN cl.claim_id AS claim_id,
                   cl.claim_type AS claim_type,
                   cl.value_json AS value_json,
                   cl.status AS status,
                   cl.sensitive AS sensitive,
                   cl.valid_from AS valid_from,
                   cl.valid_to AS valid_to,
                   cl.confidence AS confidence,
                   cl.source_system AS source_system,
                   c.contact_id AS contact_id
            LIMIT 1
            """,
            claim_id=claim_id,
        ).data()

    if not rows:
        return None
    row = rows[0]
    return {
        "claim_id": row.get("claim_id"),
        "claim_type": row.get("claim_type"),
        "value_json": _as_components_json(row.get("value_json")),
        "status": row.get("status"),
        "sensitive": bool(row.get("sensitive", False)),
        "valid_from": row.get("valid_from"),
        "valid_to": row.get("valid_to"),
        "confidence": float(row.get("confidence", 0.0)),
        "source_system": row.get("source_system") or "mem0",
        "contact_id": row.get("contact_id"),
    }


def update_claim_status(
    claim_id: str,
    status: str,
    *,
    value_json: dict[str, Any] | None = None,
    resolved_at_iso: str | None = None,
) -> None:
    with neo4j_session() as session:
        if session is None:
            return

        _session_run(session, 
            """
            MATCH (cl:Claim {claim_id: $claim_id})
            SET cl.status = $status,
                cl.resolved_at = CASE
                    WHEN $resolved_at IS NULL THEN cl.resolved_at
                    ELSE datetime($resolved_at)
                END,
                cl.value_json = CASE
                    WHEN $value_json IS NULL THEN cl.value_json
                    ELSE $value_json
                END
            """,
            claim_id=claim_id,
            status=status,
            value_json=_json_text(value_json) if value_json is not None else None,
            resolved_at=resolved_at_iso,
        )


def set_current_employer(contact_id: str, company_name: str, claim_id: str, resolved_at_iso: str) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        _session_run(session, 
            """
            MATCH (c:Contact {contact_id: $contact_id})
            OPTIONAL MATCH (c)-[existing:CURRENT_EMPLOYER]->(:Company)
            DELETE existing
            MERGE (co:Company {name: $company_name})
            MERGE (c)-[rel:CURRENT_EMPLOYER]->(co)
            SET rel.claim_id = $claim_id,
                rel.updated_at = datetime($resolved_at)
            """,
            contact_id=contact_id,
            company_name=company_name,
            claim_id=claim_id,
            resolved_at=resolved_at_iso,
        )
    upsert_relation_triple(
        contact_id=contact_id,
        interaction_id=f"resolution:{claim_id}",
        interaction_timestamp_iso=resolved_at_iso,
        subject_name="contact",
        predicate="works_at",
        object_name=company_name,
        claim_id=claim_id,
        confidence=0.95,
        status="accepted",
        source_system="resolution",
        uncertain=False,
        evidence_refs=[{"source": "resolution_task", "claim_id": claim_id}],
        subject_kind="Contact",
        object_kind="Company",
    )


def get_contact_company_hint(contact_id: str) -> str | None:
    with neo4j_session() as session:
        if session is None:
            return None
        if _v2_graph_reads_enabled():
            rows = _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                MATCH (c:CRMContact {external_id: $contact_id})
                OPTIONAL MATCH (c)-[r:WORKS_AT]->(co:CRMCompany)
                RETURN c.company AS company_hint,
                       co.name AS current_employer,
                       coalesce(r.updated_at, co.updated_at) AS rank_ts
                ORDER BY rank_ts DESC
                LIMIT 1
                """
                ),
                contact_id=contact_id,
            ).data()
        else:
            rows = _session_run(session, 
                """
                MATCH (c:Contact {contact_id: $contact_id})
                OPTIONAL MATCH (c)-[:CURRENT_EMPLOYER]->(co:Company)
                RETURN c.company AS company_hint, co.name AS current_employer
                LIMIT 1
                """,
                contact_id=contact_id,
            ).data()

    if not rows:
        return None
    row = rows[0]
    for key in ("current_employer", "company_hint"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_contact_company_hints(contact_ids: list[str]) -> dict[str, str]:
    if not contact_ids:
        return {}

    with neo4j_session() as session:
        if session is None:
            return {}
        if _v2_graph_reads_enabled():
            rows = _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                UNWIND $contact_ids AS cid
                OPTIONAL MATCH (c:CRMContact {external_id: cid})
                OPTIONAL MATCH (c)-[r:WORKS_AT]->(co:CRMCompany)
                WITH cid, c, co, r
                ORDER BY cid, coalesce(r.updated_at, co.updated_at) DESC
                WITH cid, collect({company: co.name, fallback: c.company})[0] AS top
                RETURN cid AS contact_id,
                       coalesce(top.company, top.fallback) AS company
                """
                ),
                contact_ids=contact_ids,
            ).data()
        else:
            rows = _session_run(session, 
                """
                UNWIND $contact_ids AS cid
                OPTIONAL MATCH (c:Contact {contact_id: cid})
                OPTIONAL MATCH (c)-[:CURRENT_EMPLOYER]->(co:Company)
                RETURN cid AS contact_id,
                       coalesce(co.name, c.company) AS company
                """,
                contact_ids=contact_ids,
            ).data()

    results: dict[str, str] = {}
    for row in rows:
        contact_id = row.get("contact_id")
        company = row.get("company")
        if not isinstance(contact_id, str) or not isinstance(company, str):
            continue
        company_value = company.strip()
        if company_value:
            results[contact_id] = company_value
    return results


def _contact_graph_path_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    latest_seen_at = item.get("latest_seen_at")
    latest_dt: datetime | None = None
    if isinstance(latest_seen_at, datetime):
        if latest_seen_at.tzinfo is None:
            latest_dt = latest_seen_at.replace(tzinfo=timezone.utc)
        else:
            latest_dt = latest_seen_at.astimezone(timezone.utc)
    elif isinstance(latest_seen_at, str) and latest_seen_at.strip():
        try:
            latest_dt = datetime.fromisoformat(latest_seen_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            latest_dt = None
    try:
        noise_penalty = float(item.get("noise_penalty", 0.0) or 0.0)
    except (TypeError, ValueError):
        noise_penalty = 0.0
    null_time_rank = 1 if latest_dt is None else 0
    latest_ts_rank = -latest_dt.timestamp() if latest_dt is not None else 0.0
    return (
        int(item.get("uncertain_hops") or 0),
        -int(item.get("opportunity_hits") or 0),
        noise_penalty,
        null_time_rank,
        latest_ts_rank,
        -float(item.get("avg_confidence") or 0.0),
        int(item.get("hops") or 0),
        str(item.get("path_text") or ""),
    )


def _normalized_compact_text(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower())).strip()


def _graph_path_noise_penalty(
    *,
    path_kind: str,
    predicates: list[str],
    tail_node: str | None,
    claim_type: str | None = None,
    keyword_hits: int = 0,
    opportunity_hits: int = 0,
) -> float:
    penalty = 0.0
    normalized_kind = _normalize_text(path_kind).lower()
    normalized_claim_type = _normalize_text(claim_type or "").lower()
    normalized_predicates = {_normalize_text(pred).lower() for pred in predicates if isinstance(pred, str)}
    tail_norm = _normalized_compact_text(tail_node)

    if normalized_kind == "coworker":
        penalty += 0.04
    if normalized_kind == "assertion":
        if normalized_claim_type == "relationship_signal":
            penalty += 0.08
        if normalized_claim_type == "topic":
            penalty += 0.18
        if normalized_predicates & {"related_to", "mentions", "discussed_topic"}:
            penalty += 0.08
        if tail_norm in _LOW_SIGNAL_GRAPH_PATH_TERMS:
            penalty += 0.20
        elif len(tail_norm) <= 3:
            penalty += 0.15
        if normalized_predicates == {"related_to"} and not opportunity_hits:
            penalty += 0.05

    if keyword_hits > 0:
        penalty -= min(0.12, keyword_hits * 0.05)
    if opportunity_hits > 0:
        penalty -= 0.06

    return round(max(0.0, min(0.5, penalty)), 4)


def _contact_graph_paths_v2(
    contact_id: str,
    *,
    objective: str | None = None,
    max_hops: int = 3,
    limit: int = 8,
    include_uncertain: bool = False,
    lookback_days: int | None = None,
) -> list[dict[str, Any]]:
    _ = max_hops  # V2 path generation currently emits curated 1-2 hop paths.
    keywords = _extract_keywords(objective or "", max_keywords=8)
    cutoff_dt: datetime | None = None
    if isinstance(lookback_days, int) and lookback_days > 0:
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    with neo4j_session() as session:
        if session is None:
            return []

        rows: list[dict[str, Any]] = []

        rows.extend(
            _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                MATCH (c:CRMContact {external_id: $contact_id})-[w:WORKS_AT]->(co:CRMCompany)
                RETURN c.display_name AS contact_name,
                       co.name AS company_name,
                       coalesce(w.updated_at, co.updated_at) AS updated_at
                LIMIT 10
                """
                ),
                contact_id=contact_id,
            ).data()
        )

        coworker_rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (c:CRMContact {external_id: $contact_id})-[w1:WORKS_AT]->(co:CRMCompany)<-[w2:WORKS_AT]-(other:CRMContact)
            WHERE other.external_id <> c.external_id
            RETURN c.display_name AS contact_name,
                   co.name AS company_name,
                   other.display_name AS coworker_name,
                   coalesce(w2.updated_at, w1.updated_at, co.updated_at) AS updated_at
            LIMIT 24
            """
            ),
            contact_id=contact_id,
        ).data()

        assertion_rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(c:CRMContact {external_id: $contact_id})
            WHERE coalesce(a.status, "proposed") <> "rejected"
              AND toLower(coalesce(a.claim_type, "")) <> "topic"
            OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(ch:EvidenceChunk)
            WITH a, c, collect(DISTINCT ch.interaction_id) AS interaction_ids
            RETURN c.display_name AS contact_name,
                   a.assertion_id AS assertion_id,
                   a.predicate AS predicate,
                   a.object_name AS object_name,
                   a.claim_type AS claim_type,
                   a.status AS status,
                   a.confidence AS confidence,
                   a.updated_at AS updated_at,
                   interaction_ids AS interaction_ids
            ORDER BY a.updated_at DESC, a.confidence DESC
            LIMIT 50
            """
            ),
            contact_id=contact_id,
        ).data()

        case_opp_rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (co:CaseOpportunity)-[:INVOLVES_CONTACT]->(c:CRMContact {external_id: $contact_id})
            WHERE coalesce(co.status, "open") = "open"
            RETURN c.display_name AS contact_name,
                   co.case_id AS case_id,
                   co.title AS title,
                   co.company_name AS company_name,
                   co.updated_at AS updated_at,
                   co.interaction_id AS interaction_id
            LIMIT 20
            """
            ),
            contact_id=contact_id,
        ).data()

        opp_rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (o:CRMOpportunity)-[:INVOLVES_CONTACT]->(c:CRMContact {external_id: $contact_id})
            WHERE coalesce(o.status, "open") = "open"
            OPTIONAL MATCH (o)-[:OPPORTUNITY_FOR_COMPANY|OPPORTUNITY_FOR_COMPANY_DERIVED]->(co:CRMCompany)
            RETURN c.display_name AS contact_name,
                   o.external_id AS opportunity_id,
                   o.title AS title,
                   co.name AS company_name,
                   o.updated_at AS updated_at
            LIMIT 20
            """
            ),
            contact_id=contact_id,
        ).data()

    results: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _row_time(value: Any) -> tuple[str | None, datetime | None]:
        if value is None:
            return None, None
        text = str(value)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
            return text, dt
        except ValueError:
            return text, None

    def _keep_by_time(dt: datetime | None) -> bool:
        return cutoff_dt is None or dt is None or dt >= cutoff_dt

    def _append_path(entry: dict[str, Any]) -> None:
        key = json.dumps(
            {
                "n": entry.get("node_names"),
                "p": entry.get("predicates"),
                "r": entry.get("relation_ids"),
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        if key in seen_keys:
            return
        seen_keys.add(key)
        results.append(entry)

    def _keyword_hits(path_text: str) -> int:
        if not keywords:
            return 0
        lowered = path_text.lower()
        return sum(1 for keyword in keywords if keyword in lowered)

    for row in rows:
        contact_name = _normalize_text(row.get("contact_name")) or contact_id
        company_name = _normalize_text(row.get("company_name"))
        if not company_name:
            continue
        latest_seen_at, latest_dt = _row_time(row.get("updated_at"))
        if not _keep_by_time(latest_dt):
            continue
        _append_path(
            {
                "path_text": _build_path_text([contact_name, company_name], ["works_at"]),
                "node_names": [contact_name, company_name],
                "predicates": ["works_at"],
                "relation_ids": [f"v2:works_at:{contact_id}:{_normalize_key(company_name)}"],
                "interaction_ids": [],
                "avg_confidence": 0.98,
                "hops": 1,
                "uncertain_hops": 0,
                "latest_seen_at": latest_seen_at,
                "recency_days": (datetime.now(timezone.utc) - latest_dt).days if latest_dt else None,
                "opportunity_hits": 0,
                "path_kind": "employment",
                "noise_penalty": _graph_path_noise_penalty(
                    path_kind="employment",
                    predicates=["works_at"],
                    tail_node=company_name,
                ),
            }
        )

    for row in coworker_rows:
        contact_name = _normalize_text(row.get("contact_name")) or contact_id
        company_name = _normalize_text(row.get("company_name"))
        coworker_name = _normalize_text(row.get("coworker_name"))
        if not company_name or not coworker_name:
            continue
        latest_seen_at, latest_dt = _row_time(row.get("updated_at"))
        if not _keep_by_time(latest_dt):
            continue
        _append_path(
            {
                "path_text": _build_path_text([contact_name, company_name, coworker_name], ["works_at", "works_at"]),
                "node_names": [contact_name, company_name, coworker_name],
                "predicates": ["works_at", "works_at"],
                "relation_ids": [
                    f"v2:works_at:{contact_id}:{_normalize_key(company_name)}",
                    f"v2:works_at:coworker:{_normalize_key(coworker_name)}:{_normalize_key(company_name)}",
                ],
                "interaction_ids": [],
                "avg_confidence": 0.98,
                "hops": 2,
                "uncertain_hops": 0,
                "latest_seen_at": latest_seen_at,
                "recency_days": (datetime.now(timezone.utc) - latest_dt).days if latest_dt else None,
                "opportunity_hits": 0,
                "path_kind": "coworker",
                "noise_penalty": _graph_path_noise_penalty(
                    path_kind="coworker",
                    predicates=["works_at", "works_at"],
                    tail_node=coworker_name,
                ),
            }
        )

    for row in assertion_rows:
        contact_name = _normalize_text(row.get("contact_name")) or contact_id
        predicate = _normalize_text(row.get("predicate")) or "related_to"
        object_name = _normalize_text(row.get("object_name"))
        claim_type = _normalize_text(row.get("claim_type")).lower()
        if not object_name:
            continue
        latest_seen_at, latest_dt = _row_time(row.get("updated_at"))
        if not _keep_by_time(latest_dt):
            continue
        confidence = _as_float(row.get("confidence"), 0.0)
        status = _normalize_text(row.get("status")).lower() or "proposed"
        uncertain = bool(status not in {"accepted", "verified"} or confidence < 0.8)
        if uncertain and not include_uncertain:
            continue
        path_text = _build_path_text([contact_name, object_name], [predicate])
        if not path_text:
            continue
        keyword_hits = _keyword_hits(path_text)
        opportunity_hits = 1 if claim_type == "opportunity" else 0
        noise_penalty = _graph_path_noise_penalty(
            path_kind="assertion",
            predicates=[predicate],
            tail_node=object_name,
            claim_type=claim_type,
            keyword_hits=keyword_hits,
            opportunity_hits=opportunity_hits,
        )
        if (
            claim_type in {"relationship_signal", "topic"}
            and noise_penalty >= 0.18
            and keyword_hits == 0
            and opportunity_hits == 0
        ):
            # Defensive query-layer filter for legacy/noisy assertions even if worker filters were weaker.
            continue
        _append_path(
            {
                "path_text": path_text,
                "node_names": [contact_name, object_name],
                "predicates": [predicate],
                "relation_ids": [_normalize_text(row.get("assertion_id")) or f"v2:assertion:{_normalize_key(predicate)}:{_normalize_key(object_name)}"],
                "interaction_ids": [
                    item for item in (row.get("interaction_ids") or []) if isinstance(item, str) and item.strip()
                ],
                "avg_confidence": round(confidence, 4),
                "hops": 1,
                "uncertain_hops": 1 if uncertain else 0,
                "latest_seen_at": latest_seen_at,
                "recency_days": (datetime.now(timezone.utc) - latest_dt).days if latest_dt else None,
                "opportunity_hits": opportunity_hits,
                "claim_type": claim_type,
                "path_kind": "assertion",
                "noise_penalty": noise_penalty,
            }
        )

    for row in case_opp_rows:
        contact_name = _normalize_text(row.get("contact_name")) or contact_id
        title = _normalize_text(row.get("title")) or "Case opportunity"
        company_name = _normalize_text(row.get("company_name"))
        tail = f"{title} ({company_name})" if company_name else title
        latest_seen_at, latest_dt = _row_time(row.get("updated_at"))
        if not _keep_by_time(latest_dt):
            continue
        _append_path(
            {
                "path_text": _build_path_text([contact_name, tail], ["case_opportunity"]),
                "node_names": [contact_name, tail],
                "predicates": ["case_opportunity"],
                "relation_ids": [_normalize_text(row.get("case_id")) or f"v2:case_opp:{_normalize_key(title)}"],
                "interaction_ids": [
                    _normalize_text(row.get("interaction_id"))
                ] if _normalize_text(row.get("interaction_id")) else [],
                "avg_confidence": 0.55,
                "hops": 1,
                "uncertain_hops": 1,
                "latest_seen_at": latest_seen_at,
                "recency_days": (datetime.now(timezone.utc) - latest_dt).days if latest_dt else None,
                "opportunity_hits": 1,
                "path_kind": "case_opportunity",
                "noise_penalty": _graph_path_noise_penalty(
                    path_kind="case_opportunity",
                    predicates=["case_opportunity"],
                    tail_node=tail,
                    opportunity_hits=1,
                ),
            }
        )

    for row in opp_rows:
        contact_name = _normalize_text(row.get("contact_name")) or contact_id
        title = _normalize_text(row.get("title")) or "Opportunity"
        company_name = _normalize_text(row.get("company_name"))
        tail = f"{title} ({company_name})" if company_name else title
        latest_seen_at, latest_dt = _row_time(row.get("updated_at"))
        if not _keep_by_time(latest_dt):
            continue
        _append_path(
            {
                "path_text": _build_path_text([contact_name, tail], ["opportunity"]),
                "node_names": [contact_name, tail],
                "predicates": ["opportunity"],
                "relation_ids": [_normalize_text(row.get("opportunity_id")) or f"v2:opp:{_normalize_key(title)}"],
                "interaction_ids": [],
                "avg_confidence": 0.9,
                "hops": 1,
                "uncertain_hops": 0,
                "latest_seen_at": latest_seen_at,
                "recency_days": (datetime.now(timezone.utc) - latest_dt).days if latest_dt else None,
                "opportunity_hits": 1,
                "path_kind": "opportunity",
                "noise_penalty": _graph_path_noise_penalty(
                    path_kind="opportunity",
                    predicates=["opportunity"],
                    tail_node=tail,
                    opportunity_hits=1,
                ),
            }
        )

    filtered: list[dict[str, Any]] = []
    for row in results:
        if row.get("uncertain_hops", 0) and not include_uncertain:
            continue
        path_text = str(row.get("path_text") or "")
        if not path_text:
            continue
        lower = path_text.lower()
        keyword_hits = int(row.get("keyword_hits") or 0) if "keyword_hits" in row else sum(1 for keyword in keywords if keyword in lower)
        if keywords and keyword_hits == 0 and int(row.get("opportunity_hits") or 0) <= 0:
            continue
        row["keyword_hits"] = keyword_hits
        filtered.append(row)

    filtered.sort(key=_contact_graph_path_sort_key)
    return filtered[: max(1, limit)]


def _contact_graph_metrics_v2(contact_id: str, *, lookback_days: int = 120) -> dict[str, Any]:
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max(1, int(lookback_days)))
    cutoff_iso = cutoff_dt.isoformat()
    with neo4j_session() as session:
        if session is None:
            return {
                "direct_relation_count": 0,
                "accepted_relation_count": 0,
                "uncertain_relation_count": 0,
                "recent_relation_count": 0,
                "entity_reach_2hop": 0,
                "path_count_2hop": 0,
                "opportunity_edge_count": 0,
                "recent_opportunity_edge_count": 0,
                "stale_opportunity_edge_count": 0,
                "latest_relation_at": None,
            }
        rel_rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (c:CRMContact {external_id: $contact_id})
            OPTIONAL MATCH (c)-[w:WORKS_AT]->(co:CRMCompany)
            WITH c, collect(DISTINCT {kind:"works_at", confidence:0.98, status:"accepted", updated_at: coalesce(w.updated_at, co.updated_at)}) AS work_edges
            OPTIONAL MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(c)
            WHERE coalesce(a.status, "proposed") <> "rejected"
              AND toLower(coalesce(a.claim_type, "")) <> "topic"
            WITH c, work_edges,
                 collect(DISTINCT {
                    kind:"assertion",
                    confidence: coalesce(a.confidence, 0.0),
                    status: toLower(coalesce(a.status, "proposed")),
                    updated_at: a.updated_at
                 }) AS assertions
            OPTIONAL MATCH (o:CRMOpportunity)-[:INVOLVES_CONTACT]->(c)
            WHERE coalesce(o.status, "open") = "open"
            WITH c, work_edges, assertions,
                 collect(DISTINCT {updated_at: o.updated_at, created_at: o.created_at}) AS opps
            OPTIONAL MATCH (coCase:CaseOpportunity)-[:INVOLVES_CONTACT]->(c)
            WHERE coalesce(coCase.status, "open") = "open"
            RETURN work_edges, assertions, opps,
                   collect(DISTINCT {updated_at: coCase.updated_at, created_at: coCase.created_at}) AS case_opps
            LIMIT 1
            """
            ),
            contact_id=contact_id,
        ).data()

    row = rel_rows[0] if rel_rows else {}
    work_edges = [x for x in (row.get("work_edges") or []) if isinstance(x, dict)]
    assertions = [x for x in (row.get("assertions") or []) if isinstance(x, dict)]
    opps = [x for x in (row.get("opps") or []) if isinstance(x, dict)]
    case_opps = [x for x in (row.get("case_opps") or []) if isinstance(x, dict)]

    def _parse_dt(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    relation_entries = work_edges + assertions
    direct_relation_count = len(relation_entries)
    accepted_relation_count = 0
    uncertain_relation_count = 0
    recent_relation_count = 0
    latest_relation_dt: datetime | None = None
    for item in relation_entries:
        status = _normalize_text(item.get("status")).lower() or "proposed"
        confidence = _as_float(item.get("confidence"), 0.0)
        dt = _parse_dt(item.get("updated_at"))
        if item.get("kind") == "works_at" or status in {"accepted", "verified"}:
            accepted_relation_count += 1
        if item.get("kind") != "works_at" and (status not in {"accepted", "verified"} or confidence < 0.8):
            uncertain_relation_count += 1
        if dt is not None and dt >= cutoff_dt:
            recent_relation_count += 1
        if dt is not None and (latest_relation_dt is None or dt > latest_relation_dt):
            latest_relation_dt = dt

    path_rows = _contact_graph_paths_v2(
        contact_id,
        objective=None,
        max_hops=2,
        limit=128,
        include_uncertain=True,
        lookback_days=lookback_days,
    )
    entity_reach: set[str] = set()
    for row_item in path_rows:
        for node_name in row_item.get("node_names") or []:
            if isinstance(node_name, str) and node_name.strip():
                entity_reach.add(node_name.strip().lower())
    if entity_reach:
        entity_reach.discard(_normalize_text(contact_id).lower())

    opp_timestamps: list[datetime] = []
    recent_opportunity_edge_count = 0
    stale_opportunity_edge_count = 0
    for coll in (opps, case_opps):
        for item in coll:
            dt = _parse_dt(item.get("updated_at")) or _parse_dt(item.get("created_at"))
            if dt is not None:
                opp_timestamps.append(dt)
            if dt is not None and dt >= cutoff_dt:
                recent_opportunity_edge_count += 1
            else:
                stale_opportunity_edge_count += 1

    if opp_timestamps:
        latest_opp_dt = max(opp_timestamps)
        if latest_relation_dt is None or latest_opp_dt > latest_relation_dt:
            latest_relation_dt = latest_opp_dt

    return {
        "direct_relation_count": int(direct_relation_count),
        "accepted_relation_count": int(accepted_relation_count),
        "uncertain_relation_count": int(uncertain_relation_count),
        "recent_relation_count": int(recent_relation_count),
        "entity_reach_2hop": max(0, len(entity_reach) - 1) if entity_reach else 0,
        "path_count_2hop": int(len(path_rows)),
        "opportunity_edge_count": int(len(opps) + len(case_opps)),
        "recent_opportunity_edge_count": int(recent_opportunity_edge_count),
        "stale_opportunity_edge_count": int(stale_opportunity_edge_count),
        "latest_relation_at": latest_relation_dt.isoformat() if latest_relation_dt else None,
    }


def get_contact_graph_paths(
    contact_id: str,
    *,
    objective: str | None = None,
    max_hops: int = 3,
    limit: int = 8,
    include_uncertain: bool = False,
    lookback_days: int | None = None,
) -> list[dict[str, Any]]:
    if _v2_graph_reads_enabled():
        return _contact_graph_paths_v2(
            contact_id,
            objective=objective,
            max_hops=max_hops,
            limit=limit,
            include_uncertain=include_uncertain,
            lookback_days=lookback_days,
        )
    hops = max(1, min(int(max_hops), 3))
    fetch_limit = max(limit * 8, 40)
    keywords = _extract_keywords(objective or "", max_keywords=8)
    excluded_predicates = sorted(_LEGACY_SCORING_PREDICATE_EXCLUSIONS)
    cutoff_iso = None
    if isinstance(lookback_days, int) and lookback_days > 0:
        cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()

    query = f"""
            MATCH (c:Contact {{contact_id: $contact_id}})-[:AS_ENTITY]->(root:Entity)
            MATCH p=(root)-[rels:RELATES_TO*1..{hops}]-(target:Entity)
            WHERE all(
                rel IN rels
                WHERE coalesce(rel.status, "proposed") IN ["accepted", "proposed"]
                  AND NOT (coalesce(rel.predicate_norm, "") IN $excluded_predicates)
                  AND (
                    $cutoff_iso IS NULL
                    OR coalesce(rel.last_seen_at, rel.first_seen_at, datetime("1970-01-01T00:00:00Z")) >= datetime($cutoff_iso)
                  )
            )
            WITH nodes(p) AS ns,
                 rels,
                 reduce(total = 0.0, rel IN rels | total + coalesce(rel.confidence, 0.5)) / toFloat(size(rels)) AS avg_confidence,
                 size([rel IN rels WHERE coalesce(rel.uncertain, false)]) AS uncertain_hops,
                 [rel IN rels | coalesce(rel.interaction_id, "")] AS interaction_ids,
                 size([
                    rel IN rels
                    WHERE
                        toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "opportun"
                        OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "proposal"
                        OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "deal"
                        OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "pilot"
                        OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "workshop"
                        OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "next_step"
                        OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "milestone"
                        OR toLower(coalesce(rel.object_name, "")) CONTAINS "opportun"
                        OR toLower(coalesce(rel.object_name, "")) CONTAINS "proposal"
                        OR toLower(coalesce(rel.object_name, "")) CONTAINS "deal"
                 ]) AS opportunity_hits,
                 reduce(
                    latest = datetime("1970-01-01T00:00:00Z"),
                    rel IN rels |
                        CASE
                            WHEN coalesce(rel.last_seen_at, rel.first_seen_at, datetime("1970-01-01T00:00:00Z")) > latest
                                THEN coalesce(rel.last_seen_at, rel.first_seen_at, datetime("1970-01-01T00:00:00Z"))
                            ELSE latest
                        END
                 ) AS latest_seen_at
            RETURN [node IN ns | coalesce(node.name, node.contact_id, "")] AS node_names,
                   [rel IN rels | coalesce(rel.predicate, "related_to")] AS predicates,
                   [rel IN rels | coalesce(rel.relation_id, "")] AS relation_ids,
                   [rel IN rels | coalesce(rel.uncertain, false)] AS uncertain_flags,
                   interaction_ids AS interaction_ids,
                   opportunity_hits AS opportunity_hits,
                   toString(latest_seen_at) AS latest_seen_at,
                   avg_confidence AS avg_confidence,
                   uncertain_hops AS uncertain_hops,
                   size(rels) AS hops
            ORDER BY uncertain_hops ASC, opportunity_hits DESC, latest_seen_at DESC, avg_confidence DESC, hops ASC
            LIMIT $limit
            """

    with neo4j_session() as session:
        if session is None:
            return []
        rows = _session_run(session, 
            query,
            contact_id=contact_id,
            limit=fetch_limit,
            cutoff_iso=cutoff_iso,
            excluded_predicates=excluded_predicates,
        ).data()

    results: list[dict[str, Any]] = []
    for row in rows:
        uncertain_flags = row.get("uncertain_flags") or []
        uncertain_count = sum(1 for flag in uncertain_flags if bool(flag))
        if uncertain_count and not include_uncertain:
            continue

        node_names = [name for name in row.get("node_names") or [] if isinstance(name, str) and name.strip()]
        predicates = [item for item in row.get("predicates") or [] if isinstance(item, str) and item.strip()]
        if len(node_names) < 2 or not predicates:
            continue

        path_text = _build_path_text(node_names, predicates)
        if not path_text:
            continue
        opportunity_hits = int(row.get("opportunity_hits") or 0)
        path_text_lower = path_text.lower()
        if keywords and not any(keyword in path_text_lower for keyword in keywords) and opportunity_hits <= 0:
            continue

        latest_seen_at = row.get("latest_seen_at")
        recency_days = None
        if isinstance(latest_seen_at, str) and latest_seen_at.strip():
            try:
                latest_dt = datetime.fromisoformat(latest_seen_at.replace("Z", "+00:00"))
                recency_days = max(0, (datetime.now(timezone.utc) - latest_dt.astimezone(timezone.utc)).days)
            except ValueError:
                recency_days = None

        results.append(
            {
                "path_text": path_text,
                "node_names": node_names,
                "predicates": predicates,
                "relation_ids": [item for item in row.get("relation_ids") or [] if isinstance(item, str) and item],
                "interaction_ids": [item for item in row.get("interaction_ids") or [] if isinstance(item, str) and item],
                "avg_confidence": round(_as_float(row.get("avg_confidence"), 0.0), 4),
                "hops": int(row.get("hops") or 0),
                "uncertain_hops": uncertain_count,
                "latest_seen_at": latest_seen_at if isinstance(latest_seen_at, str) and latest_seen_at else None,
                "recency_days": recency_days,
                "opportunity_hits": opportunity_hits,
            }
        )
        if len(results) >= limit:
            break
    return results


def get_contact_graph_metrics(contact_id: str, *, lookback_days: int = 120) -> dict[str, Any]:
    if _v2_graph_reads_enabled():
        return _contact_graph_metrics_v2(contact_id, lookback_days=lookback_days)
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=max(1, int(lookback_days)))).isoformat()
    excluded_predicates = sorted(_LEGACY_SCORING_PREDICATE_EXCLUSIONS)

    with neo4j_session() as session:
        if session is None:
            return {
                "direct_relation_count": 0,
                "accepted_relation_count": 0,
                "uncertain_relation_count": 0,
                "recent_relation_count": 0,
                "entity_reach_2hop": 0,
                "path_count_2hop": 0,
                "opportunity_edge_count": 0,
                "recent_opportunity_edge_count": 0,
                "stale_opportunity_edge_count": 0,
                "latest_relation_at": None,
            }

        row_data = _session_run(session, 
            """
            MATCH (c:Contact {contact_id: $contact_id})-[:AS_ENTITY]->(root:Entity)
            OPTIONAL MATCH (root)-[direct:RELATES_TO]-(:Entity)
            WHERE coalesce(direct.status, "proposed") IN ["accepted", "proposed"]
              AND NOT (coalesce(direct.predicate_norm, "") IN $excluded_predicates)
            WITH root,
                 count(direct) AS direct_relation_count,
                 count(CASE WHEN coalesce(direct.status, "proposed") = "accepted" THEN 1 END) AS accepted_relation_count,
                 count(CASE WHEN coalesce(direct.uncertain, false) THEN 1 END) AS uncertain_relation_count,
                 count(CASE WHEN direct.last_seen_at >= datetime($cutoff_iso) THEN 1 END) AS recent_relation_count
            OPTIONAL MATCH (root)-[:RELATES_TO*1..2]-(reach:Entity)
            WITH root,
                 direct_relation_count,
                 accepted_relation_count,
                 uncertain_relation_count,
                 recent_relation_count,
                 count(DISTINCT reach) AS entity_reach_2hop
            OPTIONAL MATCH p=(root)-[hop:RELATES_TO*1..2]-(:Entity)
            WHERE all(
                rel IN hop
                WHERE coalesce(rel.status, "proposed") IN ["accepted", "proposed"]
                  AND NOT (coalesce(rel.predicate_norm, "") IN $excluded_predicates)
            )
            RETURN direct_relation_count,
                   accepted_relation_count,
                   uncertain_relation_count,
                   recent_relation_count,
                   entity_reach_2hop,
                   count(DISTINCT p) AS path_count_2hop
            LIMIT 1
            """,
            contact_id=contact_id,
            cutoff_iso=cutoff_iso,
            excluded_predicates=excluded_predicates,
        ).data()

        opportunity_data = _session_run(session, 
            """
            MATCH (c:Contact {contact_id: $contact_id})-[:AS_ENTITY]->(root:Entity)
            OPTIONAL MATCH (root)-[r:RELATES_TO]-(:Entity)
            WHERE coalesce(r.status, "proposed") IN ["accepted", "proposed"]
              AND NOT (coalesce(r.predicate_norm, "") IN $excluded_predicates)
            WITH collect(r) AS rels
            WITH
                rels,
                [rel IN rels WHERE
                    toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "opportun"
                    OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "proposal"
                    OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "deal"
                    OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "pilot"
                    OR toLower(coalesce(rel.predicate_norm, rel.predicate, "")) CONTAINS "workshop"
                    OR toLower(coalesce(rel.object_name, "")) CONTAINS "opportun"
                    OR toLower(coalesce(rel.object_name, "")) CONTAINS "proposal"
                    OR toLower(coalesce(rel.object_name, "")) CONTAINS "deal"
                ] AS opp_rels
            RETURN size(opp_rels) AS opportunity_edge_count,
                   size([
                        rel IN opp_rels
                        WHERE coalesce(rel.last_seen_at, rel.first_seen_at, datetime("1970-01-01T00:00:00Z")) >= datetime($cutoff_iso)
                   ]) AS recent_opportunity_edge_count,
                   toString(reduce(
                        latest = datetime("1970-01-01T00:00:00Z"),
                        rel IN rels |
                            CASE
                                WHEN coalesce(rel.last_seen_at, rel.first_seen_at, datetime("1970-01-01T00:00:00Z")) > latest
                                    THEN coalesce(rel.last_seen_at, rel.first_seen_at, datetime("1970-01-01T00:00:00Z"))
                                ELSE latest
                            END
                   )) AS latest_relation_at
            LIMIT 1
            """,
            contact_id=contact_id,
            cutoff_iso=cutoff_iso,
            excluded_predicates=excluded_predicates,
        ).data()

    row = row_data[0] if row_data else {}
    opp_row = opportunity_data[0] if opportunity_data else {}
    return {
        "direct_relation_count": int(row.get("direct_relation_count") or 0),
        "accepted_relation_count": int(row.get("accepted_relation_count") or 0),
        "uncertain_relation_count": int(row.get("uncertain_relation_count") or 0),
        "recent_relation_count": int(row.get("recent_relation_count") or 0),
        "entity_reach_2hop": int(row.get("entity_reach_2hop") or 0),
        "path_count_2hop": int(row.get("path_count_2hop") or 0),
        "opportunity_edge_count": int(opp_row.get("opportunity_edge_count") or 0),
        "recent_opportunity_edge_count": int(opp_row.get("recent_opportunity_edge_count") or 0),
        "stale_opportunity_edge_count": max(
            0,
            int(opp_row.get("opportunity_edge_count") or 0) - int(opp_row.get("recent_opportunity_edge_count") or 0),
        ),
        "latest_relation_at": (
            opp_row.get("latest_relation_at")
            if isinstance(opp_row.get("latest_relation_at"), str) and opp_row.get("latest_relation_at")
            else None
        ),
    }


def _json_text(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=True, separators=(",", ":"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_extraction_event_v2(
    *,
    interaction_id: str,
    stage: str,
    status: str,
    extractor: str,
    source_system: str,
    error_message: str | None = None,
) -> str:
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"extract:{interaction_id}:{stage}:{extractor}"))
    with neo4j_session() as session:
        if session is None:
            return event_id
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="ExtractionEvent",
            ontology_label_identifier=_HS_EXTRACTION_EVENT_LABEL,
            key_property="event_id",
            key_value=event_id,
        )
        _session_run(session, 
            """
            MERGE (evt:ExtractionEvent {event_id: $event_id})
            SET evt.interaction_id = $interaction_id,
                evt.stage = $stage,
                evt.status = $status,
                evt.extractor = $extractor,
                evt.source_system = $source_system,
                evt.error_message = $error_message,
                evt.ont_class = $event_ont_class,
                evt.updated_at = datetime($updated_at)
            """,
            event_id=event_id,
            interaction_id=interaction_id,
            stage=stage,
            status=status,
            extractor=extractor,
            source_system=source_system,
            error_message=error_message,
            event_ont_class=_ont_class_for_v2_label("ExtractionEvent"),
            updated_at=_now_iso(),
        )
        if _HS_EXTRACTION_EVENT_LABEL:
            _session_run(session, 
                f"""
                MATCH (evt:ExtractionEvent {{event_id: $event_id}})
                SET evt:{_HS_EXTRACTION_EVENT_LABEL}
                """,
                event_id=event_id,
            )
    return event_id


def create_assertion_with_evidence_v2(
    *,
    interaction_id: str,
    claim: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
    contact_id: str | None = None,
    source_system: str = "cognee",
    extractor: str = "cognee",
    entity_status: str = "provisional",
    gate_results: dict[str, Any] | None = None,
) -> str:
    if not evidence_refs:
        raise ValueError("Assertion write rejected: at least one evidence ref is required.")
    for ref in evidence_refs:
        if not isinstance(ref, dict):
            raise ValueError("Assertion write rejected: malformed evidence ref.")
        if not ref.get("chunk_id"):
            raise ValueError("Assertion write rejected: chunk_id is required in evidence.")
        if ref.get("span_json") is None:
            raise ValueError("Assertion write rejected: span_json is required in evidence.")

    claim_id = _normalize_text(claim.get("claim_id")) or str(uuid.uuid4())
    value_json = claim.get("value_json") if isinstance(claim.get("value_json"), dict) else {}
    predicate = _normalize_text(value_json.get("predicate")) or "related_to"
    explicit_ontology_predicate = _normalize_text(claim.get("ontology_predicate"))
    ontology_supported = bool(claim.get("ontology_supported")) if "ontology_supported" in claim else bool(
        predicate_token_to_ontology_property(predicate)
    )
    claim_type_name = _normalize_text(claim.get("claim_type")) or "topic"
    object_name = (
        _normalize_text(value_json.get("object"))
        or _normalize_text(value_json.get("company"))
        or _normalize_text(value_json.get("label"))
    )
    assertion_id = f"assertion:{claim_id}"
    extraction_event_id = create_extraction_event_v2(
        interaction_id=interaction_id,
        stage="interaction_processing",
        status="success",
        extractor=extractor,
        source_system=source_system,
    )
    now_iso = _now_iso()

    with neo4j_session() as session:
        if session is None:
            return assertion_id
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="CRMEngagement",
            ontology_label_identifier=_HS_ENGAGEMENT_BASE_LABEL,
            key_property="external_id",
            key_value=interaction_id,
        )
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="ExtractionEvent",
            ontology_label_identifier=_HS_EXTRACTION_EVENT_LABEL,
            key_property="event_id",
            key_value=extraction_event_id,
        )
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="KGAssertion",
            ontology_label_identifier=_HS_ASSERTION_LABEL,
            key_property="assertion_id",
            key_value=assertion_id,
        )
        resolved_object_name = object_name
        value_json_for_write = dict(value_json)
        if contact_id and object_name and claim_type_name.lower() in {"employment", "opportunity"}:
            resolved_object_name = _prefer_contact_company_for_alias_candidate(
                session,
                contact_id=contact_id,
                candidate_company=object_name,
            )
            if resolved_object_name != object_name:
                if isinstance(value_json_for_write.get("company"), str):
                    value_json_for_write["company"] = resolved_object_name
                if isinstance(value_json_for_write.get("object"), str):
                    value_json_for_write["object"] = resolved_object_name
        has_assertion_merge_fragment = (
            """
            MERGE (eng)-[ha:HAS_ASSERTION]->(a)
            SET ha.ont_predicate = $has_assertion_ont_predicate
            """
            if _v2_physical_projection_writes_enabled()
            else ""
        )
        _session_run(session, 
            (
                """
            MERGE (eng:CRMEngagement {external_id: $interaction_id})
            ON CREATE SET eng.created_at = datetime($created_at)
            SET eng.updated_at = datetime($updated_at)
            MERGE (evt:ExtractionEvent {event_id: $extraction_event_id})
            MERGE (a:KGAssertion {assertion_id: $assertion_id})
            SET a.claim_id = $claim_id,
                a.claim_type = $claim_type,
                a.status = $status,
                a.confidence = $confidence,
                a.source_system = $source_system,
                a.sensitive = $sensitive,
                a.predicate = $predicate,
                a.ont_class = $assertion_ont_class,
                a.ont_predicate = $assertion_ont_predicate,
                a.ontology_supported = $ontology_supported,
                a.object_name = $object_name,
                a.value_json = $value_json,
                a.entity_status = $entity_status,
                a.gate_results_json = $gate_results_json,
                a.updated_at = datetime($updated_at)
            """
                + has_assertion_merge_fragment
                + """
            MERGE (a)-[fe:FROM_EXTRACTION_EVENT]->(evt)
            SET fe.ont_predicate = $from_extraction_event_ont_predicate
            """
            ),
            interaction_id=interaction_id,
            created_at=now_iso,
            updated_at=now_iso,
            extraction_event_id=extraction_event_id,
            assertion_id=assertion_id,
            claim_id=claim_id,
            claim_type=claim_type_name,
            status=_normalize_text(claim.get("status")) or "proposed",
            confidence=float(claim.get("confidence", 0.0) or 0.0),
            source_system=source_system,
            sensitive=bool(claim.get("sensitive", False)),
            predicate=predicate,
            assertion_ont_class=_ont_class_for_v2_label("KGAssertion"),
            assertion_ont_predicate=(
                explicit_ontology_predicate
                if ontology_supported and explicit_ontology_predicate
                else (predicate_token_to_ontology_property(predicate) or "")
                if ontology_supported
                else ""
            ),
            ontology_supported=ontology_supported,
            object_name=resolved_object_name,
            value_json=_json_text(value_json_for_write),
            entity_status=_normalize_text(entity_status) or "provisional",
            gate_results_json=_json_text(gate_results),
            has_assertion_ont_predicate=_ont_predicate_for_rel("HAS_ASSERTION"),
            from_extraction_event_ont_predicate=_ont_predicate_for_rel("FROM_EXTRACTION_EVENT"),
        )
        if (
            _HS_ASSERTION_LABEL
            and _HS_EXTRACTION_EVENT_LABEL
            and _HS_DERIVED_FROM_ENGAGEMENT_REL
            and _HS_EXTRACTION_EVENT_REL
            and _HS_ENGAGEMENT_BASE_LABEL
        ):
            _session_run(session, 
                f"""
                MATCH (eng:CRMEngagement {{external_id: $interaction_id}})
                MATCH (evt:ExtractionEvent {{event_id: $extraction_event_id}})
                MATCH (a:KGAssertion {{assertion_id: $assertion_id}})
                SET eng:{_HS_ENGAGEMENT_BASE_LABEL}
                SET evt:{_HS_EXTRACTION_EVENT_LABEL}
                SET a:{_HS_ASSERTION_LABEL}
                MERGE (a)-[r1:{_HS_DERIVED_FROM_ENGAGEMENT_REL}]->(eng)
                SET r1.ont_predicate = $has_assertion_ont_predicate
                MERGE (a)-[r2:{_HS_EXTRACTION_EVENT_REL}]->(evt)
                SET r2.ont_predicate = $from_extraction_event_ont_predicate
                """,
                interaction_id=interaction_id,
                extraction_event_id=extraction_event_id,
                assertion_id=assertion_id,
                has_assertion_ont_predicate=_ont_predicate_for_rel("HAS_ASSERTION"),
                from_extraction_event_ont_predicate=_ont_predicate_for_rel("FROM_EXTRACTION_EVENT"),
            )
        for ref in evidence_refs:
            evidence_id = _normalize_text(ref.get("evidence_id")) or str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"{interaction_id}:{ref.get('chunk_id')}")
            )
            _adopt_ontology_node_into_v2_label(
                session,
                v2_label="EvidenceChunk",
                ontology_label_identifier=_HS_SOURCE_ARTIFACT_LABEL,
                key_property="chunk_id",
                key_value=_normalize_text(ref.get("chunk_id")),
            )
            _session_run(session, 
                """
                MATCH (a:KGAssertion {assertion_id: $assertion_id})
                MERGE (ch:EvidenceChunk {chunk_id: $chunk_id})
                SET ch.interaction_id = $interaction_id,
                    ch.span_json = $span_json,
                    ch.quote_hash = $quote_hash,
                    ch.ont_class = $evidence_chunk_ont_class,
                    ch.updated_at = datetime($updated_at)
                MERGE (a)-[r:SUPPORTED_BY]->(ch)
                SET r.evidence_id = $evidence_id
                SET r.ont_predicate = $supported_by_ont_predicate
                """,
                assertion_id=assertion_id,
                chunk_id=ref.get("chunk_id"),
                interaction_id=ref.get("interaction_id") or interaction_id,
                span_json=_json_text(ref.get("span_json", {})),
                quote_hash=_normalize_text(ref.get("quote_hash")),
                updated_at=now_iso,
                evidence_id=evidence_id,
                evidence_chunk_ont_class=_ont_class_for_v2_label("EvidenceChunk"),
                supported_by_ont_predicate=_ont_predicate_for_rel("SUPPORTED_BY"),
            )
            if _HS_SOURCE_ARTIFACT_LABEL and _HS_SOURCE_ARTIFACT_REL and _HS_ASSERTION_LABEL:
                _session_run(session, 
                    f"""
                    MATCH (a:KGAssertion {{assertion_id: $assertion_id}})
                    MATCH (ch:EvidenceChunk {{chunk_id: $chunk_id}})
                    SET a:{_HS_ASSERTION_LABEL}
                    SET ch:{_HS_SOURCE_ARTIFACT_LABEL}
                    MERGE (a)-[r:{_HS_SOURCE_ARTIFACT_REL}]->(ch)
                    SET r.evidence_id = $evidence_id,
                        r.ont_predicate = $supported_by_ont_predicate
                    """,
                    assertion_id=assertion_id,
                    chunk_id=ref.get("chunk_id"),
                    evidence_id=evidence_id,
                    supported_by_ont_predicate=_ont_predicate_for_rel("SUPPORTED_BY"),
                )
        if contact_id:
            is_provisional = str(contact_id).startswith("contact:provisional:")
            _adopt_ontology_node_into_v2_label(
                session,
                v2_label="CRMContact",
                ontology_label_identifier=_HS_CONTACT_LABEL,
                key_property="external_id",
                key_value=contact_id,
            )
            _session_run(session, 
                """
                MATCH (a:KGAssertion {assertion_id: $assertion_id})
                MERGE (c:CRMContact {external_id: $contact_id})
                ON CREATE SET c.created_at = datetime($created_at),
                              c.entity_status = CASE WHEN $is_provisional THEN "provisional" ELSE "canonical" END,
                              c.ont_class = $contact_ont_class
                SET c.updated_at = datetime($updated_at),
                    c.ont_class = coalesce(c.ont_class, $contact_ont_class),
                    c.entity_status = CASE
                        WHEN c.entity_status = "canonical" THEN "canonical"
                        WHEN $is_provisional THEN "provisional"
                        ELSE "canonical"
                    END
                MERGE (a)-[r:ASSERTS_ABOUT_CONTACT]->(c)
                SET r.ont_predicate = $asserts_about_contact_ont_predicate
                """,
                assertion_id=assertion_id,
                contact_id=contact_id,
                created_at=now_iso,
                updated_at=now_iso,
                is_provisional=is_provisional,
                contact_ont_class=_ont_class_for_v2_label("CRMContact"),
                asserts_about_contact_ont_predicate=_ont_predicate_for_rel("ASSERTS_ABOUT_CONTACT"),
            )
            if _HS_CONTACT_LABEL and _HS_ASSERTION_OBJECT_REL and _HS_ASSERTION_LABEL:
                _session_run(session, 
                    f"""
                    MATCH (a:KGAssertion {{assertion_id: $assertion_id}})
                    MATCH (c:CRMContact {{external_id: $contact_id}})
                    SET a:{_HS_ASSERTION_LABEL}
                    SET c:{_HS_CONTACT_LABEL}
                    MERGE (a)-[r:{_HS_ASSERTION_OBJECT_REL}]->(c)
                    SET r.ont_predicate = $asserts_about_contact_ont_predicate
                    """,
                    assertion_id=assertion_id,
                    contact_id=contact_id,
                    asserts_about_contact_ont_predicate=_ont_predicate_for_rel("ASSERTS_ABOUT_CONTACT"),
                )
        if resolved_object_name and claim_type_name.lower() in {"employment", "opportunity"}:
            company_external_id = f"company:auto:{_normalize_key(resolved_object_name)}"
            _adopt_ontology_node_into_v2_label(
                session,
                v2_label="CRMCompany",
                ontology_label_identifier=_HS_COMPANY_LABEL,
                key_property="external_id",
                key_value=company_external_id,
            )
            _session_run(session, 
                """
                MATCH (a:KGAssertion {assertion_id: $assertion_id})
                MERGE (co:CRMCompany {external_id: $company_external_id})
                ON CREATE SET co.name = $company_name,
                              co.ont_class = $company_ont_class,
                              co.entity_status = "provisional",
                              co.created_at = datetime($created_at)
                SET co.updated_at = datetime($updated_at),
                    co.ont_class = coalesce(co.ont_class, $company_ont_class)
                MERGE (a)-[r:ASSERTS_ABOUT_COMPANY]->(co)
                SET r.ont_predicate = $asserts_about_company_ont_predicate
                """,
                assertion_id=assertion_id,
                company_external_id=company_external_id,
                company_name=resolved_object_name,
                created_at=now_iso,
                updated_at=now_iso,
                company_ont_class=_ont_class_for_v2_label("CRMCompany"),
                asserts_about_company_ont_predicate=_ont_predicate_for_rel("ASSERTS_ABOUT_COMPANY"),
            )
            if _HS_COMPANY_LABEL and _HS_ASSERTION_OBJECT_REL and _HS_ASSERTION_LABEL:
                _session_run(session, 
                    f"""
                    MATCH (a:KGAssertion {{assertion_id: $assertion_id}})
                    MATCH (co:CRMCompany {{external_id: $company_external_id}})
                    SET a:{_HS_ASSERTION_LABEL}
                    SET co:{_HS_COMPANY_LABEL}
                    MERGE (a)-[r:{_HS_ASSERTION_OBJECT_REL}]->(co)
                    SET r.ont_predicate = $asserts_about_company_ont_predicate
                    """,
                    assertion_id=assertion_id,
                    company_external_id=company_external_id,
                    asserts_about_company_ont_predicate=_ont_predicate_for_rel("ASSERTS_ABOUT_COMPANY"),
                )
    return assertion_id


def upsert_case_contact_v2(
    *,
    email: str,
    interaction_id: str,
    display_name: str | None = None,
    promotion_reason: str = "auto_from_interaction",
    gate_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_email = _normalize_text(email).lower()
    if not normalized_email:
        return {"created": False}
    case_id = f"case_contact:{uuid.uuid5(uuid.NAMESPACE_URL, normalized_email)}"
    provisional_contact_id = f"contact:provisional:{uuid.uuid5(uuid.NAMESPACE_URL, normalized_email)}"
    now_iso = _now_iso()
    with neo4j_session() as session:
        if session is None:
            return {"created": False, "case_id": case_id, "provisional_contact_id": provisional_contact_id}
        existing = _session_run(session, 
            "MATCH (c:CaseContact {case_id: $case_id}) RETURN c.case_id AS case_id",
            case_id=case_id,
        ).data()
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="CaseContact",
            ontology_label_identifier=_HS_CASE_CONTACT_LABEL,
            key_property="case_id",
            key_value=case_id,
        )
        _session_run(session, 
            """
            MERGE (c:CaseContact {case_id: $case_id})
            SET c.email = $email,
                c.display_name = coalesce($display_name, c.display_name),
                c.status = coalesce(c.status, "open"),
                c.entity_status = coalesce(c.entity_status, "provisional"),
                c.interaction_id = $interaction_id,
                c.provisional_contact_id = $provisional_contact_id,
                c.promotion_reason = $promotion_reason,
                c.gate_results_json = $gate_results_json,
                c.updated_at = datetime($updated_at),
                c.created_at = coalesce(c.created_at, datetime($created_at))
            """,
            case_id=case_id,
            email=normalized_email,
            display_name=_normalize_text(display_name) or None,
            interaction_id=interaction_id,
            provisional_contact_id=provisional_contact_id,
            promotion_reason=promotion_reason,
            gate_results_json=_json_text(gate_results),
            updated_at=now_iso,
            created_at=now_iso,
        )
        if _HS_CASE_CONTACT_LABEL:
            _session_run(session, 
                f"""
                MATCH (c:CaseContact {{case_id: $case_id}})
                SET c:{_HS_CASE_CONTACT_LABEL}
                """,
                case_id=case_id,
            )
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="CRMContact",
            ontology_label_identifier=_HS_CONTACT_LABEL,
            key_property="external_id",
            key_value=provisional_contact_id,
        )
        _session_run(session, 
            """
            MATCH (c:CaseContact {case_id: $case_id})
            MERGE (pc:CRMContact {external_id: $provisional_contact_id})
            ON CREATE SET pc.primary_email = $email,
                          pc.display_name = $display_name,
                          pc.entity_status = "provisional",
                          pc.created_at = datetime($created_at)
            SET pc.primary_email = coalesce(pc.primary_email, $email),
                pc.display_name = coalesce($display_name, pc.display_name),
                pc.updated_at = datetime($updated_at)
            MERGE (c)-[:TARGETS_CONTACT]->(pc)
            """,
            case_id=case_id,
            provisional_contact_id=provisional_contact_id,
            email=normalized_email,
            display_name=_normalize_text(display_name) or None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        if _HS_CASE_CONTACT_LABEL and _HS_CONTACT_LABEL and _HS_TARGETS_CONTACT_REL:
            _session_run(session, 
                f"""
                MATCH (c:CaseContact {{case_id: $case_id}})-[:TARGETS_CONTACT]->(pc:CRMContact {{external_id: $provisional_contact_id}})
                SET c:{_HS_CASE_CONTACT_LABEL}
                SET pc:{_HS_CONTACT_LABEL}
                MERGE (c)-[r:{_HS_TARGETS_CONTACT_REL}]->(pc)
                SET r.ont_predicate = $targets_contact_ont_predicate
                """,
                case_id=case_id,
                provisional_contact_id=provisional_contact_id,
                targets_contact_ont_predicate=_ont_predicate_for_rel("TARGETS_CONTACT"),
            )
        _session_run(session, 
            """
            MATCH (eng:CRMEngagement {external_id: $interaction_id})
            MATCH (c:CaseContact {case_id: $case_id})
            MATCH (pc:CRMContact {external_id: $provisional_contact_id})
            MERGE (eng)-[:HAS_CASE_CONTACT]->(c)
            MERGE (eng)-[:ENGAGED_WITH]->(pc)
            """,
            interaction_id=interaction_id,
            case_id=case_id,
            provisional_contact_id=provisional_contact_id,
        )
        if _HS_CASE_CONTACT_LABEL and _HS_CONTACT_LABEL and _HS_HAS_CASE_CONTACT_REL and _HS_ENGAGED_WITH_REL and _HS_ENGAGEMENT_BASE_LABEL:
            _session_run(session, 
                f"""
                MATCH (eng:CRMEngagement {{external_id: $interaction_id}})
                MATCH (c:CaseContact {{case_id: $case_id}})
                MATCH (pc:CRMContact {{external_id: $provisional_contact_id}})
                SET eng:{_HS_ENGAGEMENT_BASE_LABEL}
                SET c:{_HS_CASE_CONTACT_LABEL}
                SET pc:{_HS_CONTACT_LABEL}
                MERGE (eng)-[r1:{_HS_HAS_CASE_CONTACT_REL}]->(c)
                SET r1.ont_predicate = $has_case_contact_ont_predicate
                MERGE (eng)-[r2:{_HS_ENGAGED_WITH_REL}]->(pc)
                SET r2.ont_predicate = $engaged_with_ont_predicate
                """,
                interaction_id=interaction_id,
                case_id=case_id,
                provisional_contact_id=provisional_contact_id,
                has_case_contact_ont_predicate=_ont_predicate_for_rel("HAS_CASE_CONTACT"),
                engaged_with_ont_predicate=_ont_predicate_for_rel("ENGAGED_WITH"),
            )
    return {"created": not bool(existing), "case_id": case_id, "provisional_contact_id": provisional_contact_id}


def list_case_contacts_v2(status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return []
        rows = _session_run(session, 
            f"""
            MATCH (c:{_HS_CASE_CONTACT_LABEL or 'CaseContact'})
            WHERE $status = "all" OR coalesce(c.status, "open") = $status
            OPTIONAL MATCH (c)-[:{_HS_TARGETS_CONTACT_REL or 'TARGETS_CONTACT'}]->(pc:{_HS_CONTACT_LABEL or 'CRMContact'})
            OPTIONAL MATCH (c)<-[:{_HS_HAS_CASE_CONTACT_REL or 'HAS_CASE_CONTACT'}]-(eng:{_HS_ENGAGEMENT_BASE_LABEL or 'CRMEngagement'})
                          <-[:{_HS_DERIVED_FROM_ENGAGEMENT_REL or 'HAS_ASSERTION'}]-(:{_HS_ASSERTION_LABEL or 'KGAssertion'})
                          -[:{_HS_SOURCE_ARTIFACT_REL or 'SUPPORTED_BY'}]->(ev:{_HS_SOURCE_ARTIFACT_LABEL or 'EvidenceChunk'})
            RETURN c.case_id AS case_id,
                   c.email AS email,
                   c.display_name AS display_name,
                   c.status AS status,
                   c.entity_status AS entity_status,
                   c.interaction_id AS interaction_id,
                   c.provisional_contact_id AS provisional_contact_id,
                   c.promotion_reason AS promotion_reason,
                   c.gate_results_json AS gate_results_json,
                   count(DISTINCT ev) AS evidence_count,
                   pc.external_id AS targeted_contact_id,
                   toString(c.created_at) AS created_at,
                   toString(c.updated_at) AS updated_at
            ORDER BY updated_at DESC
            LIMIT $limit
            """,
            status=status,
            limit=max(1, limit),
        ).data()
    results: list[dict[str, Any]] = []
    for row in rows:
        gate_results_json = row.get("gate_results_json")
        try:
            gate_results = json.loads(gate_results_json) if isinstance(gate_results_json, str) else {}
        except Exception:
            gate_results = {}
        results.append(
            {
                "case_id": row.get("case_id"),
                "email": row.get("email"),
                "display_name": row.get("display_name"),
                "status": row.get("status") or "open",
                "entity_status": row.get("entity_status") or "provisional",
                "interaction_id": row.get("interaction_id"),
                "provisional_contact_id": row.get("provisional_contact_id") or row.get("targeted_contact_id"),
                "promotion_reason": row.get("promotion_reason"),
                "gate_results": gate_results,
                "evidence_count": int(row.get("evidence_count") or 0),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    return results


def promote_case_contact_v2(
    case_id: str,
    *,
    canonical_contact_id: str | None = None,
    promotion_reason: str,
    gate_results: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    now_iso = _now_iso()
    settings = get_settings()
    with neo4j_session() as session:
        if session is None:
            return None
        rows = _session_run(session, 
            f"""
            MATCH (cc:{_HS_CASE_CONTACT_LABEL or 'CaseContact'} {{case_id: $case_id}})
            OPTIONAL MATCH (cc)<-[:{_HS_HAS_CASE_CONTACT_REL or 'HAS_CASE_CONTACT'}]-(eng:{_HS_ENGAGEMENT_BASE_LABEL or 'CRMEngagement'})
                          <-[:{_HS_DERIVED_FROM_ENGAGEMENT_REL or 'HAS_ASSERTION'}]-(a:{_HS_ASSERTION_LABEL or 'KGAssertion'})
            OPTIONAL MATCH (a)-[:{_HS_SOURCE_ARTIFACT_REL or 'SUPPORTED_BY'}]->(ev:{_HS_SOURCE_ARTIFACT_LABEL or 'EvidenceChunk'})
            RETURN cc.email AS email,
                   cc.display_name AS display_name,
                   cc.provisional_contact_id AS provisional_contact_id,
                   count(DISTINCT ev) AS evidence_count,
                   count(DISTINCT a) AS assertion_count,
                   sum(CASE WHEN toLower(coalesce(a.status, "proposed")) = "rejected" THEN 1 ELSE 0 END) AS rejected_assertion_count,
                   toString(max(coalesce(a.updated_at, datetime('1970-01-01T00:00:00Z')))) AS latest_assertion_at
            LIMIT 1
            """,
            case_id=case_id,
        ).data()
        if not rows:
            return None
        email = _normalize_text(rows[0].get("email")).lower()
        display_name = _normalize_text(rows[0].get("display_name")) or None
        provisional_contact_id = _normalize_text(rows[0].get("provisional_contact_id"))
        evidence_count = int(rows[0].get("evidence_count") or 0)
        assertion_count = int(rows[0].get("assertion_count") or 0)
        rejected_assertion_count = int(rows[0].get("rejected_assertion_count") or 0)
        latest_assertion_at = _normalize_text(rows[0].get("latest_assertion_at")) or None

        age_days = 99999
        if latest_assertion_at:
            try:
                latest_dt = datetime.fromisoformat(latest_assertion_at.replace("Z", "+00:00"))
                age_days = max(0, (datetime.now(timezone.utc) - latest_dt.astimezone(timezone.utc)).days)
            except Exception:
                age_days = 99999

        base_gate_results: dict[str, Any] = {
            "min_evidence_required": int(settings.graph_v2_case_contact_promotion_min_evidence),
            "max_age_days": int(settings.graph_v2_case_contact_promotion_max_age_days),
            "evidence_count": evidence_count,
            "assertion_count": assertion_count,
            "rejected_assertion_count": rejected_assertion_count,
            "latest_assertion_age_days": age_days,
        }
        gate_payload = dict(base_gate_results)
        if isinstance(gate_results, dict):
            gate_payload.update(gate_results)
        override_gate = bool(gate_payload.get("override", False))
        gate_passed = override_gate or (
            evidence_count >= settings.graph_v2_case_contact_promotion_min_evidence
            and age_days <= settings.graph_v2_case_contact_promotion_max_age_days
            and rejected_assertion_count == 0
            and assertion_count > 0
        )

        if not gate_passed:
            gate_payload["passed"] = False
            gate_payload["blocked_reason"] = "promotion_gate_failed"
            _session_run(session, 
                """
                MATCH (cc:CaseContact {case_id: $case_id})
                SET cc.status = coalesce(cc.status, "open"),
                    cc.entity_status = "provisional",
                    cc.promotion_reason = $promotion_reason,
                    cc.gate_results_json = $gate_results_json,
                    cc.updated_at = datetime($updated_at)
                """,
                case_id=case_id,
                promotion_reason=promotion_reason,
                gate_results_json=_json_text(gate_payload),
                updated_at=now_iso,
            )
            return {
                "case_id": case_id,
                "status": "open",
                "entity_status": "provisional",
                "promoted_id": None,
                "email": email,
                "display_name": display_name,
                "provisional_contact_id": provisional_contact_id or None,
                "gate_results": gate_payload,
            }

        promoted_id = canonical_contact_id or f"contact:auto:{uuid.uuid5(uuid.NAMESPACE_URL, email)}"
        gate_payload["passed"] = True
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="CRMContact",
            ontology_label_identifier=_HS_CONTACT_LABEL,
            key_property="external_id",
            key_value=promoted_id,
        )
        _session_run(session, 
            """
            MATCH (cc:CaseContact {case_id: $case_id})
            MERGE (c:CRMContact {external_id: $promoted_id})
            SET c.primary_email = $email,
                c.display_name = coalesce($display_name, c.display_name),
                c.entity_status = "canonical",
                c.updated_at = datetime($updated_at),
                c.created_at = coalesce(c.created_at, datetime($created_at))
            WITH cc, c
            OPTIONAL MATCH (pc:CRMContact {external_id: $provisional_contact_id})
            FOREACH (_ IN CASE WHEN pc IS NULL THEN [] ELSE [1] END |
                SET pc.entity_status = "superseded",
                    pc.updated_at = datetime($updated_at)
            )
            FOREACH (_ IN CASE WHEN pc IS NULL THEN [] ELSE [1] END |
                MERGE (pc)-[:PROMOTED_TO]->(c)
            )
            WITH cc, c, pc
            OPTIONAL MATCH (eng:CRMEngagement)-[:ENGAGED_WITH]->(pc)
            FOREACH (_ IN CASE WHEN eng IS NULL THEN [] ELSE [1] END |
                MERGE (eng)-[:ENGAGED_WITH]->(c)
            )
            WITH cc, c, pc
            OPTIONAL MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(pc)
            FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END |
                MERGE (a)-[:ASSERTS_ABOUT_CONTACT]->(c)
            )
            WITH cc, c, pc
            OPTIONAL MATCH (opp:CRMOpportunity)-[:INVOLVES_CONTACT]->(pc)
            FOREACH (_ IN CASE WHEN opp IS NULL THEN [] ELSE [1] END |
                MERGE (opp)-[:INVOLVES_CONTACT]->(c)
            )
            WITH cc, c
            SET cc.status = "promoted",
                cc.entity_status = "canonical",
                cc.promoted_contact_id = $promoted_id,
                cc.promotion_reason = $promotion_reason,
                cc.gate_results_json = $gate_results_json,
                cc.updated_at = datetime($updated_at)
            MERGE (cc)-[:PROMOTED_TO]->(c)
            """,
            case_id=case_id,
            promoted_id=promoted_id,
            email=email,
            display_name=display_name,
            provisional_contact_id=provisional_contact_id or None,
            updated_at=now_iso,
            created_at=now_iso,
            promotion_reason=promotion_reason,
            gate_results_json=_json_text(gate_payload),
        )
        if _HS_CASE_CONTACT_LABEL and _HS_CONTACT_LABEL and _HS_PROMOTED_TO_REL:
            _session_run(session, 
                f"""
                MATCH (cc:CaseContact {{case_id: $case_id}})
                MATCH (c:CRMContact {{external_id: $promoted_id}})
                SET cc:{_HS_CASE_CONTACT_LABEL}
                SET c:{_HS_CONTACT_LABEL}
                MERGE (cc)-[r:{_HS_PROMOTED_TO_REL}]->(c)
                SET r.ont_predicate = $promoted_to_ont_predicate
                """,
                case_id=case_id,
                promoted_id=promoted_id,
                promoted_to_ont_predicate=_ont_predicate_for_rel("PROMOTED_TO"),
            )
        if _HS_ENGAGED_WITH_REL and _HS_CONTACT_LABEL and _HS_ENGAGEMENT_BASE_LABEL:
            _session_run(session, 
                f"""
                MATCH (c:CRMContact {{external_id: $promoted_id}})
                SET c:{_HS_CONTACT_LABEL}
                WITH c
                OPTIONAL MATCH (eng:CRMEngagement)-[:ENGAGED_WITH]->(c)
                FOREACH (_ IN CASE WHEN eng IS NULL THEN [] ELSE [1] END |
                    SET eng:{_HS_ENGAGEMENT_BASE_LABEL}
                    MERGE (eng)-[r:{_HS_ENGAGED_WITH_REL}]->(c)
                    SET r.ont_predicate = $engaged_with_ont_predicate
                )
                """,
                promoted_id=promoted_id,
                engaged_with_ont_predicate=_ont_predicate_for_rel("ENGAGED_WITH"),
            )
    return {
        "case_id": case_id,
        "status": "promoted",
        "entity_status": "canonical",
        "promoted_id": promoted_id,
        "email": email,
        "display_name": display_name,
        "provisional_contact_id": provisional_contact_id or None,
        "gate_results": gate_payload,
    }


def upsert_case_opportunity_v2(
    *,
    interaction_id: str,
    title: str,
    company_name: str | None,
    thread_id: str | None,
    promotion_reason: str = "auto_from_interaction",
    gate_results: dict[str, Any] | None = None,
    motivators: list[str] | None = None,
    contact_ids: list[str] | None = None,
) -> dict[str, Any]:
    normalized_title = _normalize_text(title) or "Untitled Opportunity"
    key_seed = _normalize_text(thread_id) or f"{interaction_id}:{normalized_title}:{_normalize_text(company_name)}"
    case_id = f"case_opp:{uuid.uuid5(uuid.NAMESPACE_URL, key_seed)}"
    now_iso = _now_iso()
    motivator_values = [m for m in (motivators or []) if isinstance(m, str) and m.strip()]
    participant_contact_ids = [item.strip() for item in (contact_ids or []) if isinstance(item, str) and item.strip()]
    with neo4j_session() as session:
        if session is None:
            return {"created": False, "case_id": case_id}
        existing = _session_run(session, 
            "MATCH (c:CaseOpportunity {case_id: $case_id}) RETURN c.case_id AS case_id",
            case_id=case_id,
        ).data()
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="CaseOpportunity",
            ontology_label_identifier=_HS_CASE_OPPORTUNITY_LABEL,
            key_property="case_id",
            key_value=case_id,
        )
        _session_run(session, 
            """
            MERGE (c:CaseOpportunity {case_id: $case_id})
            SET c.title = $title,
                c.company_name = coalesce($company_name, c.company_name),
                c.thread_id = coalesce($thread_id, c.thread_id),
                c.status = coalesce(c.status, "open"),
                c.entity_status = coalesce(c.entity_status, "provisional"),
                c.interaction_id = $interaction_id,
                c.promotion_reason = $promotion_reason,
                c.gate_results_json = $gate_results_json,
                c.motivators = $motivators,
                c.contact_ids = $contact_ids,
                c.updated_at = datetime($updated_at),
                c.created_at = coalesce(c.created_at, datetime($created_at))
            """,
            case_id=case_id,
            title=normalized_title,
            company_name=_normalize_text(company_name) or None,
            thread_id=_normalize_text(thread_id) or None,
            interaction_id=interaction_id,
            promotion_reason=promotion_reason,
            gate_results_json=_json_text(gate_results),
            motivators=motivator_values,
            contact_ids=participant_contact_ids,
            updated_at=now_iso,
            created_at=now_iso,
        )
        if _HS_CASE_OPPORTUNITY_LABEL:
            _session_run(session, 
                f"""
                MATCH (c:CaseOpportunity {{case_id: $case_id}})
                SET c:{_HS_CASE_OPPORTUNITY_LABEL}
                """,
                case_id=case_id,
            )
        _session_run(session, 
            """
            MATCH (eng:CRMEngagement {external_id: $interaction_id})
            MATCH (c:CaseOpportunity {case_id: $case_id})
            MERGE (eng)-[:HAS_CASE_OPPORTUNITY]->(c)
            """,
            interaction_id=interaction_id,
            case_id=case_id,
        )
        if _HS_HAS_CASE_OPPORTUNITY_REL and _HS_CASE_OPPORTUNITY_LABEL and _HS_ENGAGEMENT_BASE_LABEL:
            _session_run(session, 
                f"""
                MATCH (eng:CRMEngagement {{external_id: $interaction_id}})
                MATCH (c:CaseOpportunity {{case_id: $case_id}})
                SET eng:{_HS_ENGAGEMENT_BASE_LABEL}
                SET c:{_HS_CASE_OPPORTUNITY_LABEL}
                MERGE (eng)-[r:{_HS_HAS_CASE_OPPORTUNITY_REL}]->(c)
                SET r.ont_predicate = $has_case_opportunity_ont_predicate
                """,
                interaction_id=interaction_id,
                case_id=case_id,
                has_case_opportunity_ont_predicate=_ont_predicate_for_rel("HAS_CASE_OPPORTUNITY"),
            )
        if participant_contact_ids:
            _session_run(session, 
                """
                MATCH (c:CaseOpportunity {case_id: $case_id})
                UNWIND $contact_ids AS cid
                MERGE (ct:CRMContact {external_id: cid})
                ON CREATE SET ct.entity_status = CASE WHEN cid STARTS WITH "contact:provisional:" THEN "provisional" ELSE "canonical" END,
                              ct.created_at = datetime($created_at)
                SET ct.updated_at = datetime($updated_at)
                MERGE (c)-[:INVOLVES_CONTACT]->(ct)
                """,
                case_id=case_id,
                contact_ids=participant_contact_ids,
                created_at=now_iso,
                updated_at=now_iso,
            )
            if _HS_CASE_OPPORTUNITY_LABEL and _HS_CONTACT_LABEL and _HS_INVOLVES_CONTACT_REL:
                _session_run(session, 
                    f"""
                    MATCH (c:CaseOpportunity {{case_id: $case_id}})
                    UNWIND $contact_ids AS cid
                    MATCH (ct:CRMContact {{external_id: cid}})
                    SET c:{_HS_CASE_OPPORTUNITY_LABEL}
                    SET ct:{_HS_CONTACT_LABEL}
                    MERGE (c)-[r:{_HS_INVOLVES_CONTACT_REL}]->(ct)
                    SET r.ont_predicate = $involves_contact_ont_predicate
                    """,
                    case_id=case_id,
                    contact_ids=participant_contact_ids,
                    involves_contact_ont_predicate=_ont_predicate_for_rel("INVOLVES_CONTACT"),
                )
    return {"created": not bool(existing), "case_id": case_id}


def list_case_opportunities_v2(status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return []
        rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (c:CaseOpportunity)
            WHERE $status = "all" OR coalesce(c.status, "open") = $status
            OPTIONAL MATCH (c)-[:INVOLVES_CONTACT]->(ct:CRMContact)
            RETURN c.case_id AS case_id,
                   c.title AS title,
                   c.company_name AS company_name,
                   c.thread_id AS thread_id,
                   c.status AS status,
                   c.entity_status AS entity_status,
                   c.interaction_id AS interaction_id,
                   c.promotion_reason AS promotion_reason,
                   c.gate_results_json AS gate_results_json,
                   c.motivators AS motivators,
                   collect(DISTINCT ct.external_id) AS contact_ids,
                   toString(c.created_at) AS created_at,
                   toString(c.updated_at) AS updated_at
            ORDER BY updated_at DESC
            LIMIT $limit
            """
            ),
            status=status,
            limit=max(1, limit),
        ).data()
    results: list[dict[str, Any]] = []
    for row in rows:
        gate_results_json = row.get("gate_results_json")
        try:
            gate_results = json.loads(gate_results_json) if isinstance(gate_results_json, str) else {}
        except Exception:
            gate_results = {}
        results.append(
            {
                "case_id": row.get("case_id"),
                "title": row.get("title") or "Untitled Opportunity",
                "company_name": row.get("company_name"),
                "thread_id": row.get("thread_id"),
                "status": row.get("status") or "open",
                "entity_status": row.get("entity_status") or "provisional",
                "interaction_id": row.get("interaction_id"),
                "promotion_reason": row.get("promotion_reason"),
                "gate_results": gate_results,
                "motivators": [m for m in (row.get("motivators") or []) if isinstance(m, str)],
                "contact_ids": [cid for cid in (row.get("contact_ids") or []) if isinstance(cid, str)],
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
        )
    return results


def promote_case_opportunity_v2(
    case_id: str,
    *,
    promotion_reason: str,
    gate_results: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    now_iso = _now_iso()
    with neo4j_session() as session:
        if session is None:
            return None
        rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (co:CaseOpportunity {case_id: $case_id})
            OPTIONAL MATCH (co)-[:INVOLVES_CONTACT]->(ct:CRMContact)
            RETURN co.title AS title,
                   co.company_name AS company_name,
                   co.thread_id AS thread_id,
                   collect(DISTINCT ct.external_id) AS contact_ids
            LIMIT 1
            """
            ),
            case_id=case_id,
        ).data()
        if not rows:
            return None
        title = _normalize_text(rows[0].get("title")) or "Untitled Opportunity"
        company_name = _normalize_text(rows[0].get("company_name")) or None
        thread_id = _normalize_text(rows[0].get("thread_id")) or None
        contact_ids = [cid for cid in (rows[0].get("contact_ids") or []) if isinstance(cid, str) and cid.strip()]
        promoted_id = f"opp:auto:{uuid.uuid5(uuid.NAMESPACE_URL, f'{case_id}:{title}')}"
        _adopt_ontology_node_into_v2_label(
            session,
            v2_label="CRMOpportunity",
            ontology_label_identifier=_HS_DEAL_LABEL,
            key_property="external_id",
            key_value=promoted_id,
        )
        _session_run(session, 
            """
            MATCH (caseOpp:CaseOpportunity {case_id: $case_id})
            MERGE (opp:CRMOpportunity {external_id: $promoted_id})
            SET opp.title = $title,
                opp.status = "open",
                opp.entity_status = "canonical",
                opp.last_thread_id = coalesce($thread_id, opp.last_thread_id),
                opp.updated_at = datetime($updated_at),
                opp.created_at = coalesce(opp.created_at, datetime($created_at))
            SET caseOpp.status = "promoted",
                caseOpp.entity_status = "canonical",
                caseOpp.promoted_opportunity_id = $promoted_id,
                caseOpp.promotion_reason = $promotion_reason,
                caseOpp.gate_results_json = $gate_results_json,
                caseOpp.updated_at = datetime($updated_at)
            MERGE (caseOpp)-[:PROMOTED_TO]->(opp)
            """,
            case_id=case_id,
            promoted_id=promoted_id,
            title=title,
            thread_id=thread_id,
            updated_at=now_iso,
            created_at=now_iso,
            promotion_reason=promotion_reason,
                gate_results_json=_json_text(gate_results),
            )
        if _HS_CASE_OPPORTUNITY_LABEL and _HS_DEAL_LABEL and _HS_PROMOTED_TO_REL:
            _session_run(session, 
                f"""
                MATCH (caseOpp:CaseOpportunity {{case_id: $case_id}})
                MATCH (opp:CRMOpportunity {{external_id: $promoted_id}})
                SET caseOpp:{_HS_CASE_OPPORTUNITY_LABEL}
                SET opp:{_HS_DEAL_LABEL}
                MERGE (caseOpp)-[r:{_HS_PROMOTED_TO_REL}]->(opp)
                SET r.ont_predicate = $promoted_to_ont_predicate
                """,
                case_id=case_id,
                promoted_id=promoted_id,
                promoted_to_ont_predicate=_ont_predicate_for_rel("PROMOTED_TO"),
            )
        if contact_ids:
            _session_run(session, 
                """
                MATCH (opp:CRMOpportunity {external_id: $promoted_id})
                UNWIND $contact_ids AS cid
                MERGE (c:CRMContact {external_id: cid})
                ON CREATE SET c.entity_status = CASE WHEN cid STARTS WITH "contact:provisional:" THEN "provisional" ELSE "canonical" END,
                              c.created_at = datetime($created_at)
                SET c.updated_at = datetime($updated_at),
                    c.entity_status = CASE
                        WHEN c.entity_status = "canonical" THEN "canonical"
                        WHEN cid STARTS WITH "contact:provisional:" THEN "provisional"
                        ELSE "canonical"
                    END
                MERGE (opp)-[:INVOLVES_CONTACT]->(c)
                """,
                promoted_id=promoted_id,
                contact_ids=contact_ids,
                created_at=now_iso,
                updated_at=now_iso,
            )
            if _HS_DEAL_LABEL and _HS_CONTACT_LABEL and _HS_INVOLVES_CONTACT_REL:
                _session_run(session, 
                    f"""
                    MATCH (opp:CRMOpportunity {{external_id: $promoted_id}})
                    UNWIND $contact_ids AS cid
                    MATCH (c:CRMContact {{external_id: cid}})
                    SET opp:{_HS_DEAL_LABEL}
                    SET c:{_HS_CONTACT_LABEL}
                    MERGE (opp)-[r:{_HS_INVOLVES_CONTACT_REL}]->(c)
                    SET r.ont_predicate = $involves_contact_ont_predicate
                    """,
                    promoted_id=promoted_id,
                    contact_ids=contact_ids,
                    involves_contact_ont_predicate=_ont_predicate_for_rel("INVOLVES_CONTACT"),
                )
        if company_name:
            company_external_id = f"company:auto:{_normalize_key(company_name)}"
            _session_run(session, 
                """
                MATCH (opp:CRMOpportunity {external_id: $promoted_id})
                MERGE (co:CRMCompany {external_id: $company_external_id})
                ON CREATE SET co.name = $company_name,
                              co.created_at = datetime($created_at),
                              co.entity_status = "provisional"
                SET co.updated_at = datetime($updated_at)
                MERGE (opp)-[:OPPORTUNITY_FOR_COMPANY]->(co)
                """,
                promoted_id=promoted_id,
                company_external_id=company_external_id,
                company_name=company_name,
                created_at=now_iso,
                updated_at=now_iso,
            )
            if _HS_DEAL_LABEL and _HS_COMPANY_LABEL and _HS_OPPORTUNITY_FOR_COMPANY_REL:
                _session_run(session, 
                    f"""
                    MATCH (opp:CRMOpportunity {{external_id: $promoted_id}})
                    MATCH (co:CRMCompany {{external_id: $company_external_id}})
                    SET opp:{_HS_DEAL_LABEL}
                    SET co:{_HS_COMPANY_LABEL}
                    MERGE (opp)-[r:{_HS_OPPORTUNITY_FOR_COMPANY_REL}]->(co)
                    SET r.ont_predicate = $opportunity_for_company_ont_predicate
                    """,
                    promoted_id=promoted_id,
                    company_external_id=company_external_id,
                    opportunity_for_company_ont_predicate=_ont_predicate_for_rel("OPPORTUNITY_FOR_COMPANY"),
                )
    return {
        "case_id": case_id,
        "status": "promoted",
        "entity_status": "canonical",
        "promoted_id": promoted_id,
    }


def list_open_opportunities_v2(limit: int = 100) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return []
        rows = _session_run(
            session,
            _ontify_v2_read_cypher(
                """
            MATCH (opp:CRMOpportunity)
            WHERE coalesce(opp.status, "open") = "open"
            OPTIONAL MATCH (opp)-[:OPPORTUNITY_FOR_COMPANY|OPPORTUNITY_FOR_COMPANY_DERIVED]->(co:CRMCompany)
            OPTIONAL MATCH (opp)-[:INVOLVES_CONTACT]->(ct:CRMContact)
            OPTIONAL MATCH (eng:CRMEngagement)-[:ENGAGED_OPPORTUNITY|ENGAGED_OPPORTUNITY_DERIVED]->(opp)
            RETURN opp.external_id AS opportunity_id,
                   opp.title AS title,
                   co.name AS company_name,
                   coalesce(opp.status, "open") AS status,
                   coalesce(opp.entity_status, "canonical") AS entity_status,
                   opp.last_thread_id AS thread_id,
                   collect(DISTINCT ct.external_id) AS contact_ids,
                   toString(max(eng.occurred_at)) AS last_engagement_at,
                   toString(opp.updated_at) AS updated_at,
                   toString(opp.created_at) AS created_at
            ORDER BY coalesce(updated_at, '1970-01-01T00:00:00Z') DESC, opportunity_id ASC
            LIMIT $limit
            """
            ),
            limit=max(1, limit),
        ).data()
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "opportunity_id": row.get("opportunity_id"),
                "title": row.get("title") or "Untitled Opportunity",
                "company_name": row.get("company_name"),
                "status": row.get("status") or "open",
                "entity_status": row.get("entity_status") or "canonical",
                "thread_id": row.get("thread_id"),
                "contact_ids": [cid for cid in (row.get("contact_ids") or []) if isinstance(cid, str)],
                "last_engagement_at": row.get("last_engagement_at"),
                "updated_at": row.get("updated_at"),
                "created_at": row.get("created_at"),
            }
        )
    return results


def find_best_opportunity_for_interaction_v2(
    *,
    thread_id: str | None,
    company_name: str | None,
    contact_ids: list[str],
    subject_hint: str | None = None,
    body_hint: str | None = None,
    limit: int = 30,
) -> dict[str, Any] | None:
    cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    with neo4j_session() as session:
        if session is None:
            return None
        rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (opp:CRMOpportunity)
            WHERE coalesce(opp.status, "open") = "open"
            OPTIONAL MATCH (opp)-[:OPPORTUNITY_FOR_COMPANY]->(co:CRMCompany)
            OPTIONAL MATCH (opp)-[:INVOLVES_CONTACT]->(ct:CRMContact)
            OPTIONAL MATCH (eng:CRMEngagement)-[:ENGAGED_OPPORTUNITY|ENGAGED_OPPORTUNITY_DERIVED]->(opp)
                RETURN opp.external_id AS opportunity_id,
                       opp.title AS title,
                       opp.last_thread_id AS last_thread_id,
                       opp.updated_at AS updated_at,
                       max(eng.occurred_at) AS last_engagement_at,
                       count(DISTINCT CASE WHEN eng.occurred_at >= datetime($cutoff_30d) THEN eng END) AS recent_engagement_count_30d,
                       count(DISTINCT CASE WHEN eng.occurred_at >= datetime($cutoff_30d) AND toLower(coalesce(eng.direction, "")) = "in" THEN eng END) AS recent_inbound_count_30d,
                       opp.stage AS stage,
                       opp.status AS status,
                       co.name AS company_name,
                       collect(DISTINCT ct.external_id) AS contact_ids
                LIMIT $limit
            """
            ),
            limit=max(1, limit),
            cutoff_30d=cutoff_30d,
        ).data()
    company_norm = _normalize_key(company_name)
    thread_norm = _normalize_text(thread_id)
    contact_set = {item for item in contact_ids if isinstance(item, str) and item}
    hint_tokens = {
        tok
        for tok in re.findall(r"[a-zA-Z0-9]{3,}", " ".join(part for part in [subject_hint or "", body_hint or ""]).lower())
        if tok
    }

    def _parse_dt(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    def _recency_match_points(updated_at: Any) -> float:
        dt = _parse_dt(updated_at)
        if dt is None:
            return 0.0
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        if age_days <= 14:
            return 0.10
        if age_days <= 30:
            return 0.07
        if age_days <= 90:
            return 0.04
        if age_days > 180:
            return -0.05
        return 0.0

    def _lexical_match_points(row: dict[str, Any]) -> float:
        if not hint_tokens:
            return 0.0
        row_tokens = {
            tok
            for tok in re.findall(
                r"[a-zA-Z0-9]{3,}",
                " ".join(
                    part for part in [str(row.get("title") or ""), str(row.get("company_name") or ""), str(row.get("stage") or "")]
                ).lower(),
            )
            if tok
        }
        if not row_tokens:
            return 0.0
        overlap = len(hint_tokens & row_tokens)
        if overlap <= 0:
            return 0.0
        return min(0.15, (overlap / max(1, len(hint_tokens))) * 0.15)

    def _stage_compatibility_points(row: dict[str, Any]) -> float:
        stage = _normalize_text(row.get("stage")).lower()
        if not stage:
            return 0.0
        if any(term in stage for term in ("closed", "won", "lost")):
            return -0.08
        if any(term in stage for term in ("discovery", "proposal", "pilot", "evaluation", "negotiation")):
            return 0.03
        return 0.0

    def _activity_pressure_points(row: dict[str, Any]) -> float:
        recent_engagement_count = int(row.get("recent_engagement_count_30d") or 0)
        recent_inbound_count = int(row.get("recent_inbound_count_30d") or 0)
        if recent_engagement_count <= 0:
            return 0.0
        activity_points = min(0.08, recent_engagement_count * 0.025)
        outbound_count = max(0, recent_engagement_count - recent_inbound_count)
        open_loop_proxy = max(0, recent_inbound_count - outbound_count)
        open_loop_points = min(0.06, open_loop_proxy * 0.03)
        return round(activity_points + open_loop_points, 4)

    best: dict[str, Any] | None = None
    best_score = 0.0
    for row in rows:
        score = 0.0
        score_components = {
            "thread_match": 0.0,
            "company_match": 0.0,
            "contact_overlap": 0.0,
            "recency": 0.0,
            "lexical_similarity": 0.0,
            "stage_compatibility": 0.0,
            "activity_pressure": 0.0,
        }
        if thread_norm and _normalize_text(row.get("last_thread_id")) == thread_norm:
            score += 0.45
            score_components["thread_match"] = 0.45
        if company_norm and _normalize_key(row.get("company_name")) == company_norm:
            score += 0.35
            score_components["company_match"] = 0.35
        candidate_contacts = {item for item in (row.get("contact_ids") or []) if isinstance(item, str) and item}
        if contact_set and candidate_contacts:
            overlap = len(contact_set & candidate_contacts)
            overlap_points = min(0.20, overlap * 0.10)
            score += overlap_points
            score_components["contact_overlap"] = overlap_points
        recency_points = _recency_match_points(row.get("updated_at"))
        score += recency_points
        score_components["recency"] = round(recency_points, 4)
        lexical_points = _lexical_match_points(row)
        score += lexical_points
        score_components["lexical_similarity"] = round(lexical_points, 4)
        stage_points = _stage_compatibility_points(row)
        score += stage_points
        score_components["stage_compatibility"] = round(stage_points, 4)
        activity_points = _activity_pressure_points(row)
        score += activity_points
        score_components["activity_pressure"] = round(activity_points, 4)

        if score > best_score or (
            best is not None and abs(score - best_score) < 1e-9 and str(row.get("updated_at") or "") > str(best.get("updated_at") or "")
        ):
            reason_chain: list[dict[str, Any]] = []
            if score_components["thread_match"] > 0:
                reason_chain.append({"kind": "thread_match", "thread_id": row.get("last_thread_id"), "weight": 0.45})
            if score_components["company_match"] > 0:
                reason_chain.append({"kind": "company_match", "company_name": row.get("company_name"), "weight": 0.35})
            if score_components["contact_overlap"] > 0:
                reason_chain.append(
                    {
                        "kind": "contact_overlap",
                        "overlap_count": len(contact_set & candidate_contacts),
                        "weight": score_components["contact_overlap"],
                    }
                )
            if score_components["recency"] != 0:
                reason_chain.append(
                    {
                        "kind": "recency",
                        "updated_at": row.get("updated_at"),
                        "weight": score_components["recency"],
                    }
                )
            if score_components["lexical_similarity"] > 0:
                reason_chain.append(
                    {
                        "kind": "lexical_similarity",
                        "weight": score_components["lexical_similarity"],
                        "subject_hint": subject_hint,
                    }
                )
            if score_components["stage_compatibility"] != 0:
                reason_chain.append(
                    {
                        "kind": "stage_compatibility",
                        "stage": row.get("stage"),
                        "weight": score_components["stage_compatibility"],
                    }
                )
            if score_components["activity_pressure"] > 0:
                reason_chain.append(
                    {
                        "kind": "activity_pressure",
                        "recent_engagement_count_30d": int(row.get("recent_engagement_count_30d") or 0),
                        "recent_inbound_count_30d": int(row.get("recent_inbound_count_30d") or 0),
                        "last_engagement_at": row.get("last_engagement_at"),
                        "weight": score_components["activity_pressure"],
                        "note": "Includes inbound-heavy open-loop proxy weighting.",
                    }
                )
            best_score = score
            best = {
                "opportunity_id": row.get("opportunity_id"),
                "title": row.get("title"),
                "score": round(score, 4),
                "company_name": row.get("company_name"),
                "updated_at": row.get("updated_at"),
                "last_engagement_at": row.get("last_engagement_at"),
                "status": row.get("status"),
                "stage": row.get("stage"),
                "recent_engagement_count_30d": int(row.get("recent_engagement_count_30d") or 0),
                "recent_inbound_count_30d": int(row.get("recent_inbound_count_30d") or 0),
                "score_components": score_components,
                "reason_chain": reason_chain,
            }
    if best is None:
        return None
    best["meets_threshold"] = best_score >= float(get_settings().graph_v2_case_opportunity_threshold)
    return best


def link_engagement_to_opportunity_v2(
    *,
    interaction_id: str,
    opportunity_id: str,
    source: str,
    score: float,
) -> None:
    with neo4j_session() as session:
        if session is None:
            return
        _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (eng:CRMEngagement {external_id: $interaction_id})
            MATCH (opp:CRMOpportunity {external_id: $opportunity_id})
            MERGE (eng)-[r:ENGAGED_OPPORTUNITY]->(opp)
            SET r.source = $source,
                r.score = $score,
                r.updated_at = datetime($updated_at)
            """
            ),
            interaction_id=interaction_id,
            opportunity_id=opportunity_id,
            source=source,
            score=score,
            updated_at=_now_iso(),
        )


def link_opportunity_contacts_v2(opportunity_id: str, contact_ids: list[str]) -> None:
    normalized_ids = [item for item in contact_ids if isinstance(item, str) and item.strip()]
    if not normalized_ids:
        return
    with neo4j_session() as session:
        if session is None:
            return
        _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (opp:CRMOpportunity {external_id: $opportunity_id})
            UNWIND $contact_ids AS cid
            MERGE (c:CRMContact {external_id: cid})
            ON CREATE SET c.entity_status = CASE WHEN cid STARTS WITH "contact:provisional:" THEN "provisional" ELSE "canonical" END,
                          c.created_at = datetime($created_at)
            SET c.updated_at = datetime($updated_at),
                c.entity_status = CASE
                    WHEN c.entity_status = "canonical" THEN "canonical"
                    WHEN cid STARTS WITH "contact:provisional:" THEN "provisional"
                    ELSE "canonical"
                END
            MERGE (opp)-[r:INVOLVES_CONTACT]->(c)
            SET r.updated_at = datetime($updated_at)
            """
            ),
            opportunity_id=opportunity_id,
            contact_ids=normalized_ids,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )


def run_inference_rules_v2(
    *,
    min_confidence: float = 0.85,
    max_age_days: int = 180,
) -> dict[str, Any]:
    with neo4j_session() as session:
        if session is None:
            return {"enabled": False, "rules": {}}

        summary: dict[str, Any] = {"enabled": True, "rules": {}}

        # R1: Infer engaged company from engaged contact + works at.
        _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (:CRMEngagement)-[r:ENGAGED_COMPANY_DERIVED {ruleId: "R1"}]->(:CRMCompany)
            DELETE r
            """
            )
        )
        r1 = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (eng:CRMEngagement)-[:ENGAGED_WITH]->(p:CRMContact)-[:WORKS_AT]->(co:CRMCompany)
            WHERE NOT (eng)-[:ENGAGED_COMPANY]->(co)
            MERGE (eng)-[r:ENGAGED_COMPANY_DERIVED]->(co)
            SET r.derived = true,
                r.ruleId = "R1",
                r.evidence = "canonical",
                r.derivedAt = datetime($derived_at)
            """
            ),
            derived_at=_now_iso(),
        ).consume()
        summary["rules"]["R1"] = {"relationships_created": r1.counters.relationships_created}

        # R2: Infer candidate opportunity-company mapping when missing.
        _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (:CRMOpportunity)-[r:OPPORTUNITY_FOR_COMPANY_DERIVED {ruleId: "R2"}]->(:CRMCompany)
            DELETE r
            """
            )
        )
        r2 = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (opp:CRMOpportunity)-[:INVOLVES_CONTACT]->(:CRMContact)-[:WORKS_AT]->(co:CRMCompany)
            WHERE NOT (opp)-[:OPPORTUNITY_FOR_COMPANY]->(:CRMCompany)
            MERGE (opp)-[r:OPPORTUNITY_FOR_COMPANY_DERIVED]->(co)
            SET r.derived = true,
                r.ruleId = "R2",
                r.candidate = true,
                r.evidence = "canonical",
                r.derivedAt = datetime($derived_at)
            """
            ),
            derived_at=_now_iso(),
        ).consume()
        summary["rules"]["R2"] = {"relationships_created": r2.counters.relationships_created}

        # R3: Infer engagement-opportunity from participant overlap.
        _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (:CRMEngagement)-[r:ENGAGED_OPPORTUNITY_DERIVED {ruleId: "R3"}]->(:CRMOpportunity)
            DELETE r
            """
            )
        )
        r3 = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (eng:CRMEngagement)-[:ENGAGED_WITH]->(p:CRMContact)<-[:INVOLVES_CONTACT]-(opp:CRMOpportunity)
            WHERE coalesce(opp.status, "open") = "open"
              AND NOT (eng)-[:ENGAGED_OPPORTUNITY]->(:CRMOpportunity)
            WITH eng, opp, count(DISTINCT p) AS overlap
            ORDER BY eng.external_id, overlap DESC, coalesce(opp.updated_at, datetime('1970-01-01T00:00:00Z')) DESC
            WITH eng, collect({opp: opp, overlap: overlap}) AS ranked
            WITH eng, ranked[0] AS best
            WHERE best.opp IS NOT NULL
            WITH eng, best.opp AS bestOpp, toInteger(best.overlap) AS overlap
            MERGE (eng)-[r:ENGAGED_OPPORTUNITY_DERIVED]->(bestOpp)
            SET r.derived = true,
                r.ruleId = "R3",
                r.evidence = "contactOverlap",
                r.overlap = overlap,
                r.derivedAt = datetime($derived_at)
            """
            ),
            derived_at=_now_iso(),
        ).consume()
        summary["rules"]["R3"] = {"relationships_created": r3.counters.relationships_created}

        # R4: Promote verified high-confidence assertions to direct relationships.
        _session_run(session, 
            """
            MATCH (:KGAssertion)-[r:ASSERTION_PROMOTED_RELATION {ruleId: "R4"}]->()
            DELETE r
            """
        )
        r4 = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(c:CRMContact)
            WHERE toLower(coalesce(a.status, "proposed")) IN ["verified", "accepted"]
              AND coalesce(a.confidence, 0.0) >= $min_confidence
              AND (
                a.updated_at IS NULL OR
                datetime(a.updated_at) >= datetime() - duration({days: $max_age_days})
              )
            WITH a, c, toLower(coalesce(a.predicate, "")) AS predicate
            WHERE predicate IN ["works_at", "has_opportunity", "committed_to", "has_preference"]
            MERGE (a)-[r:ASSERTION_PROMOTED_RELATION]->(c)
            SET r.derived = true,
                r.ruleId = "R4",
                r.derivedAt = datetime($derived_at),
                r.evidence = "assertionGate"
            """
            ),
            min_confidence=min_confidence,
            max_age_days=max_age_days,
            derived_at=_now_iso(),
        ).consume()
        summary["rules"]["R4"] = {"relationships_created": r4.counters.relationships_created}

        return summary


def get_open_case_counts_for_contact(contact_id: str) -> dict[str, int]:
    with neo4j_session() as session:
        if session is None:
            return {"open_case_contacts": 0, "open_case_opportunities": 0}
        rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (c:CRMContact {external_id: $contact_id})
            OPTIONAL MATCH (c)<-[:ENGAGED_WITH]-(:CRMEngagement)-[:HAS_CASE_CONTACT]->(cc:CaseContact)
            WHERE coalesce(cc.status, "open") = "open"
            OPTIONAL MATCH (c)<-[:ENGAGED_WITH]-(:CRMEngagement)-[:HAS_CASE_OPPORTUNITY]->(co:CaseOpportunity)
            WHERE coalesce(co.status, "open") = "open"
            RETURN count(DISTINCT cc) AS open_case_contacts,
                   count(DISTINCT co) AS open_case_opportunities
            LIMIT 1
            """
            ),
            contact_id=contact_id,
        ).data()
    row = rows[0] if rows else {}
    return {
        "open_case_contacts": int(row.get("open_case_contacts") or 0),
        "open_case_opportunities": int(row.get("open_case_opportunities") or 0),
    }


def get_contact_context_signals_v2(contact_id: str, limit: int = 10) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return []
        rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (canonical:CRMContact {external_id: $contact_id})
            OPTIONAL MATCH (alias:CRMContact)-[:PROMOTED_TO]->(canonical)
            WITH collect(DISTINCT canonical) + collect(DISTINCT alias) AS contacts
            UNWIND contacts AS contact
            WITH DISTINCT contact
            WHERE contact IS NOT NULL
            MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(contact)
            WHERE coalesce(a.status, "proposed") <> "rejected"
            WITH DISTINCT a
            RETURN a.assertion_id AS assertion_id,
                   a.claim_type AS claim_type,
                   a.predicate AS predicate,
                   a.object_name AS object_name,
                   a.confidence AS confidence,
                   a.status AS status,
                   a.sensitive AS sensitive,
                   a.updated_at AS updated_at
            ORDER BY coalesce(a.confidence, 0.0) DESC, a.updated_at DESC
            LIMIT $limit
            """
            ),
            contact_id=contact_id,
            limit=max(1, limit),
        ).data()
    return [
        {
            "assertion_id": row.get("assertion_id"),
            "claim_type": row.get("claim_type"),
            "predicate": row.get("predicate"),
            "object_name": row.get("object_name"),
            "confidence": _as_float(row.get("confidence")),
            "status": row.get("status") or "proposed",
            "sensitive": bool(row.get("sensitive", False)),
            "updated_at": (str(row.get("updated_at")) if row.get("updated_at") is not None else None),
        }
        for row in rows
    ]


def get_contact_assertion_evidence_trace_v2(contact_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with neo4j_session() as session:
        if session is None:
            return []
        rows = _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (canonical:CRMContact {external_id: $contact_id})
            OPTIONAL MATCH (alias:CRMContact)-[:PROMOTED_TO]->(canonical)
            WITH collect(DISTINCT canonical) + collect(DISTINCT alias) AS contacts
            UNWIND contacts AS contact
            WITH DISTINCT contact
            WHERE contact IS NOT NULL
            MATCH (a:KGAssertion)-[:ASSERTS_ABOUT_CONTACT]->(contact)
            WITH DISTINCT a
            OPTIONAL MATCH (a)-[:SUPPORTED_BY]->(ch:EvidenceChunk)
            RETURN a.assertion_id AS assertion_id,
                   a.claim_type AS claim_type,
                   a.predicate AS predicate,
                   a.object_name AS object_name,
                   a.status AS status,
                   a.confidence AS confidence,
                   a.updated_at AS updated_at,
                   collect(DISTINCT {
                        chunk_id: ch.chunk_id,
                        interaction_id: ch.interaction_id,
                        span_json: ch.span_json,
                        quote_hash: ch.quote_hash
                   }) AS evidence
            ORDER BY coalesce(confidence, 0.0) DESC, updated_at DESC
            LIMIT $limit
            """
            ),
            contact_id=contact_id,
            limit=max(1, limit),
        ).data()
    results: list[dict[str, Any]] = []
    for row in rows:
        evidence_rows = row.get("evidence") or []
        evidence: list[dict[str, Any]] = []
        for item in evidence_rows:
            if not isinstance(item, dict) or not item.get("chunk_id"):
                continue
            span_json = item.get("span_json")
            if isinstance(span_json, str):
                try:
                    span_payload = json.loads(span_json)
                except Exception:
                    span_payload = {}
            else:
                span_payload = span_json if isinstance(span_json, dict) else {}
            evidence.append(
                {
                    "chunk_id": item.get("chunk_id"),
                    "interaction_id": item.get("interaction_id"),
                    "span_json": span_payload,
                    "quote_hash": item.get("quote_hash"),
                }
            )
        results.append(
            {
                "assertion_id": row.get("assertion_id"),
                "claim_type": row.get("claim_type"),
                "predicate": row.get("predicate"),
                "object_name": row.get("object_name"),
                "status": row.get("status") or "proposed",
                "confidence": _as_float(row.get("confidence")),
                "evidence": evidence,
            }
        )
    return results


def run_shacl_validation_v2(interaction_id: str | None = None) -> dict[str, Any]:
    with neo4j_session() as session:
        if session is None:
            return {"enabled": False, "valid": True, "violations": []}
        n10s_error: str | None = None
        n10s_rows: list[dict[str, Any]] = []
        try:
            n10s_rows = _session_run(session, 
                """
                CALL n10s.validation.shacl.validate()
                YIELD focusNode, nodeType, shapeId, propertyShape, offendingValue, resultPath, severity, resultMessage
                RETURN focusNode, nodeType, shapeId, propertyShape, offendingValue, resultPath, severity, resultMessage
                LIMIT 200
                """
            ).data()
        except Exception as exc:
            n10s_error = str(exc)

        local_violations: list[dict[str, Any]] = []
        if interaction_id:
            engagement_rows = _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                MATCH (eng:CRMEngagement {external_id: $interaction_id})
                OPTIONAL MATCH (eng)-[:ENGAGED_WITH]->(p:CRMContact)
                RETURN eng.external_id AS engagement_id,
                       eng.occurred_at AS occurred_at,
                       count(DISTINCT p) AS participant_count
                LIMIT 1
                """
                ),
                interaction_id=interaction_id,
            ).data()
            if not engagement_rows:
                local_violations.append(
                    {
                        "focus_node": interaction_id,
                        "shape_id": "CRMEngagementExistsShape",
                        "severity": "Violation",
                        "message": "CRMEngagement node is missing for interaction.",
                    }
                )
            else:
                engagement = engagement_rows[0]
                if engagement.get("occurred_at") is None:
                    local_violations.append(
                        {
                            "focus_node": engagement.get("engagement_id"),
                            "shape_id": "CRMEngagementOccurredAtShape",
                            "severity": "Violation",
                            "message": "CRMEngagement must have occurred_at.",
                        }
                    )
                if int(engagement.get("participant_count") or 0) < 1:
                    local_violations.append(
                        {
                            "focus_node": engagement.get("engagement_id"),
                            "shape_id": "CRMEngagementParticipantShape",
                            "severity": "Violation",
                            "message": "CRMEngagement must have at least one ENGAGED_WITH contact.",
                        }
                    )

            assertion_rows = _session_run(session, 
                f"""
                MATCH (eng:{_HS_ENGAGEMENT_BASE_LABEL or 'CRMEngagement'} {{external_id: $interaction_id}})
                MATCH (a:{_HS_ASSERTION_LABEL or 'KGAssertion'})-[:{_HS_DERIVED_FROM_ENGAGEMENT_REL or 'HAS_ASSERTION'}]->(eng)
                OPTIONAL MATCH (a)-[:{_HS_SOURCE_ARTIFACT_REL or 'SUPPORTED_BY'}]->(ev:{_HS_SOURCE_ARTIFACT_LABEL or 'EvidenceChunk'})
                OPTIONAL MATCH (a)-[:{_HS_EXTRACTION_EVENT_REL or 'FROM_EXTRACTION_EVENT'}]->(evt:{_HS_EXTRACTION_EVENT_LABEL or 'ExtractionEvent'})
                RETURN a.assertion_id AS assertion_id,
                       a.predicate AS predicate,
                       a.object_name AS object_name,
                       count(DISTINCT ev) AS evidence_count,
                       count(DISTINCT evt) AS extraction_event_count
                """,
                interaction_id=interaction_id,
            ).data()
            for row in assertion_rows:
                if int(row.get("evidence_count") or 0) < 1:
                    local_violations.append(
                        {
                            "focus_node": row.get("assertion_id"),
                            "shape_id": "KGAssertionEvidenceShape",
                            "severity": "Violation",
                            "message": "KGAssertion must be supported by at least one EvidenceChunk.",
                        }
                    )
                if int(row.get("extraction_event_count") or 0) < 1:
                    local_violations.append(
                        {
                            "focus_node": row.get("assertion_id"),
                            "shape_id": "KGAssertionExtractionEventShape",
                            "severity": "Violation",
                            "message": "KGAssertion must be linked to an ExtractionEvent.",
                        }
                    )
                if not _normalize_text(row.get("predicate")) or not _normalize_text(row.get("object_name")):
                    local_violations.append(
                        {
                            "focus_node": row.get("assertion_id"),
                            "shape_id": "KGAssertionTripleShape",
                            "severity": "Violation",
                            "message": "KGAssertion must include predicate and object_name.",
                        }
                    )

            evidence_rows = _session_run(session, 
                f"""
                MATCH (eng:{_HS_ENGAGEMENT_BASE_LABEL or 'CRMEngagement'} {{external_id: $interaction_id}})
                MATCH (a:{_HS_ASSERTION_LABEL or 'KGAssertion'})-[:{_HS_DERIVED_FROM_ENGAGEMENT_REL or 'HAS_ASSERTION'}]->(eng)
                MATCH (a)-[:{_HS_SOURCE_ARTIFACT_REL or 'SUPPORTED_BY'}]->(ev:{_HS_SOURCE_ARTIFACT_LABEL or 'EvidenceChunk'})
                RETURN ev.chunk_id AS chunk_id,
                       ev.interaction_id AS interaction_id,
                       ev.span_json AS span_json
                """,
                interaction_id=interaction_id,
            ).data()
            for row in evidence_rows:
                if not _normalize_text(row.get("chunk_id")):
                    local_violations.append(
                        {
                            "focus_node": row.get("chunk_id"),
                            "shape_id": "EvidenceChunkIdShape",
                            "severity": "Violation",
                            "message": "EvidenceChunk must include chunk_id.",
                        }
                    )
                if not _normalize_text(row.get("interaction_id")):
                    local_violations.append(
                        {
                            "focus_node": row.get("chunk_id"),
                            "shape_id": "EvidenceChunkInteractionShape",
                            "severity": "Violation",
                            "message": "EvidenceChunk must include interaction_id provenance.",
                        }
                    )
                if row.get("span_json") is None:
                    local_violations.append(
                        {
                            "focus_node": row.get("chunk_id"),
                            "shape_id": "EvidenceChunkSpanShape",
                            "severity": "Violation",
                            "message": "EvidenceChunk must include span_json provenance.",
                        }
                    )

            opportunity_rows = _session_run(session, 
                _ontify_v2_read_cypher(
                    """
                MATCH (eng:CRMEngagement {external_id: $interaction_id})-[:ENGAGED_OPPORTUNITY|ENGAGED_OPPORTUNITY_DERIVED]->(opp:CRMOpportunity)
                OPTIONAL MATCH (opp)-[:OPPORTUNITY_FOR_COMPANY|OPPORTUNITY_FOR_COMPANY_DERIVED]->(co:CRMCompany)
                OPTIONAL MATCH (opp)-[:INVOLVES_CONTACT]->(ct:CRMContact)
                RETURN opp.external_id AS opportunity_id,
                       count(DISTINCT co) AS company_count,
                       count(DISTINCT ct) AS contact_count
                """
                ),
                interaction_id=interaction_id,
            ).data()
            for row in opportunity_rows:
                if int(row.get("company_count") or 0) != 1:
                    local_violations.append(
                        {
                            "focus_node": row.get("opportunity_id"),
                            "shape_id": "CRMOpportunityCompanyShape",
                            "severity": "Violation",
                            "message": "CRMOpportunity must link to exactly one company.",
                        }
                    )
                if int(row.get("contact_count") or 0) < 1:
                    local_violations.append(
                        {
                            "focus_node": row.get("opportunity_id"),
                            "shape_id": "CRMOpportunityContactShape",
                            "severity": "Violation",
                            "message": "CRMOpportunity must involve at least one contact.",
                        }
                    )

    n10s_violations = [
        {
            "focus_node": row.get("focusNode"),
            "node_type": row.get("nodeType"),
            "shape_id": row.get("shapeId"),
            "property_shape": row.get("propertyShape"),
            "offending_value": row.get("offendingValue"),
            "result_path": row.get("resultPath"),
            "severity": row.get("severity"),
            "message": row.get("resultMessage"),
        }
        for row in n10s_rows
    ]
    all_violations = n10s_violations + local_violations
    result: dict[str, Any] = {
        "enabled": True,
        "n10s_available": n10s_error is None,
        "valid": len(all_violations) == 0,
        "violations": all_violations,
    }
    if n10s_error is not None:
        result["n10s_error"] = n10s_error
    if interaction_id:
        result["interaction_id"] = interaction_id
    return result


def delete_contact_graph(contact_id: str) -> None:
    with neo4j_session() as session:
        if session is None:
            return

        _session_run(session, 
            """
            MATCH (s:ScoreSnapshot {contact_id: $contact_id})
            DETACH DELETE s
            """,
            contact_id=contact_id,
        )
        _session_run(session, 
            """
            MATCH ()-[r:RELATES_TO {contact_id: $contact_id}]-()
            DELETE r
            """,
            contact_id=contact_id,
        )
        _session_run(session, 
            """
            MATCH (e:Entity {contact_id: $contact_id})
            DETACH DELETE e
            """,
            contact_id=contact_id,
        )
        _session_run(session, 
            """
            MATCH (e:Entity)
            WHERE NOT (e)--()
            DELETE e
            """
        )
        _session_run(session, 
            """
            MATCH (c:Contact {contact_id: $contact_id})
            DETACH DELETE c
            """,
            contact_id=contact_id,
        )
        _session_run(session, 
            _ontify_v2_read_cypher(
                """
            MATCH (c:CRMContact {external_id: $contact_id})
            DETACH DELETE c
            """
            ),
            contact_id=contact_id,
        )


def delete_case_contact_graph_by_email(email: str) -> int:
    normalized_email = _normalize_text(email).lower()
    if not normalized_email:
        return 0
    with neo4j_session() as session:
        if session is None:
            return 0
        deleted_count_rows = _session_run(
            session,
            _ontify_v2_read_cypher(
                """
                MATCH (cc:CaseContact)
                WHERE toLower(cc.email) = $email
                RETURN count(cc) AS deleted_count
                """
            ),
            email=normalized_email,
        ).data()
        deleted_count = int((deleted_count_rows[0] or {}).get("deleted_count", 0) or 0) if deleted_count_rows else 0
        if deleted_count == 0:
            return 0
        _session_run(
            session,
            _ontify_v2_read_cypher(
                """
                MATCH (cc:CaseContact)
                WHERE toLower(cc.email) = $email
                DETACH DELETE cc
                """
            ),
            email=normalized_email,
        )
        return deleted_count
