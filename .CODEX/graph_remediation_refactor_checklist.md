# Lux CRM Graph Remediation + Refactor Checklist

## Objective

Restore the Neo4j graph to a clean, ontology-aligned CRM/Case/Evidence model that is usable for:

- contact prioritization and relationship scoring
- opportunity identification and case promotion
- evidence-backed drafting and next-step recommendations

This plan addresses the current failure modes observed in the live graph:

- topic/noise assertions dominating graph writes (`Check`, `From`, `Great`, etc.)
- false `CaseOpportunity` creation from noisy claims
- legacy `RELATES_TO` graph contaminating scoring paths and metrics
- contradictory employer/company relations from duplicate source rows and stale legacy edges
- mixed-mode runtime (`GRAPH_V2_DUAL_WRITE` + heuristic fallback) without semantic gating

## Scope

- Runtime extraction gating and claim filtering
- Opportunity/case creation gating
- Contact sync dedupe and employer conflict resolution
- Legacy graph contamination controls (especially scoring paths)
- SHACL/runtime validation enablement plan
- Data reset + backfill validation on small sample

## Constraints / Assumptions

- In-place refactor (no full rewrite)
- Existing API endpoints remain stable
- V2 graph remains the target model; legacy graph may remain temporarily for compatibility but must stop driving scoring
- Contact sheet source data may contain duplicate rows for the same email

## Remediation Checklist

## Sequential Refactor Execution Rule (Current Backlog)

- [ ] For each backlog phase below: implement all listed fixes, run all listed tests, and record results before starting the next phase.
- [ ] Do not mark a phase complete if any acceptance test is failing, skipped, or only partially implemented.
- [ ] Keep legacy compatibility behavior only where explicitly noted; otherwise prefer V2 CRM/Case/Evidence semantics.

## Implementation Backlog (Post-Remediation Product Alignment)

This backlog extends the remediation work above and covers the remaining gaps identified in the code review against the intended CRM outcomes.

### Phase 7: Ontology Authority (OWL TTL -> Runtime Contract)

Problem addressed:

- Runtime writes use hard-coded LPG labels/relationships and a separate JSON claim taxonomy instead of the OWL ontology.
- `.CODEX/CRM_ontoology_spec.ttl` is loaded into Neo4j but not used as the authoritative runtime schema contract.
- `ontology_config.json` can drift from ontology terms without runtime detection.

Implementation checklist:

- [x] Add a runtime ontology contract loader that reads `.CODEX/CRM_ontoology_spec.ttl` and exposes canonical class/property registries.
- [x] Add explicit LPG-to-ontology mappings for V2 labels and relationship types (e.g., `CRMContact -> hs:Contact`, `WORKS_AT -> hs:worksAt`).
- [x] Persist ontology term metadata (`ont_class`, `ont_predicate`, or equivalent) on V2 nodes/relationships/assertions during writes.
- [x] Add runtime diagnostics (or CI test) that detect ontology drift between `ontology_config.json` and TTL-backed registry.
- [x] Document how ontology aliases (HubSpot/Salesforce naming differences) are represented without changing canonical ontology terms.

Testing plan:

- [x] T5.1 Unit test: ontology contract loader parses expected classes/properties from TTL (`hs:Contact`, `hs:Company`, `hs:Deal`, `hs:Engagement`, `hs:worksAt`, `hs:engagedWith`, `hs:involvesContact`, `hs:dealForCompany`, `hs:assertedAt`).
- [x] T5.2 Unit test: LPG mapping table resolves to valid ontology terms (no unknown `hs:*` references).
- [x] T5.3 Integration smoke test: after a `sheets_sync` + one interaction ingest, verify V2 graph nodes/edges include ontology metadata properties.
- [x] T5.4 Drift report test: runtime diagnostics flag unsupported/custom predicates in `ontology_config.json` (expected until later phase resolves taxonomy alignment).

Acceptance criteria:

- [x] TTL is read by application runtime code (not only bootstrap scripts).
- [x] V2 graph writes include ontology metadata sufficient to trace nodes/edges to OWL terms.
- [x] Ontology drift can be detected deterministically in tests.

### Phase 8: Ontology-Aligned Claim Taxonomy + Semantic Promotion Gates

Problem addressed:

- Extraction claim mapping is driven by `ontology_config.json` and generic claim types (`topic`, `opportunity`, etc.), not ontology-backed domains/ranges.
- SHACL catches structure but not semantic misuse (e.g., topic tokens treated as companies/opportunities).
- Promotion to CRM Graph is not fully tied to ontology gate rules in `.CODEX/Ontology_design.md`.

Implementation checklist:

- [ ] Replace/augment `ontology_config.json` predicate catalog with ontology-derived predicate registry and explicit alias layer.
- [ ] Add domain/range-aware claim normalization (contact/company/opportunity/person-detail semantics).
- [x] Implement semantic gate rules that prevent weak topic claims from driving company/opportunity/motivator derivation.
- [ ] Add confidence/recency/status gate enforcement for CRM Graph promotion per `.CODEX/Ontology_design.md` decision table.
- [x] Separate “Evidence/Case-only” claims from “CRM-promotable” claims in a deterministic policy module.
- [ ] Add temporal scope handling for claim candidates where date phrases are detected (prepare V2 assertion temporal fields in Phase 9).

Testing plan:

- [ ] T6.1 Unit tests: claim normalization maps supported aliases to canonical ontology predicates.
- [ ] T6.2 Unit tests: unsupported predicates are quarantined (Evidence/Case only) and not promoted to CRM Graph.
- [ ] T6.3 Worker integration test: low-signal greeting/catch-up emails create no `CaseOpportunity` and no promoted company/opportunity signals.
- [ ] T6.4 Worker integration test: explicit employment + opportunity mentions are promoted/gated correctly with evidence attached.

Acceptance criteria:

- [ ] Topic/noise claims no longer influence company hints, motivators, or opportunity creation.
- [ ] Promotion policy is explicit, testable, and aligned to the three-layer contract.

### Phase 8B: Physical Ontology-Native Neo4j Migration (Labels / Relationship Types)

Problem addressed:

