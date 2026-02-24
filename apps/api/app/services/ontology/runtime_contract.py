from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


_TTL_TERM_RE = re.compile(r"^hs:([A-Za-z][A-Za-z0-9_]*)\s+a\s+owl:(Class|ObjectProperty|DatatypeProperty)\b")


@dataclass(frozen=True)
class OntologyRuntimeContract:
    namespace: str
    classes: frozenset[str]
    object_properties: frozenset[str]
    datatype_properties: frozenset[str]

    @property
    def properties(self) -> frozenset[str]:
        return frozenset(set(self.object_properties) | set(self.datatype_properties))

    def has_class(self, term: str | None) -> bool:
        return _norm_term(term) in self.classes

    def has_property(self, term: str | None) -> bool:
        normalized = _norm_term(term)
        return normalized in self.object_properties or normalized in self.datatype_properties


LPG_NODE_ONTOLOGY_CLASS_MAP: dict[str, str] = {
    "CRMContact": "hs:Contact",
    "CRMCompany": "hs:Company",
    "CRMEngagement": "hs:Engagement",
    "CRMOpportunity": "hs:Deal",
    "ScoreSnapshot": "hs:ScoreSnapshot",
    "CaseContact": "hs:CaseContact",
    "CaseOpportunity": "hs:CaseOpportunity",
    "KGAssertion": "hs:Assertion",
    "ExtractionEvent": "hs:ExtractionEvent",
    # EvidenceChunk is a graph projection of source evidence; map to SourceArtifact for ontology traceability.
    "EvidenceChunk": "hs:SourceArtifact",
}

ENGAGEMENT_TYPE_ONTOLOGY_CLASS_MAP: dict[str, str] = {
    "email": "hs:Email",
    "meeting": "hs:Meeting",
    "call": "hs:Call",
    "note": "hs:Note",
    "meeting_notes": "hs:Note",
    "chat_message": "hs:ChatMessage",
}

LPG_RELATIONSHIP_ONTOLOGY_PREDICATE_MAP: dict[str, str] = {
    "WORKS_AT": "hs:worksAt",
    "ENGAGED_WITH": "hs:engagedWith",
    "INVOLVES_CONTACT": "hs:involvesContact",
    "OPPORTUNITY_FOR_COMPANY": "hs:dealForCompany",
    "OPPORTUNITY_FOR_COMPANY_DERIVED": "hs:dealForCompany",
    "ENGAGED_COMPANY": "hs:engagedCompany",
    "ENGAGED_COMPANY_DERIVED": "hs:engagedCompany",
    "ENGAGED_OPPORTUNITY": "hs:engagedDeal",
    "ENGAGED_OPPORTUNITY_DERIVED": "hs:engagedDeal",
    "FROM_EXTRACTION_EVENT": "hs:extractionEvent",
    "SUPPORTED_BY": "hs:sourceArtifact",
    "ASSERTS_ABOUT_CONTACT": "hs:assertionObject",
    "ASSERTS_ABOUT_COMPANY": "hs:assertionObject",
    # Direction is Engagement -> Assertion; ontology defines Assertion -> Engagement (derivedFromEngagement).
    "HAS_ASSERTION": "hs:derivedFromEngagement",
    "TARGETS_CONTACT": "hs:targetsContact",
    "HAS_CASE_CONTACT": "hs:hasCaseContact",
    "HAS_CASE_OPPORTUNITY": "hs:hasCaseOpportunity",
    "PROMOTED_TO": "hs:promotedTo",
    "HAS_SCORE": "hs:hasScore",
}


def _norm_term(term: str | None) -> str:
    return (term or "").strip()


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".CODEX" / "CRM_ontoology_spec.ttl").exists():
            return parent
    raise FileNotFoundError("Could not locate repo root containing .CODEX/CRM_ontoology_spec.ttl")


def _ontology_ttl_path() -> Path:
    return _repo_root() / ".CODEX" / "CRM_ontoology_spec.ttl"


def _parse_ttl_terms(ttl_text: str) -> tuple[set[str], set[str], set[str]]:
    classes: set[str] = set()
    object_properties: set[str] = set()
    datatype_properties: set[str] = set()
    for raw_line in ttl_text.splitlines():
        line = raw_line.strip()
        match = _TTL_TERM_RE.match(line)
        if not match:
            continue
        local_name = match.group(1)
        kind = match.group(2)
        term = f"hs:{local_name}"
        if kind == "Class":
            classes.add(term)
        elif kind == "ObjectProperty":
            object_properties.add(term)
        elif kind == "DatatypeProperty":
            datatype_properties.add(term)
    return classes, object_properties, datatype_properties


@lru_cache(maxsize=1)
def load_runtime_ontology_contract() -> OntologyRuntimeContract:
    ttl_text = _ontology_ttl_path().read_text(encoding="utf-8")
    classes, object_properties, datatype_properties = _parse_ttl_terms(ttl_text)
    return OntologyRuntimeContract(
        namespace="https://luxcrm.ai/ontologies/hubspot-crm#",
        classes=frozenset(classes),
        object_properties=frozenset(object_properties),
        datatype_properties=frozenset(datatype_properties),
    )


