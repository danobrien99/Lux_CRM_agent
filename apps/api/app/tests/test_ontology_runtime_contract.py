from __future__ import annotations

from app.services.ontology.runtime_contract import (
    load_runtime_ontology_contract,
    lpg_node_neo4j_label_identifier,
    lpg_relationship_neo4j_type_identifier,
    ontology_config_drift_report,
    ontology_term_to_neo4j_identifier,
    predicate_token_to_ontology_property,
    validate_lpg_mappings_against_ontology,
)


def test_runtime_ontology_contract_parses_expected_terms() -> None:
    contract = load_runtime_ontology_contract()

    for cls in (
        "hs:Contact",
        "hs:Company",
        "hs:Deal",
        "hs:Engagement",
        "hs:ScoreSnapshot",
        "hs:Assertion",
        "hs:ExtractionEvent",
        "hs:CaseContact",
        "hs:CaseOpportunity",
    ):
        assert contract.has_class(cls), cls

    for prop in (
        "hs:worksAt",
        "hs:engagedWith",
        "hs:involvesContact",
        "hs:dealForCompany",
        "hs:assertedAt",
        "hs:targetsContact",
        "hs:hasCaseContact",
        "hs:hasCaseOpportunity",
        "hs:promotedTo",
        "hs:hasScore",
    ):
        assert contract.has_property(prop), prop


def test_lpg_mappings_resolve_to_known_ontology_terms() -> None:
    report = validate_lpg_mappings_against_ontology()
    assert report["valid"] is True, report
    assert report["unknown_node_classes"] == []
    assert report["unknown_relationship_predicates"] == []


def test_predicate_token_to_ontology_property_uses_camel_case() -> None:
    assert predicate_token_to_ontology_property("works_at") == "hs:worksAt"
    assert predicate_token_to_ontology_property("deal_for_company") == "hs:dealForCompany"
    assert predicate_token_to_ontology_property("") is None


def test_ontology_terms_and_lpg_mappings_produce_safe_neo4j_identifiers() -> None:
    assert ontology_term_to_neo4j_identifier("hs:Contact") == "`Contact`"
    assert ontology_term_to_neo4j_identifier("bad term") is None
    assert lpg_node_neo4j_label_identifier("CRMContact") == "`Contact`"
    assert lpg_node_neo4j_label_identifier("ScoreSnapshot") == "`ScoreSnapshot`"
    assert lpg_node_neo4j_label_identifier("CaseOpportunity") == "`CaseOpportunity`"
    assert lpg_relationship_neo4j_type_identifier("WORKS_AT") == "`worksAt`"
    assert lpg_relationship_neo4j_type_identifier("HAS_SCORE") == "`hasScore`"
    assert lpg_relationship_neo4j_type_identifier("HAS_CASE_CONTACT") == "`hasCaseContact`"


def test_ontology_config_drift_report_flags_non_ttl_predicates() -> None:
    report = ontology_config_drift_report()
    assert report["config_predicate_count"] > 0
    assert "hs:worksAt" in report["mapped_terms"]
    # Current ontology_config.json still contains custom predicates not present in the TTL.
    assert report["valid"] is False
    assert len(report["unknown_terms"]) >= 1