- The graph is still physically stored with V2 LPG labels/relationship types (`CRMContact`, `CRMCompany`, `WORKS_AT`, `ENGAGED_WITH`, etc.) even after ontology metadata stamping.
- Users inspecting Neo4j cannot validate ontology conformance directly because ontology terms are only present as properties (`ont_class`, `ont_predicate`).
- Runtime write/read/UI code remains coupled to V2 label/type names.

Implementation checklist:

- [x] Define and document the Neo4j physical naming convention for ontology-native identifiers (escaped literal terms, e.g. ``:`hs:Contact` `` and ``[:`hs:worksAt`]``).
- [x] Extend `.CODEX/CRM_ontoology_spec.ttl` with Case-layer classes/properties (or explicit case namespace) required by runtime (`CaseContact`, `CaseOpportunity`, promotion links, engagement-case links).
- [x] Add runtime helper functions for safe ontology term -> Neo4j label/relationship identifiers.
- [x] Add ontology-native constraints/indexes in `apps/api/app/db/neo4j/schema.py` for core CRM/Case/Evidence classes.
- [x] Patch core write paths to dual-write ontology-native labels and relationship types on the same nodes/edges during transition:
  - [x] `merge_contact`
  - [x] `merge_interaction`
  - [x] `attach_contact_interaction`
  - [x] `create_extraction_event_v2`
  - [x] `create_assertion_with_evidence_v2`
  - [x] `upsert_case_contact_v2` / `promote_case_contact_v2`
  - [x] `upsert_case_opportunity_v2` / `promote_case_opportunity_v2`
- [x] Keep V2 labels/types temporarily for read compatibility while read-path/UI cutover is in progress.
- [x] Add verification queries/tests that confirm dual physical projection appears in Neo4j for the same entities/relations.

Testing plan:

- [x] T6B.1 Unit tests: runtime ontology contract includes Case-layer terms and safe Neo4j identifier helpers.
- [x] T6B.2 Neo4j schema init smoke test: ontology-native constraints/indexes create successfully (`hs:Contact`, `hs:Company`, `hs:Engagement`, `hs:Assertion`, `hs:CaseContact`, `hs:CaseOpportunity`).
- [x] T6B.3 Integration smoke: `contacts/sync` writes `:CRMContact` + `:`hs:Contact`` and parallel `[:WORKS_AT]` + ``[:`hs:worksAt`]``.
- [x] T6B.4 Integration smoke: one interaction ingest writes `:CRMEngagement` + ontology engagement labels and parallel ``[:`hs:engagedWith`]``.
- [x] T6B.5 Assertion/evidence smoke: assertion write includes ``:`hs:Assertion` ``, ``:`hs:SourceArtifact` ``, ``[:`hs:sourceArtifact`]`` and ``[:`hs:extractionEvent`]``.

Test results (2026-02-23):

- `python -m pytest app/tests/test_ontology_runtime_contract.py -q` -> `5 passed`
- `scripts/init_neo4j_schema.py` applied new ontology-native constraints/indexes successfully (including `hs:Contact`, `hs:Company`, `hs:Deal`, `hs:Engagement`, `hs:Assertion`, `hs:SourceArtifact`, `hs:ExtractionEvent`, `hs:CaseContact`, `hs:CaseOpportunity`)
- Direct Neo4j write smoke via `queries.py` confirmed dual physical projection:
  - `CRMContact` node labels: `['CRMContact', 'hs:Contact']`
  - `CRMCompany` node labels: `['CRMCompany', 'hs:Company']`
  - `CRMEngagement` node labels: `['CRMEngagement', 'hs:Engagement', 'hs:Email']`
  - `KGAssertion` node labels: `['KGAssertion', 'hs:Assertion']`
  - `EvidenceChunk` node labels: `['EvidenceChunk', 'hs:SourceArtifact']`
  - `CaseContact` node labels: `['CaseContact', 'hs:CaseContact']`
  - `CaseOpportunity` node labels: `['CaseOpportunity', 'hs:CaseOpportunity']`
  - Parallel relationship types present: `WORKS_AT` + `hs:worksAt`, `ENGAGED_WITH` + `hs:engagedWith`, `hs:hasCaseContact`, `hs:hasCaseOpportunity`, `hs:targetsContact`, `hs:sourceArtifact`, `hs:extractionEvent`, `hs:derivedFromEngagement`

Acceptance criteria:

- [x] Core graph writes produce ontology-native labels/relationship types in Neo4j.
- [x] V2 labels/types remain readable only as transitional compatibility, not the sole physical representation.
- [x] Users can inspect the graph and directly observe ontology-native classes/properties for newly written data.

### Phase 8C: Read-Path Cutover to Ontology-Native Labels / Relationship Types (In Progress)

Problem addressed:

- User-visible score/case/draft context reads were still written against `CRM*` labels and V2 relationship types.
- Physical dual-write alone does not prove the runtime is actually reading the ontology-native projection.
- Some V2 relationship replacements in alternations (`[:A|B]`) were only partially converted.

Implementation checklist:

- [x] Add a centralized V2->ontology-native Cypher read transformer for labels/relationship types (excluding `HAS_ASSERTION`, which requires direction reversal).
- [x] Patch high-impact read queries in `apps/api/app/db/neo4j/queries.py`:
  - [x] score snapshots (`get_latest_score_snapshots`, `get_contact_score_snapshots`)
  - [x] contact claims/company hints/graph paths/graph metrics
  - [x] case contact/opportunity list + promotion pre-reads
  - [x] opportunity matching / open-case counts / context signals / evidence trace
  - [x] inference rule queries (relationship/type replacement)
  - [x] SHACL local validation queries (including manual reversal for `hs:derivedFromEngagement`)
- [x] Fix alternation replacement bug for relationship types in Cypher patterns (`[:A|B]` second token replacement).
- [x] Confirm no direct `CRM*` graph-label assumptions exist in `apps/ui` (UI uses API responses, not direct Neo4j Cypher).
- [ ] Migrate remaining write/query code to ontology-native primary merges (currently some writes still merge on V2 labels first, then add ontology labels).

Testing plan:

