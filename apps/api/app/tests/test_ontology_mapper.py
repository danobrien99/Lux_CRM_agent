from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.services.ontology import (
    canonicalize_predicate,
    claim_type_for_predicate,
    clear_ontology_cache,
    clear_runtime_ontology_contract_cache,
    map_relation_to_claim,
    relation_payload_from_claim,
)


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    get_settings.cache_clear()
    clear_ontology_cache()
    clear_runtime_ontology_contract_cache()
    yield
    get_settings.cache_clear()
    clear_ontology_cache()
    clear_runtime_ontology_contract_cache()


def test_default_ontology_maps_employment_alias_to_canonical_claim() -> None:
    claim = map_relation_to_claim(
        {
            "subject": "contact",
            "predicate": "employment_change",
            "object": "Contoso",
            "confidence": 0.94,
        },
        source_system="cognee",
    )
    assert claim is not None
    assert claim["claim_type"] == "employment"
    assert claim["value_json"]["predicate"] == "works_at"
    assert claim["ontology_predicate"] == "hs:worksAt"
    assert claim["ontology_supported"] is True
    payload = relation_payload_from_claim(claim)
    assert payload is not None
    assert payload["object_kind"] == "Company"
    assert payload["high_value"] is True


def test_custom_ontology_file_overrides_mapping(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "custom_ontology.json"
    config_path.write_text(
        json.dumps(
            {
                "predicate_aliases": {"big_interest": "has_opportunity"},
                "predicate_claim_type": {"has_opportunity": "opportunity"},
                "claim_types": {
                    "opportunity": {
                        "default_predicate": "has_opportunity",
                        "subject_kind": "Contact",
                        "object_kind": "Deal",
                        "sensitive": False,
                        "high_value": True,
                    }
                },
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ONTOLOGY_CONFIG_PATH", str(config_path))
    get_settings.cache_clear()
    clear_ontology_cache()

    assert canonicalize_predicate("big_interest") == "has_opportunity"
    assert claim_type_for_predicate("big_interest") == "opportunity"

    claim = map_relation_to_claim(
        {
            "subject": "contact",
            "predicate": "big_interest",
            "object": "Nature Investment Hub workshop expansion",
        },
        source_system="cognee",
    )
    assert claim is not None
    assert claim["claim_type"] == "opportunity"
    assert claim["ontology_predicate"] == "hs:hasOpportunity"
    assert claim["ontology_supported"] is False
    assert claim["promotion_scope"] == "case_evidence_only"
    payload = relation_payload_from_claim(claim)
    assert payload is None