def clear_runtime_ontology_contract_cache() -> None:
    load_runtime_ontology_contract.cache_clear()


def lpg_node_ontology_class(label: str | None, *, engagement_type: str | None = None) -> str | None:
    normalized_label = _norm_term(label)
    if normalized_label == "CRMEngagement":
        etype = _norm_term(engagement_type).lower().strip()
        if etype and etype in ENGAGEMENT_TYPE_ONTOLOGY_CLASS_MAP:
            return ENGAGEMENT_TYPE_ONTOLOGY_CLASS_MAP[etype]
    return LPG_NODE_ONTOLOGY_CLASS_MAP.get(normalized_label)


def lpg_relationship_ontology_predicate(rel_type: str | None) -> str | None:
    return LPG_RELATIONSHIP_ONTOLOGY_PREDICATE_MAP.get(_norm_term(rel_type))


_ONTOLOGY_TERM_CYPHER_SAFE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*:[A-Za-z][A-Za-z0-9_]*$")
_ONTOLOGY_LOCAL_TERM_SAFE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def ontology_term_to_neo4j_identifier(term: str | None) -> str | None:
    normalized = _norm_term(term)
    if not normalized:
        return None
    if _ONTOLOGY_TERM_CYPHER_SAFE_RE.fullmatch(normalized):
        _prefix, local = normalized.split(":", 1)
        if not _ONTOLOGY_LOCAL_TERM_SAFE_RE.fullmatch(local):
            return None
        return f"`{local}`"
    if _ONTOLOGY_LOCAL_TERM_SAFE_RE.fullmatch(normalized):
        return f"`{normalized}`"
    return None


def lpg_node_neo4j_label_identifier(label: str | None, *, engagement_type: str | None = None) -> str | None:
    return ontology_term_to_neo4j_identifier(lpg_node_ontology_class(label, engagement_type=engagement_type))


def lpg_relationship_neo4j_type_identifier(rel_type: str | None) -> str | None:
    return ontology_term_to_neo4j_identifier(lpg_relationship_ontology_predicate(rel_type))


def validate_lpg_mappings_against_ontology() -> dict[str, Any]:
    contract = load_runtime_ontology_contract()
    unknown_node_classes = sorted(
        {term for term in LPG_NODE_ONTOLOGY_CLASS_MAP.values() if term and not contract.has_class(term)}
        | {term for term in ENGAGEMENT_TYPE_ONTOLOGY_CLASS_MAP.values() if term and not contract.has_class(term)}
    )
    unknown_rel_predicates = sorted(
        {term for term in LPG_RELATIONSHIP_ONTOLOGY_PREDICATE_MAP.values() if term and not contract.has_property(term)}
    )
    return {
        "valid": not unknown_node_classes and not unknown_rel_predicates,
        "unknown_node_classes": unknown_node_classes,
        "unknown_relationship_predicates": unknown_rel_predicates,
    }


def _snake_to_hs_term(token: str) -> str:
    parts = [part for part in token.strip().split("_") if part]
    if not parts:
        return ""
    head, *tail = parts
    camel = head + "".join(part.capitalize() for part in tail)
    return f"hs:{camel}"


def predicate_token_to_ontology_property(token: str | None) -> str | None:
    normalized = (token or "").strip()
    if not normalized:
        return None
    return _snake_to_hs_term(normalized)


def ontology_config_drift_report() -> dict[str, Any]:
    # Lazy import to avoid circular dependency (mapper imports ontology package).
    from app.services.ontology.mapper import load_ontology_config

    contract = load_runtime_ontology_contract()
    config = load_ontology_config()
    referenced_predicates: set[str] = set()

    predicate_aliases = config.get("predicate_aliases")
    if isinstance(predicate_aliases, dict):
        for canonical in predicate_aliases.values():
            if isinstance(canonical, str) and canonical.strip():
                referenced_predicates.add(canonical.strip())

    predicate_claim_type = config.get("predicate_claim_type")
    if isinstance(predicate_claim_type, dict):
        for predicate in predicate_claim_type.keys():
            if isinstance(predicate, str) and predicate.strip():
                referenced_predicates.add(predicate.strip())

    claim_types = config.get("claim_types")
    if isinstance(claim_types, dict):
        for value in claim_types.values():
            if not isinstance(value, dict):
                continue
            default_predicate = value.get("default_predicate")
            if isinstance(default_predicate, str) and default_predicate.strip():
                referenced_predicates.add(default_predicate.strip())

    high_value_predicates = config.get("high_value_predicates")
    if isinstance(high_value_predicates, list):
        for predicate in high_value_predicates:
            if isinstance(predicate, str) and predicate.strip():
                referenced_predicates.add(predicate.strip())

    mapped_hs_terms = sorted(filter(None, (_snake_to_hs_term(token) for token in referenced_predicates)))
    unknown_terms = [term for term in mapped_hs_terms if not contract.has_property(term)]

    return {
        "valid": len(unknown_terms) == 0,
        "ttl_property_count": len(contract.properties),
        "config_predicate_count": len(referenced_predicates),
        "mapped_terms": mapped_hs_terms,
        "unknown_terms": unknown_terms,
    }