- [x] T6C.1 Compile check for `queries.py` and ontology runtime contract after read transformer + query changes.
- [x] T6C.2 Unit tests (ontology runtime contract + worker semantic gates) still pass after read-query cutover support code changes.
- [x] T6C.3 Query-layer ontology-only read smoke:
  - create a mini CRM/Case/Evidence subgraph via app writers
  - strip V2 labels + V2 relationship types from that subgraph
  - verify read functions still succeed using ontology-native labels/types
- [x] T6C.4 API smoke:
  - `/v1/scores/contact/{id}`
  - `/v1/cases/contacts?status=all`
  - `/v1/cases/opportunities?status=all`
- [ ] T6C.5 Repeat smoke after writer merge hardening (transition safeguards added for core writers; full ontology-native primary merge cutover still pending across all write paths).

Test results (2026-02-23):

- `python -m py_compile apps/api/app/db/neo4j/queries.py apps/api/app/services/ontology/runtime_contract.py` -> passed
- `pytest app/tests/test_ontology_runtime_contract.py app/tests/test_worker_semantic_claim_gates.py -q` -> `7 passed`
- Query-layer ontology-only read smoke passed after removing V2 labels/types from the test subgraph:
  - `get_latest_score_snapshots`, `get_contact_claims`, `get_contact_company_hint`
  - `get_contact_graph_paths`, `get_contact_graph_metrics`
  - `list_case_contacts_v2`, `list_case_opportunities_v2`
  - `get_contact_context_signals_v2`, `get_contact_assertion_evidence_trace_v2`
  - `run_shacl_validation_v2`
- API smoke passed:
  - `/v1/scores/contact/contact:test:readcut-75e1bc2a` returned score payload
  - `/v1/cases/contacts?status=all` returned provisional case contact item
  - `/v1/cases/opportunities?status=all` returned provisional case opportunity item
- Known gap discovered during repeated ontology-only smoke setup:
  - some writers (`merge_contact`, company/case upserts, etc.) still `MERGE` on V2 labels first and then add ontology labels
  - if a node exists with only ontology labels, re-running the same write can hit uniqueness conflicts on ontology-native constraints
  - next fix: make ontology-native labels the primary merge key (or adopt ontology-only nodes before V2 merge during transition)
- Transition hardening progress:
  - added adoption safeguards in core writers to reapply V2 labels from ontology-only nodes before V2 `MERGE` calls (contact/company/engagement/extraction event/assertion/evidence chunk/case contact/case opportunity + promotion paths)
  - focused rerun test passed (`merge_contact`, `merge_interaction`, `upsert_case_contact_v2` after stripping V2 labels from the same nodes)

Acceptance criteria:

- [x] High-impact read queries execute against ontology-native labels/relationship types.
- [x] Read functions continue to work when V2 labels/types are removed from the tested subgraph.
- [ ] All write/query paths are fully ontology-native primary (no V2-first merge assumptions remain).

### Phase 9: Temporal Semantics for Assertions / Opportunities / Scoring Inputs

Problem addressed:

- V2 assertions rely mostly on write-time `updated_at` rather than explicit asserted/observed validity semantics.
- Scoring and drafting need stronger time-aware weighting on newer information.

Implementation checklist:

- [ ] Add V2 assertion temporal properties (`asserted_at`, `observed_at`, optional `valid_from`, `valid_to`) and populate from interaction/extraction context.
- [ ] Add opportunity temporal properties (`stage_updated_at`, `last_meaningful_touch_at`, `next_step_due_at`) where missing.
- [ ] Update scoring and retrieval weighting to use temporal semantics over write-time fallbacks.
- [ ] Preserve backward compatibility when temporal fields are missing (graceful fallback to `occurred_at` / `updated_at`).
- [ ] Add contradiction/supersession handling hooks for time-sensitive facts (e.g., employer changes).

Testing plan:

- [ ] T7.1 Unit tests: assertion temporal defaults derive from interaction timestamp when explicit extraction time is absent.
- [ ] T7.2 Integration test: newer conflicting employer assertion outranks older one without deleting historical evidence.
- [ ] T7.3 Score regression test: recent supported signals outweigh stale signals in contact priority calculation.
- [ ] T7.4 Draft retrieval test: recency ordering in `assertion_evidence_trace` and `context_signals_v2` favors newer evidence.

Acceptance criteria:

- [ ] Time-aware behavior is visible in scores, next-step suggestions, and draft retrieval traces.
- [ ] Temporal fields are persisted and queryable in V2 graph assertions.

### Phase 10: Score / Priority / Next-Step Engine V3 (Remove Stubs)

Problem addressed:

- `/scores/*` summary and next-step outputs still rely on stubs/placeholder HubSpot text.
- Opportunity prioritization is weakly tied to actual `CaseOpportunity` / `CRMOpportunity` graph state.

Implementation checklist:

- [ ] Remove stub next-step generation from `apps/api/app/api/v1/routes/scores.py`.
- [ ] Implement graph-driven next-step generation based on opportunity stage, blockers, commitments, recency, and open loops.
- [ ] Add explicit `contact_priority_score` inputs from linked opportunities (`CRMOpportunity`, `CaseOpportunity`, unresolved-case penalties).
- [ ] Add `opportunity_priority_score` computation using stage/confidence/value/timing/stakeholder coverage signals.
- [ ] Add rationale payloads that reference evidence-backed signals (not just heuristic summaries).
- [ ] Ensure scores remain stable when no opportunity data exists (contact-only fallback).

Testing plan:

- [ ] T8.1 API test: `/v1/scores/contact/{id}` returns non-stub `priority_next_step` when evidence/opportunity signals exist.
- [ ] T8.2 API test: `/v1/scores/today` rankings change as interaction recency changes (time-aware).
- [ ] T8.3 Regression test: no “Stub:” text appears in score payloads under normal operation.
- [ ] T8.4 Integration test: unresolved `CaseOpportunity` increases contact priority but applies provisional confidence penalty.

Acceptance criteria:

- [ ] Score APIs return graph-driven, evidence-linked rationales and next steps.
- [ ] No user-facing stub next-step content remains.

