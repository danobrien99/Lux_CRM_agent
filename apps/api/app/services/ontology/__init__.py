from __future__ import annotations

from app.services.ontology.mapper import (
    canonicalize_predicate,
    claim_type_for_predicate,
    clear_ontology_cache,
    load_ontology_config,
    map_relation_to_claim,
    map_topic_to_claim,
    relation_payload_from_claim,
)

__all__ = [
    "load_ontology_config",
    "clear_ontology_cache",
    "canonicalize_predicate",
    "claim_type_for_predicate",
    "map_relation_to_claim",
    "map_topic_to_claim",
    "relation_payload_from_claim",
]
