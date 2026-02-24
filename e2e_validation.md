# E2E Validation Guide (KG V2 Refactor)

This guide describes the runnable end-to-end validation harness for the CRM KG V2 refactor.

## Files

- Runner: `/Users/dobrien/code/Lux/Lux_CRM_agent/run_e2e_validation.sh`
- Fixtures: `/Users/dobrien/code/Lux/Lux_CRM_agent/e2e_fixtures/`

## What the runner validates

1. Seeds canonical contacts via `/v1/contacts/sync`.
2. Ingests an interaction with an unknown external participant.
3. Verifies autonomous `CaseContact` + provisional contact creation with evidence.
4. Verifies contact promotion gate behavior:
   - fails without override,
   - succeeds with override.
5. Ingests an opportunity-signal interaction and verifies `CaseOpportunity` creation with motivators.
6. Promotes the case opportunity to canonical.
7. Ingests follow-up interaction on same thread and verifies no new open case opportunity remains.
8. Generates a draft and verifies retrieval trace contains provenance (`assertion_evidence_trace`) and motivator signals.
9. Verifies score endpoint resolves for the promoted contact.
10. Enqueues inference run.
11. Optionally runs Neo4j assertions (promotion edge, evidence provenance, engagement-opportunity linkage).

## Prerequisites

1. Stack running (`api`, `worker`, `redis`, `neo4j`) and API reachable.
2. Neo4j schema initialized and ontology/shapes loaded:
```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
source apps/api/.venv/bin/activate
python scripts/init_neo4j_schema.py
python scripts/load_ontology_and_shacl.py
```
3. If webhook secret is enabled, ensure `.env` has `N8N_WEBHOOK_SECRET` (runner auto-loads `.env` and sends header).

## Run

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
chmod +x run_e2e_validation.sh
./run_e2e_validation.sh
```

## Runtime flags

- `BASE_URL` (default: `http://localhost:8000/v1`)
- `POLL_TIMEOUT_SECONDS` (default: `240`)
- `POLL_INTERVAL_SECONDS` (default: `3`)
- `NEO4J_ASSERT`:
  - `auto` (default): run Neo4j checks if Docker+Neo4j container detected.
  - `on`: require Neo4j checks; fail if unavailable.
  - `off`: skip Neo4j checks.

Example:

```bash
BASE_URL="http://localhost:8000/v1" NEO4J_ASSERT=on ./run_e2e_validation.sh
```

## Fixture overview

- `contacts_seed.json.template`: seeds owner + existing external contact.
- `interaction_unknown_external.json.template`: drives autonomous case-contact path.
- `interaction_new_opportunity.json.template`: drives case-opportunity detection.
- `interaction_followup_opportunity.json.template`: validates follow-up handling on promoted thread.
- `promote_case_contact_fail.json`: gate-fail promotion request.
- `promote_case_contact_override.json`: override promotion request.
- `promote_case_opportunity.json`: promote case opportunity.
- `draft_request.json.template`: draft retrieval/provenance validation.

## Pass/fail behavior

- Every checkpoint is asserted.
- First failed assertion exits non-zero with a `FAIL:` message.
- Success prints total `Pass checks` count and run ID.

## Notes

- Payload fixture values are templated at runtime with unique IDs/emails/timestamps to avoid collisions.
- If worker processing is delayed or down, polling steps will timeout and fail with explicit messages.
- For deepest validation, keep `NEO4J_ASSERT=on`.
