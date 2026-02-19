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