### Phase 11: Opportunity Graph UX Cutover (Replace Synthetic UI Opportunities)

Problem addressed:

- UI “Priority Opportunities” is currently synthesized by grouping scored contacts by company rather than using `CaseOpportunity` / `CRMOpportunity`.

Implementation checklist:

- [ ] Add backend endpoint(s) for ranked opportunities (`CRMOpportunity` + open `CaseOpportunity`) with next-step/rationale payloads.
- [ ] Replace `apps/ui/components/priority-opportunities.tsx` synthetic company-grouping logic with real API-backed opportunities.
- [ ] Display canonical vs provisional status and promotion confidence in UI.
- [ ] Show opportunity-company/contact links from graph (not derived UI-only grouping).
- [ ] Ensure time-aware fields (last touch, next step due, stale indicator) are surfaced.

Testing plan:

- [ ] T9.1 UI/API integration test: homepage opportunity list renders real `CRMOpportunity` / `CaseOpportunity` records.
- [ ] T9.2 UI regression test: no opportunities are invented from contact score grouping when opportunity graph is empty.
- [ ] T9.3 API test: ranked opportunities include rationale and next-step payload consistent with score engine V3.
- [ ] T9.4 End-to-end test: interaction linked to existing opportunity updates its ranking and surfaced next step.

Acceptance criteria:

- [ ] “Priority Opportunities” in UI is backed by the opportunity graph, not synthetic grouping.

### Phase 12: Cases UI (Provisional Contact / Opportunity Review & Promotion)

Problem addressed:

- `/cases` API exists, but the UI does not expose provisional contact/opportunity review, approval, edit, reject, or promotion flow.

Implementation checklist:

- [ ] Add UI pages/components for open `CaseContact` and `CaseOpportunity` queues.
- [ ] Support review/edit before promotion (display/edit basic fields, company, motivators, gate results).
- [ ] Support promote/reject actions via `/v1/cases/*/promote` (and add reject endpoints if missing).
- [ ] Surface evidence/provenance trace and gate results in the review UI.
- [ ] Add merge guidance for provisional contact duplication vs existing canonical contact.

Testing plan:

- [ ] T10.1 UI/API integration test: list open provisional contacts and opportunities.
- [ ] T10.2 Promotion flow test: promoting a case contact updates Postgres `ContactCache` and V2 graph links.
- [ ] T10.3 Promotion flow test: promoting a case opportunity creates/updates `CRMOpportunity` and retains provenance.
- [ ] T10.4 Negative test: blocked promotion displays gate failure reason and does not mutate canonical entities.

Acceptance criteria:

- [ ] Users can review and approve provisional contacts/opportunities in UI with provenance.

### Phase 13: Drafting UX + Opportunity-Linked Objective + Provenance Review

Problem addressed:

- Draft objective suggestion is contact-only and not explicitly tied to selected opportunities/cases.
- User cannot review missing provisional data inline before drafting.

Implementation checklist:

- [ ] Extend draft objective suggestion to accept optional `opportunity_id` / `case_opportunity_id`.
- [ ] Update drafting retrieval bundle to prioritize opportunity-linked signals when an opportunity is selected.
- [ ] Expose provenance/evidence trace in the drafts UI with clear source snippets and timestamps.
- [ ] Add UI controls to select target opportunity / suggested objective before draft generation.
- [ ] Add UI affordance to capture missing facts (provisional detail note) prior to drafting and persist as provisional evidence/case context.

Testing plan:

- [ ] T11.1 API test: `/v1/drafts/objective_suggestion` returns opportunity-linked objective when opportunity is provided.
- [ ] T11.2 API/UI test: generated draft payload includes provenance trace and selected opportunity context.
- [ ] T11.3 Regression test: draft generation excludes unsupported/unproven claims.
- [ ] T11.4 UX flow test: user can revise/add missing context and regenerate a draft.

Acceptance criteria:

- [ ] Drafts are explicitly tied to a contact + objective + (optional) opportunity and show provenance.

### Phase 14: News Ingestion + V2 Contact Relevance + Touchpoint Suggestions

Problem addressed:

- News matching uses legacy `Contact`/`Claim` graph and does not persist article content/evidence.
- News UI lacks file upload/URL ingestion workflow.

Implementation checklist:

- [ ] Persist uploaded/pasted news article (or URL-fetched content) into Postgres + Evidence Graph (`SourceArtifact` / `EvidenceChunk` mapping).
- [ ] Rewrite news matcher to use V2 graph (`CRMContact`, `KGAssertion`, `CRMOpportunity`, `CaseOpportunity`) instead of legacy `Contact`/`Claim`.
- [ ] Include motivator/opportunity relevance and recency-aware scoring in news-contact matching.
- [ ] Add UI support for article upload and/or URL submit with preview.
- [ ] Generate suggested outreach touchpoint objective linked to relevant contact/opportunity.

Testing plan:

- [ ] T12.1 API test: `/v1/news/match` persists article and returns V2-backed relevance matches.
- [ ] T12.2 Matcher regression test: no legacy `Contact`/`Claim` query dependency remains.
- [ ] T12.3 UI flow test: upload/paste article -> matches -> choose contact -> seed draft objective.
- [ ] T12.4 Relevance test: recent opportunity-related signals increase match ranking vs stale generic signals.

Acceptance criteria:

- [ ] News workflow is evidence-backed, V2-graph-driven, and feeds outreach planning/drafting.

### Phase 15: n8n Workflow Hardening + Backfill Metadata Reliability

Problem addressed:

- `gmail_contact_backfill` still relies on metadata hydration fallback due Gmail node paired-item metadata loss.
- n8n workflow duplication / JSON corruption / import drift can break operational workflows.
- Secret handling and payload serialization issues previously caused silent failures.

Implementation checklist:

- [ ] Eliminate paired-item metadata dependence in `gmail_contact_backfill` by carrying explicit contact metadata through deterministic payload fields.
- [ ] Add workflow self-check/fail-loud guards for empty `rows`, missing `contact_id`, missing `primary_email`, and invalid POST body shapes.
- [ ] Add workflow export validation script for `n8n/workflows/*.json` (required headers, body modes, hardcoded/credential secret policy).
- [ ] Add operator runbook steps for n8n DB reset/import/credential rebind verification.
- [ ] Add small-batch and bulk-backfill profiles with documented throughput/quality settings.

