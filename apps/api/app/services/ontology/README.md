# Ontology Mapping Config

The canonical claim/relation ontology is configured in:

- `app/services/ontology/ontology_config.json`

You can override this location with:

- `ONTOLOGY_CONFIG_PATH`

## Purpose

This config controls how extracted interaction facts are normalized into Lux claim types and graph predicates.

## Key sections

- `predicate_aliases`: maps extractor predicates to canonical predicates
- `predicate_claim_type`: maps canonical predicates to claim types
- `claim_types`: defaults per claim type (`default_predicate`, `subject_kind`, `object_kind`, `sensitive`, `high_value`)
- `high_value_predicates`: predicates that should trigger stronger review/priority handling

## Example customization

If you want `child_school` to map into education claims:

```json
{
  "predicate_aliases": {
    "child_school": "has_education_detail"
  }
}
```

Only the keys you override are needed. Missing keys fall back to built-in defaults.

## Runtime Ontology Contract (OWL TTL)

The runtime now also reads the authoritative OWL ontology from:

- `.CODEX/CRM_ontoology_spec.ttl`

See `app/services/ontology/runtime_contract.py` for:

- TTL-backed class/property registry (`hs:*`)
- LPG label/relationship to ontology-term mappings (`CRMContact -> hs:Contact`, `WORKS_AT -> hs:worksAt`)
- drift diagnostics for `ontology_config.json` predicates that do not exist in the TTL

## Alias strategy (HubSpot / Salesforce / extractor outputs)

- OWL terms in `.CODEX/CRM_ontoology_spec.ttl` are canonical.
- `ontology_config.json` is an alias/normalization layer for extractor outputs and legacy predicate names.
- Runtime graph writes persist ontology metadata (`ont_class`, `ont_predicate`) on V2 nodes/edges so the LPG graph remains traceable to canonical ontology terms while the application still uses Neo4j property-graph labels.