Testing plan:

- [ ] T13.1 n8n `sheets_sync` test: non-empty rows produce `upserted > 0` and writes verified in Postgres + Neo4j.
- [ ] T13.2 n8n `gmail_contact_backfill` test: `contact_id` and `primary_email` survive end-to-end into ingest payload without lookup hydration fallback.
- [ ] T13.3 Duplicate reprocess test: raw event payload updates correctly and backfill mode is preserved.
- [ ] T13.4 Workflow export validation script test against all repo workflows.

Acceptance criteria:

- [ ] Backfill payload metadata is deterministic and testable without n8n runtime quirks.

### Phase 16: Graph Hygiene Diagnostics, Monitoring, and Legacy Purge Completion

Problem addressed:

- No continuous graph hygiene diagnostics for duplicate contacts, multi-employer conflicts, topic-noise ratio, unsupported predicates.
- Legacy graph labels/edges may persist and confuse debugging if not actively monitored or purged.

Implementation checklist:

- [ ] Add graph hygiene diagnostic endpoint/report (duplicate contacts, multi-employer conflicts, topic-noise ratio, unsupported predicate ratio, missing temporal fields).
- [ ] Add scheduled inference + validation + hygiene monitoring jobs with persisted results and DLQ visibility.
- [ ] Add production health checks for strict Cognee mode (`extraction_failure`, queue lag, timeout/error rates).
- [ ] Finalize optional legacy purge script usage and add parity gate before purge.
- [ ] Execute and record final legacy purge validation (`Contact`, `Entity`, `RELATES_TO`, `Claim`, `Evidence`, `Interaction`, `ScoreSnapshot`).

Testing plan:

- [ ] T14.1 Diagnostic endpoint test on clean graph and noisy fixture graph.
- [ ] T14.2 Monitoring test: simulated extraction failure appears in diagnostics/alerts.
- [ ] T14.3 Legacy purge dry-run + apply test with parity checks before/after.
- [ ] T14.4 Post-purge regression: scores/drafts/news/cases function without legacy labels.

Acceptance criteria:

- [ ] Operators can detect graph drift/noise quickly and the runtime no longer depends on legacy graph projection.

### Phase 0: Freeze + Baseline

- [ ] Capture current graph profile snapshot (labels, rel types, counts, noise ratios)
- [ ] Capture current runtime config flags (`COGNEE_ENABLE_HEURISTIC_FALLBACK`, `GRAPH_V2_*`, SHACL flags)
- [ ] Identify duplicate source contacts by email in sheet sync payload

### Phase 1: Source Hygiene + Contact Sync Hardening

- [x] Add backend contact-row dedupe by `primary_email` before upsert (deterministic winner selection)
- [x] Prefer company values that match email domain when duplicate source rows conflict
- [x] Ensure contact sync upsert does not leave contradictory legacy accepted `works_at` relations for same contact
- [x] Preserve V2 canonical `CRMContact -> WORKS_AT -> CRMCompany` as source of truth

### Phase 2: Extraction Noise Suppression

- [x] Add topic claim quality filters (stopwords/header words/single-token low-signal labels)
- [x] Prevent trivial heuristic fallback tokens from becoming CRM assertions
- [x] Keep evidence capture, but quarantine/reject low-value semantic claims from promotion-sensitive paths

### Phase 3: Case/Opportunity Gating

- [x] Stop using generic `topic` claims for `company_hint`
- [x] Stop using generic `topic` claims for motivator extraction
- [x] Require explicit opportunity/commitment/action evidence before opening `CaseOpportunity`
- [x] Skip case opportunity creation on low-signal interactions (greetings/catch-up/no-op updates)

### Phase 4: Scoring / Retrieval Read-Path Hardening

- [x] Exclude stale/superseded employer relations from legacy graph path traversal
- [x] Reduce/disable topic-noise predicates (`discussed_topic`, `contains`) in scoring graph paths/metrics
- [x] Finish V2 scoring/drafting read-path cutover (`graph_v2_read_v2=true`)
- [x] Validate V2 read-path parity against legacy outputs on sample contacts
- [x] Set `GRAPH_V2_DUAL_WRITE=false` after parity (legacy writes off)
- [x] Stop using legacy graph traversal in UI-facing score/draft query paths
- [x] Prepare optional legacy graph purge (`Contact`, `Entity`, `RELATES_TO`, `Claim`, `Evidence`, `Interaction`, `ScoreSnapshot`)

### Phase 5: Validation + Runtime Guards

- [x] Fix SHACL gatekeeper import prefix issue(s)
- [x] Enable SHACL validation in write pipeline (at least in staging/test)
- [x] Add semantic guards beyond SHACL (claim quality and opportunity gate checks)
- [ ] Add graph hygiene diagnostic queries/report (duplicate contacts, multi-employer conflicts, topic-noise ratio)

### Phase 6: Cleanup + Rebuild Validation

- [x] Reset Postgres interaction-derived data + Neo4j graph (keep schema/migrations)
- [x] Reload ontology + SHACL
- [x] Re-run `sheets_sync`
- [x] Re-run targeted `gmail_contact_backfill` sample
- [x] Verify no duplicate canonical contacts by email
- [x] Verify no multi-company current employer links for canonical contacts
- [x] Verify `CaseOpportunity` creation only on supported signals
- [x] Verify scores/path samples are not dominated by topic noise or stale employer edges

## Test Matrix (to execute during refactor)

### T1: Contact Sync Duplicate-Email Conflict Handling

- Goal: duplicate rows for same email do not create contradictory current employers
- Steps:
  - run `sheets_sync`
  - inspect `contact_cache` merged result for duplicate email case (David Craig)
  - inspect `CRMContact WORKS_AT` edges
  - inspect legacy `RELATES_TO works_at` edges for same contact
- Pass criteria:
  - one canonical `CRMContact` per email
  - one effective current employer in V2
  - no active legacy contradictory accepted `works_at` relation for scoring

### T2: Backfill Noise Suppression on Low-Signal Emails

- Goal: greetings/catch-up emails do not create false case opportunities or noisy graph claims used by scoring
- Steps:
  - run targeted `gmail_contact_backfill` (small sample)
  - process interactions
  - inspect `CaseOpportunity`, `KGAssertion`, `RELATES_TO`
- Pass criteria:
  - no `CaseOpportunity` with titles like greeting/subject-only noise absent opportunity evidence
  - low-signal topic claims filtered/quarantined from promotion-sensitive logic

### T3: Score Path Sanity

- Goal: graph path explanation no longer routes through stale employer edge
- Steps:
  - compute/inspect score snapshot components for contact `17`
  - inspect path samples
- Pass criteria:
  - no stale `PwC` path for David once canonical employer is `TNFD`
  - path samples emphasize high-value relations over topic noise

### T4: Graph Hygiene Snapshot

- Goal: graph structure becomes CRM-usable after reset + rerun
- Steps:
  - count labels and relationship types
  - measure topic-noise ratio in `KGAssertion`
  - inspect `CaseContact`, `CaseOpportunity`, `CRMOpportunity`
- Pass criteria:
  - V2 nodes/relations are meaningful and not swamped by noisy topics
  - case entities reflect evidence-backed provisional workflow

## Execution Results (fill during implementation)

### Baseline (Before Refactor)

- [x] Graph profile captured: `434` nodes / `747` rels
- [x] Dominant labels: `Entity`, `Claim`, `Evidence`, `KGAssertion`
- [x] Dominant rel type: `RELATES_TO` (`118`)
- [x] `KGAssertion` mostly topic noise (`78`, all `topic`)
- [x] False `CaseOpportunity` examples observed (`Greetings!`/`David`, `Catching-up`/`Address`)
- [x] Duplicate sheet rows confirmed for `david.craig@tnfd.global` (`PwC` + `TNFD`)
- [x] Legacy contradictory employer relations confirmed for David (`PwC`, `TNFD`)

### Refactor Test Results

- [x] Phase 7 (Ontology Authority) completed
  - Code changes:
    - Added TTL-backed runtime ontology contract + mapping/drift diagnostics in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/services/ontology/runtime_contract.py`
    - Exported runtime ontology helpers from `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/services/ontology/__init__.py`
    - Stamped V2 graph writes with ontology metadata (`ont_class`, `ont_predicate`) in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/db/neo4j/queries.py`
    - Documented alias strategy + TTL authority in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/services/ontology/README.md`
  - T5.1/T5.2/T5.4 unit tests:
    - Ran `docker compose exec -T api sh -lc 'cd /workspace/apps/api && /opt/venv/bin/python -m pytest app/tests/test_ontology_runtime_contract.py -q'`
    - Result: `4 passed in 0.06s`
  - T5.3 integration smoke:
    - `POST /v1/contacts/sync` (`upserted: 1`) for synthetic contact `phase7_ont_test_contact`
    - `POST /v1/ingest/interaction_event` for synthetic Gmail event (`status: enqueued`)
    - Verified V2 graph ontology metadata via Neo4j query:
      - `CRMContact.ont_class = "hs:Contact"`
      - `CRMEngagement.ont_class = "hs:Email"`
      - `CRMEngagement.ont_class_base = "hs:Engagement"`
      - `ENGAGED_WITH.ont_predicate = "hs:engagedWith"`
      - `CRMCompany.ont_class = "hs:Company"`
      - `WORKS_AT.ont_predicate = "hs:worksAt"`
    - Note: no `KGAssertion` was created in this smoke run (`assertion_ont_classes=[]`), so assertion ontology metadata remains covered by unit-level write-path checks and will be exercised in later extraction/opportunity phases.
  - Drift diagnostics sample:
    - `ontology_config_drift_report()` returned `valid=False` with `unknown_count=8`
    - Sample unsupported predicates mapped from `ontology_config.json`: `hs:committedTo`, `hs:discussedTopic`, `hs:hasEducationDetail`, `hs:hasFamilyDetail`, `hs:hasOpportunity`, `hs:hasPersonalDetail`, `hs:hasPreference`, `hs:relatedTo`

- [ ] Phase 8 (Ontology-Aligned Claim Taxonomy + Semantic Promotion Gates) in progress
  - Implemented so far:
    - Claims now carry `ontology_predicate`, `ontology_supported`, and `promotion_scope` from `apps/api/app/services/ontology/mapper.py`
    - Unsupported predicates are blocked from relation projection (`relation_payload_from_claim()` returns `None`)
    - Worker now distinguishes evidence/case claims vs CRM-promotable claims (`_filter_crm_promotable_claims`) and uses ontology-supported claims for company hints / relation persistence
    - `KGAssertion` writes now persist `ontology_supported` and avoid stamping unsupported `ont_predicate` values as canonical ontology terms
    - Fixed low-signal text bug that incorrectly rejected uppercase/mixed-case organization acronyms (`TNFD`, `PwC`)
  - Phase 8 tests completed so far:
    - `docker compose exec -T api sh -lc 'cd /workspace/apps/api && /opt/venv/bin/python -m pytest app/tests/test_ontology_mapper_semantic_gates.py app/tests/test_cognee_mapper.py app/tests/test_worker_semantic_claim_gates.py -q'`
    - Result: `5 passed in 0.48s`
  - Remaining before Phase 8 completion:
    - domain/range-aware normalization from ontology contract
    - promotion gates aligned to `.CODEX/Ontology_design.md` (confidence/recency/status)
    - worker integration tests T6.3/T6.4 (low-signal + explicit opportunity/employment scenarios)

- [x] T1 completed
  - Result: `sheets_sync` on clean reset returned `{"mode":"push","upserted":39}`. Postgres `contact_cache` contained `39` rows with no duplicate `primary_email`. `david.craig@tnfd.global` resolved to canonical `contact_id=35`. Neo4j showed one V2 employer (`TNFD`) and one accepted legacy `works_at` edge (`TNFD`) for David; no active `PwC` employer edge remained.
- [x] T2 completed
  - Result: Targeted `gmail_contact_backfill` (David-only sample, 3 emails) processed successfully on the patched local-source worker. No `CaseOpportunity` nodes were created (`case_opp_count=0`) for `Greetings!` / `Catching-up` emails. No topic/noise `KGAssertion` rows were created (`KGAssertion total = 0`) in this sample.
- [x] T3 completed
  - Result: `GET /v1/scores/contact/35` returned clean graph paths with `TNFD` only (`David Craig -[works_at]-> TNFD`, plus TNFD coworkers). No stale `PwC` path appeared. Graph metrics were reduced to the expected contact-sync relation footprint (`graph_relation_count=1`, `graph_path_count_2hop=4`) and `open_case_opportunities=0`.
- [x] T4 completed
  - Result: Post-reset/rebuild sample graph remained CRM-usable: no duplicate `CRMContact` emails, no multi-company `WORKS_AT` conflicts, no `CaseOpportunity` false positives, and no V2 assertion noise in the David sample. `CRMOpportunity` count remained `0` (expected for this non-opportunity sample).
- [x] V2 cutover validation (clean reset + V2-only writes)
  - Result: After fixing an initial cutover regression (V2 writes were incorrectly gated on `GRAPH_V2_DUAL_WRITE`), `sheets_sync` on a clean graph produced `CRMContact=39` with `legacy Contact=0`. Post-backfill + worker processing produced a clean V2 graph profile (`CRMContact`, `CRMCompany`, `CRMEngagement`, `CaseContact`, `ScoreSnapshot`) with no legacy `Entity/RELATES_TO/Claim/Evidence/Interaction` growth.
- [x] V2 scoring/draft read-path smoke tests
  - Result: `GET /v1/scores/contact/35` and `GET /v1/scores/today` succeeded using V2 graph paths (`David Craig -[works_at]-> TNFD ...`) with no stale `PwC` path. `POST /v1/drafts` for contact `35` returned `status=proposed` and a valid retrieval trace/context summary under V2 read mode.
- [x] SHACL hard-gate runtime enablement verified
  - Result: `.env` now explicitly sets `GRAPH_V2_DUAL_WRITE=false`, `GRAPH_V2_READ_V2=true`, `SHACL_VALIDATION_ENABLED=true`, `SHACL_VALIDATION_ON_WRITE=true`. Verified via live `api` and `worker` `get_settings()` checks after restart (`False / True / True / True`).
- [x] n8n CLI/workflow DB repair + reload
  - Result: Repaired corrupted `n8n/database.sqlite` via `sqlite3 .recover`, then rebuilt workflow definitions by clearing `workflow_entity`/`shared_workflow` and re-importing repo workflows. `docker compose run --rm -T n8n list:workflow` now succeeds and returns the 10 repo workflows.
- [x] n8n workflow credential relink (Gmail + Google Sheets)
  - Result: Reattached imported `gmail` / `googleSheets` nodes to existing credentials (`Gmail account`, `Google Sheets account`) by patching `workflow_entity.nodes`. `sheets_sync` one-off run succeeded post-reimport (`upserted: 39`).
- [x] `gmail_contact_backfill` hint recovery after Gmail node (n8n)
  - Result: Direct paired metadata propagation remains unreliable in this n8n runtime, but `POST Interaction` now hydrates missing `contact_id` / `primary_email` via `/v1/contacts/lookup` using participant emails and prefers canonical target contacts based on message direction. Verified in n8n execution output (`POST Interaction.meta.contact_id` populated, canonical non-provisional contact selected).
- [x] Internal CRM-user identity separation (proper fix, not sheet workaround)
  - Code changes:
    - Added internal identity classifier (`INTERNAL_EMAIL_DOMAINS` + explicit `INTERNAL_USER_EMAILS`) in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/services/identity/internal_users.py`.
    - Added `internal_user_emails` setting in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/core/config.py`.
    - Updated `/v1/contacts/lookup` to suppress resolution task creation for internal emails in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/api/v1/routes/contacts.py`.
    - Updated contact sync to:
      - skip internal rows as CRM contacts,
      - purge stale internal rows from `contact_cache`,
      - create/update `hs:InternalUser`,
      - purge stale internal `CaseContact` graph nodes by email
      in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/services/contacts_registry/sync.py`.
    - Added graph helpers for internal users and role-specific engagement links (`hs:authoredBy`, `hs:sentTo`, `hs:ccTo`) and case-contact deletion by email in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/db/neo4j/queries.py`.
    - Updated worker participant resolution to exclude internal emails from `contact_ids_json` / provisional case-contact creation and attach them as `hs:InternalUser` engagement participants in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/workers/jobs.py`.
  - Validation:
    - Recreated `api` + `worker` containers (required to reload `env_file`; `restart` alone does not refresh env vars).
    - Verified runtime settings inside container:
      - `internal_user_emails='danobrien99@gmail.com,daniel@obrien-sustainability.com'`
      - `is_internal_email(...)` returns `True` for both aliases.
    - Triggered `POST /v1/contacts/sync` (push mode):
      - First run returned `stale_internal_contacts_purged=2` (Daniel aliases removed from `contact_cache`)
      - Second run returned `stale_internal_case_contacts_purged=2` (Daniel stale `CaseContact` nodes removed)
    - Postgres checks:
      - `contact_cache` contains no rows for `danobrien99@gmail.com` / `daniel@obrien-sustainability.com`
    - Neo4j checks:
      - `hs:Contact` count for Daniel aliases = `0`
      - `hs:CaseContact` count for Daniel aliases = `0`
      - `hs:InternalUser` count for Daniel aliases = `2`
    - Interaction ingest smoke (`manual` email sent from Daniel -> external contact):
      - `interaction.contact_ids_json = ['test_ext_001']` (external-only)
      - Neo4j engagement links show `hs:authoredBy -> hs:InternalUser(daniel@...)`
      - `hs:engagedWith -> hs:Contact(external.test@example.com)`
    - `/v1/contacts/lookup` behavior:
      - internal Daniel email returns `contact_id=null`, `resolution_task_id=null`
      - external contact returns canonical `contact_id`
    - `/v1/scores/today` behavior:
      - Daniel aliases not present in ranked contacts list

## Notes / Follow-ups

- `gmail_contact_backfill` paired metadata propagation through the Gmail node remains unreliable in the current n8n runtime. This is now mitigated in the workflow by hydrating missing hints in `POST Interaction` via `/v1/contacts/lookup` (canonical-preferred, direction-aware).
- `worker` was initially importing the packaged image copy (`/opt/venv/.../site-packages/app`) instead of the bind-mounted source. Added `PYTHONPATH=/workspace/apps/api` to `api` and `worker` in `/Users/dobrien/code/Lux/Lux_CRM_agent/docker-compose.yml`, then reran validation on the local-source worker. This was the key reason the first validation still showed noisy `KGAssertion` and false `CaseOpportunity` nodes.
- n8n DB repair (`.recover`) required a workflow re-import because recovered `workflow_entity` JSON fields were blanked. This recreated workflows with new IDs and temporarily removed credential bindings; Gmail/Google Sheets bindings were restored automatically by DB patch.
- Optional legacy purge helper script added: `/Users/dobrien/code/Lux/Lux_CRM_agent/scripts/purge_legacy_graph_projection.py` (`--apply` to execute, default dry-run).
- `COGNEE_ENABLE_HEURISTIC_FALLBACK=true` is still enabled in the current `.env` and can reintroduce noisy extractions under runtime failures. For production-quality graph hygiene, switch back to `false` after operational monitoring is in place.
- Remaining follow-up: add a dedicated graph hygiene diagnostic endpoint/report (duplicate contacts, multi-employer conflicts, topic-noise ratio) for continuous monitoring.

- [x] Final physical cutover to ontology-native labels/relationship types (application graph)
  - Code changes:
    - Added centralized Neo4j `_session_run(...)` wrapper in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/db/neo4j/queries.py` that rewrites V2 `CRM*` / `WORKS_AT` / `ENGAGED_WITH` / case/evidence score rels to ontology-native `hs:*` identifiers when `GRAPH_V2_DUAL_WRITE=false`.
    - Protected existing escaped ontology identifiers from double-rewrite collisions (fix for invalid Cypher like ``SET c:`hs:`hs:CaseContact```).
    - Disabled V2-label adoption helper when dual-write is off (`_adopt_ontology_node_into_v2_label` no-op in ontology-only physical mode).
    - Suppressed V2-only `HAS_ASSERTION` edge creation in ontology-only physical mode (ontology-native `hs:derivedFromEngagement` remains).
    - Added ontology-native score schema mapping: `ScoreSnapshot -> hs:ScoreSnapshot`, `HAS_SCORE -> hs:hasScore` in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/services/ontology/runtime_contract.py`.
    - Extended `/Users/dobrien/code/Lux/Lux_CRM_agent/.CODEX/CRM_ontoology_spec.ttl` with `hs:ScoreSnapshot` and `hs:hasScore`.
    - Added Neo4j ontology-native score snapshot uniqueness constraint in `/Users/dobrien/code/Lux/Lux_CRM_agent/apps/api/app/db/neo4j/schema.py`.
  - Validation (clean reset + `sheets_sync` + `gmail_contact_backfill` 3-contact sample):
    - Cleared Neo4j + Postgres app tables, reloaded schema/ontology/SHACL, reran `sheets_sync` and 3-contact Gmail backfill.
    - Explicit zero-check in Neo4j for V2 projection artifacts returned all zero:
      - `CRMContact=0`, `CRMCompany=0`, `CRMEngagement=0`, `ScoreSnapshot=0`
      - `WORKS_AT=0`, `ENGAGED_WITH=0`, `HAS_SCORE=0`
    - Ontology-native graph labels/relationship types present on live sample:
      - labels: `hs:Contact`, `hs:Company`, `hs:Engagement`, `hs:Email`, `hs:Assertion`, `hs:ExtractionEvent`, `hs:SourceArtifact`, `hs:CaseContact`, `hs:ScoreSnapshot`
      - rels: `hs:worksAt`, `hs:engagedWith`, `hs:assertionObject`, `hs:derivedFromEngagement`, `hs:extractionEvent`, `hs:sourceArtifact`, `hs:hasCaseContact`, `hs:targetsContact`, `hs:hasScore`
    - Note: n10s system labels (`_GraphConfig`, `_NsPrefDef`, `_n10sValidatorConfig`) remain and are expected.

- Prompt for recent code review/ refactor: I'm finding that the CRM is not behaving as I had intended. Here are the key outcomes/behaviours that I am trying to achieve: 1) Review contact list and match interactions (emails/transcripts etc) with contacts. 2) Extract information from interactions about the contact. This could be company they work for, relationship to other contacts,  information related to a live opportunity/deal associated with the company or the contact, and details about the person so that email drafts are more personalized. 3) Keep a score on relationship strength and priority based on content of interactions and opportunity/deal priority. 4) When parsing interaction information, identify association with existing opportunities, or create suggested new opportunities. This should also infer the next step for the contact and opportuity. 5) In the UI, provide the user with the top ranked opportunities and contacts, with suggested next step for moving the opportunities closer to a deal. This needs to ensure that the data extracted from the interaction is time aware (need to give greater weight to newer information). 6) In the UI, allow the user to generate a draft email based on a suggested objective/next step that links back to the opporuntity. The UI should also allow the user to review and approve (and add additional info where missing) for provisional new contacts and provisional new opportunities. The UI should also allow for users to upload a news article and find which contacts the news would be most relevannt, and then use this as a way to enhance their email touchpoint with the contact. The key design feature is the use of knowledge graphs to enable the extraction of information based on interactions and to allow for context awareness by extracting the most relevant information relevant to a contact or opportunity to provide the LLM when infering next step, priority, content for email drafts, etc.  Please review the entire code base and consider how best to enhance it to meet these specific objectives. I've also noticed that the resulting graph does not seem to be using the ontology I specified in .CODEX/CRM_ontoology_spec.ttl. Please review the code base and previous chat history to determine why this is the case. 
