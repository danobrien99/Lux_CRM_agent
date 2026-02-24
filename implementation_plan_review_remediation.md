# Lux CRM Remediation Implementation Plan (UX, Trust, Intelligence)

Date: 2026-02-24
Scope: Remediate gaps identified in the code review to align the CRM with intended behavior (contact matching, extraction, scoring, opportunity linkage, next-step guidance, evidence-backed drafting, and human review workflows).

This plan is intentionally phase-gated:
- `P0 UX Alignment` must complete before `P0/P1 Trust Alignment`.
- `P0/P1 Trust Alignment` must complete before `P1 Intelligence Quality`.
- No phase advances without passing its explicit test/acceptance gate.

## Review Findings Covered (Issue IDs)

- `R1` Stubbed next-step generation in `/scores` (including LLM output coercion to `Stub:`)
- `R2` Synthetic "Priority Opportunities" UI (company-grouped contacts, not real opportunities)
- `R3` Provisional contact/opportunity review and resolution actions not surfaced in UI
- `R4` News matching graph candidate generation reads legacy graph labels under v2 mode
- `R5` Time-aware graph path ranking bug (older context can sort ahead of newer)
- `R6` Worker filters out topic/relationship-signal context before graph persistence
- `R7` Claim provenance/citations too coarse (shared chunk refs; weak paragraph evidence mapping)
- `R8` Drafting trust/policy misalignment (`allow_sensitive` conflated with uncertain/provisional context; v2 accepted claims gap; motivator filtering)
- `R9` Contact matching is email-only (weak transcript/speaker resolution)
- `R10` Contradiction detection is too narrow (employment only)

## Global Execution Rules (Applies to All Phases)

- [ ] Work only in additive/non-destructive migrations and feature-safe changes. Avoid breaking current ingestion paths while introducing new UX/API shapes.
- [ ] Maintain evidence/provenance non-negotiables for all new draft/scoring outputs.
- [ ] For any API schema changes, update backend schemas, UI clients, and tests in the same phase before closing tasks.
- [ ] For any ranking/scoring logic changes, preserve explainability payloads and include machine-readable `reason`/`evidence_refs`.
- [ ] Run phase tests on a clean dev environment (or documented known state) and record pass/fail evidence in PR notes.
- [ ] Do not proceed to the next phase if any blocking test listed in the phase gate fails.

## Pre-Phase 0: Test Harness and Fixture Baseline (Blocking Prerequisite)

Reason: The code review attempted tests but `pytest` was unavailable. The remediation work requires repeatable verification gates.

### P0.0 Test Harness Bootstrap Checklist

- [x] Install and document local test runner prerequisites for `apps/api` (at minimum `pytest`, `pytest-cov`, and any currently missing runtime test deps).
- [x] Confirm `apps/api` tests run from repo docs or add a short local test command section to this plan/PR notes.
- [ ] Add or verify deterministic fixture generation for:
  - [ ] Contacts with multiple companies / provisional contacts
  - [ ] Interactions containing opportunity signals and commitment language
  - [x] News article matching scenarios
  - [x] Drafting evidence/citation scenarios
  - [x] Transcript speaker-only (name without email) scenarios
- [x] Add a lightweight "phase smoke" test marker set (e.g. `-m phase_smoke`) or a documented explicit test file list.

### P0.0 Testing Procedure (Must Pass Before Phase 1)

- [x] `cd apps/api && pytest -q app/tests/test_scoring.py app/tests/test_news_match.py app/tests/test_drafting.py`
- [x] Add and run one new smoke test proving the test environment can exercise Neo4j-query-dependent code with mocks/stubs.
- [x] Record exact commands and pass results in the work log / PR description.

### P0.0 Exit Gate

- [x] `pytest` runs successfully in `apps/api`.
- [x] All baseline smoke tests pass.
- [x] Fixture strategy is documented for the three remediation phases.

### P0.0 Execution Log (Local)

- [x] `cd apps/api && .venv/bin/python -m pytest -q app/tests/test_scoring.py app/tests/test_news_match.py app/tests/test_drafting.py` -> `8 passed`
- [x] `cd apps/api && .venv/bin/python -m pytest -q -m phase_smoke app/tests/test_phase_smoke_graph_v2_queries.py` -> `1 passed`
- [x] `cd apps/api && .venv/bin/python -m pytest -q app/tests/test_score_routes.py app/tests/test_cases_routes.py app/tests/test_resolution_routes.py` -> pass
- [x] `cd apps/ui && npm run build` -> pass

---

## Phase 1: P0 UX Alignment

Goal: Align what the user sees and can act on with actual graph/case/opportunity data instead of placeholders and synthetic views.

### Issues Addressed in This Phase

- [x] `R1` Stubbed next-step generation in `/scores`
- [x] `R2` Synthetic priority opportunities UI
- [x] `R3` Missing UI for provisional contact/opportunity review + resolution actions

### Workstream 1A: Replace Stub Next-Step UX in Scores/Contact Views (`R1`)

#### Backend (`/scores`) checklist

- [x] Introduce a structured next-step payload for score/detail responses (example fields: `summary`, `type`, `contact_id`, `opportunity_id`, `evidence_refs`, `source`, `confidence`).
- [x] Remove hardcoded `_stub_priority_next_step(...)` as the primary source for user-visible next-step text.
- [x] Stop forcing LLM next-step output to `Stub:` prefixes.
- [x] Ensure the fallback path is still explicit, but clearly marked as `heuristic` (not "stub"), with evidence and confidence.
- [x] Derive next-step candidates from available graph/case/opportunity context first:
  - [x] linked open opportunity (promoted)
  - [x] open provisional case opportunity
  - [x] open inbound thread / open loops
  - [x] recent graph signals and commitments
- [x] Preserve `priority_next_step_source` only if still needed, but align values to real modes (`graph`, `heuristic`, `llm`, `case_opportunity`, `opportunity_thread`).

#### UI checklist

- [x] Update `/contact/[contactId]` page to display the structured next-step data (not raw stub strings).
- [x] Show next-step evidence summary (minimum: referenced interaction IDs / chunk IDs count).
- [x] Display fallback mode when the next step is heuristic.

### Workstream 1B: Replace Synthetic "Priority Opportunities" with Real Data (`R2`)

#### Backend checklist

- [x] Create a real opportunity ranking endpoint for the homepage (recommended: `GET /v1/scores/opportunities` or `GET /v1/cases/opportunities/ranked`).
- [x] Rank using real graph entities (`CRMOpportunity` and/or `CaseOpportunity`) instead of company-grouped contacts.
- [x] Include fields required for UI cards:
  - [x] `opportunity_id` / `case_id`
  - [x] `title`
  - [x] `company_name`
  - [x] `status` and `entity_status`
  - [x] `priority_score`
  - [x] `next_step`
  - [x] `linked_contacts`
  - [x] `reason_chain` / explainability
- [x] Include time-aware weighting in ranking inputs (recent engagement recency, recent signals, stale penalty).
- [x] Provide stable sorting and deterministic tie-breakers.

#### UI checklist

- [x] Refactor `/components/priority-opportunities.tsx` to consume real opportunity/case API payloads.
- [x] Remove synthetic value/likelihood/stage derivation based on contact scores.
- [x] Render promoted opportunities and provisional case opportunities distinctly (badge/label).
- [x] Add links from opportunity cards to relevant contact pages and provisional review actions.
- [x] Preserve pagination/filtering UX, but based on real fields.

### Workstream 1C: Expose Provisional Review and Promotion Workflows in UI (`R3`)

#### Cases UI checklist

- [x] Add a new `/cases` page (or split `/cases/contacts` and `/cases/opportunities`) in Next.js.
- [x] Add nav link in `TopNav`.
- [x] Display provisional contacts from `GET /v1/cases/contacts`.
- [x] Display provisional opportunities from `GET /v1/cases/opportunities`.
- [x] Add promote actions:
  - [x] `POST /v1/cases/contacts/{case_id}/promote`
  - [x] `POST /v1/cases/opportunities/{case_id}/promote`
- [x] Show gate results / evidence counts / promotion_reason to support manual review.

#### Resolution UI checklist

- [x] Upgrade `/resolution` page from read-only list to actionable queue.
- [x] Render `payload_json` details (current/proposed claim summaries, evidence pointers).
- [x] Add action controls for:
  - [x] `accept_proposed`
  - [x] `reject_proposed`
  - [x] `edit_and_accept`
- [x] Submit to `POST /v1/resolution/tasks/{task_id}/resolve`.
- [x] Refresh UI state after action and show result status.

#### Contact page checklist

- [x] Add a "Provisional / Review" panel to `/contact/[contactId]` when the contact has open case opportunities or resolution tasks.
- [x] Add links to `/cases` and `/resolution` filtered context (if filters are implemented in UI).

### Workstream 1D: Contact Page UX Alignment to Intended Design (Partial, P0 Slice)

This phase only delivers the minimum UX alignment needed to support review and action. Deep knowledge-graph context panels are expanded later.

- [x] Add a recent interaction timeline panel (subject, timestamp, direction, thread).
- [x] Add accepted/proposed claims summary panel (sensitive hidden by default).
- [x] Add visible "Resolve" affordance for proposed changes / discrepancies.
- [x] Keep score trend and current score panels intact.

### Phase 1 Testing Procedure (Must Pass Before Phase 2)

#### Automated API tests

- [x] Add tests for `/scores` next-step payload shape and non-stub behavior.
- [x] Add tests for new real opportunities ranking endpoint (contract + ordering).
- [x] Add tests for `/cases/contacts` and `/cases/opportunities` response consumption fields.
- [x] Add tests for resolution task resolve actions (`accept`, `reject`, `edit_and_accept`) API route behavior (if not already covered).

#### Automated UI tests / component tests (or documented manual substitute if no harness)

- [ ] Validate homepage opportunity cards render real API data (not synthetic company-grouping).
- [ ] Validate `/resolution` page action buttons call the resolve endpoint and refresh state.
- [ ] Validate `/cases` page can list and promote a provisional contact and provisional opportunity.

#### Manual E2E procedure (required)

- [ ] Ingest an interaction with an unknown external email and opportunity signal.
- [ ] Confirm a provisional contact appears in `/cases`.
- [ ] Confirm a provisional opportunity appears in `/cases`.
- [ ] Promote provisional contact; verify it appears as canonical in priority contacts.
- [ ] Promote provisional opportunity; verify it appears in the real priority opportunities list.
- [ ] Open contact page; verify next step is not prefixed with `Stub:`.
- [ ] Resolve one discrepancy via UI and verify status updates immediately.

### Phase 1 Exit Gate

- [ ] No user-facing `Stub:` next-step text appears in `/scores/contact` or homepage contact workflows (except explicitly flagged developer fallback mode in debug output).
- [ ] Homepage opportunities are sourced from real opportunities/case opportunities.
- [ ] Provisional contact/opportunity review and resolution actions are available in UI and functional.
- [ ] Contact page supports review/action-oriented workflow (timeline + claims/proposed changes).

---

## Phase 2: P0/P1 Trust Alignment

Goal: Make ranking, news relevance, drafting, and citations trustworthy and time-aware under the current graph v2 architecture.

### Issues Addressed in This Phase

- [ ] `R4` News matching graph reads legacy labels under v2 mode
- [ ] `R5` Time-aware graph path sort bug
- [ ] `R7` Coarse claim provenance and weak draft citation mapping
- [ ] `R8` Drafting policy/gating misalignment (`allow_sensitive`, provisional/uncertain handling, v2 accepted-claim gap)

### Workstream 2A: Fix Time-Aware Graph Path Ranking (`R5`)

- [x] Patch `_contact_graph_paths_v2(...)` sorting to prefer newer `latest_seen_at` values rather than older ones.
- [x] Sort by parsed datetime / recency integer where possible, not raw strings.
- [x] Add deterministic fallback ordering for null timestamps.
- [ ] Verify `lookback_days` filtering and final ranking are consistent with UI expectations.

### Workstream 2B: Align News Matching with Graph v2 Read Mode (`R4`)

#### Backend refactor checklist

- [x] Replace legacy-label candidate queries in `news/match_contacts.py` with v2-aware graph queries (or call shared v2 query helpers).
- [x] Use `CRMContact` + `KGAssertion` + `EvidenceChunk` (and company/opportunity edges where available).
- [x] Keep candidate generation explainable (path/topic/claim match reasons).
- [x] Preserve no-persistence behavior for `/news/match`.
- [ ] Ensure behavior under:
  - [x] `graph_v2_read_v2=true`
  - [x] `graph_v2_dual_write=false` (default)
  - [ ] fallback/mixed modes only if explicitly supported

#### Relevance quality checklist

- [x] Include company associations and recent interactions in candidate generation (not only claim text contains keyword).
- [x] Add explicit recency weighting for article-to-contact rerank.
- [x] Return evidence refs that point to claims/interactions/chunks (not summary-only statements).

### Workstream 2C: Tighten Claim Provenance and Draft Citation Fidelity (`R7`)

#### Provenance capture checklist

- [x] Stop assigning identical `chunks[:3]` evidence refs to every claim by default.
- [x] Use extractor-provided spans (`evidence_spans`) when available to map claims to relevant chunk(s).
- [x] Implement claim-to-chunk alignment fallback (text span / semantic overlap) when extractor spans are absent.
- [x] Persist claim-level evidence refs that are specific enough for later drafting/scoring explainability.
- [ ] Keep Neo4j evidence nodes pointer-based (chunk/span/hash only; no large verbatim text).

#### Draft citation checklist

- [x] Replace "first N relevant chunks => paragraph N" citation logic with paragraph-to-evidence assignment.
- [x] Ensure each generated paragraph has zero-or-more explicit evidence refs and no fake one-to-one assumptions.
- [x] Mark unsupported paragraphs explicitly (e.g. generic pleasantries) rather than attaching arbitrary citations.
- [x] Include citation provenance quality indicators (`direct_claim`, `chunk_support`, `thread_context`, etc.) if feasible.

### Workstream 2D: Drafting Trust and Policy Gating Fixes (`R8`)

#### Policy separation checklist

- [x] Separate `allow_sensitive` from `include_uncertain` / `allow_proposed_claims`.
- [x] Add explicit request parameters (or internal defaults) for:
  - [x] `allow_sensitive`
  - [x] `allow_uncertain_context` (default false)
  - [x] `allow_proposed_changes_in_external_text` (default false)
- [x] Preserve backward compatibility for existing draft API callers if needed.

#### Retrieval bundle trust checklist

- [x] Implement v2 accepted assertion retrieval for graph claim snippets (do not zero out accepted claims when v2 is enabled).
- [x] Filter motivator/context signals using assertion status and sensitivity policy.
- [x] Exclude sensitive assertions by default from motivator signals and prompt payloads.
- [x] Include proposed contradictory claims only in an internal note channel/field, not external draft content.

#### Composer/citation enforcement checklist

- [x] Ensure composer receives policy flags explicitly and logs/returns which gates were applied.
- [x] Prevent unconfirmed changes from appearing as statements of fact in generated draft text.
- [x] Add post-generation validation that checks drafted claims against allowed evidence/policy scope before saving.

### Workstream 2E: Score Explainability Trust Hardening (Related to `R7`, `R8`)

- [x] Upgrade score reasons from generic component dumps to component-specific evidence refs for non-trivial drivers:
  - [x] open loops
  - [x] commitment/opportunity triggers
  - [x] graph-derived boosts
- [x] Include evidence quality metadata and timestamps to support "why score moved" explanations.
- [ ] Ensure score responses are still lightweight enough for UI rendering.

### Phase 2 Testing Procedure (Must Pass Before Phase 3)

#### Automated tests (required)

- [x] Add unit test for v2 path ranking recency ordering (newer path ranks above older path when other features equal).
- [x] Add news match tests that operate under `graph_v2_read_v2=true` and validate candidate generation from v2 assertions.
- [x] Add provenance tests proving claim evidence refs are not blanket-shared across unrelated claims.
- [x] Add draft citation tests validating paragraph-to-evidence mapping behavior.
- [x] Add draft policy tests:
  - [x] sensitive assertions excluded by default
  - [x] uncertain/proposed context excluded from external text by default
  - [x] optional inclusion only when explicitly enabled

#### Manual verification (required)

- [ ] Seed two graph assertions for a contact with different timestamps and confirm newer context is surfaced first in graph-path-driven summaries/retrieval.
- [ ] Submit a news article and verify top matches include explainable v2 evidence refs (claim/interactions/chunks) rather than legacy-only matches.
- [ ] Generate a draft with `allow_sensitive=false`; verify sensitive personal facts do not appear.
- [ ] Generate a draft after a proposed employment change exists; verify change is treated as tentative/internal and not asserted as fact.
- [ ] Inspect saved `citations_json` for a generated draft and confirm paragraph citations map to actual supporting chunks/spans.

### Phase 2 Exit Gate

- [ ] Graph-path ordering is demonstrably recency-correct.
- [ ] News matching uses v2 graph data in default config and remains no-persist.
- [ ] Claims and citations are evidence-specific (not blanket chunk refs).
- [ ] Drafting respects separated sensitivity/provisional policy gates and passes trust tests.

---

## Phase 3: P1 Intelligence Quality

Goal: Improve the quality of extracted context, contact resolution, contradiction handling, and opportunity inference so rankings/next steps/drafts become materially better.

### Issues Addressed in This Phase

- [x] `R6` Topic/relationship-signal context dropped before graph persistence
- [x] `R9` Transcript/speaker contact matching too weak (email-only)
- [x] `R10` Contradiction detection too narrow

### Workstream 3A: Persist Graph Context Claims Separately from CRM-Promotable Claims (`R6`)

#### Model/logic checklist

- [x] Split claim filtering into two explicit pipelines:
  - [x] `graph_context_claims` (includes topic + relationship signals + other non-promotable but useful context)
  - [x] `crm_promotable_claims` (strict subset for case/opportunity promotion and high-value relation persistence)
- [x] Add support for `topic` and `relationship_signal` persistence to v2 assertions with evidence.
- [ ] Keep low-signal filtering, but tune it to avoid dropping valuable context terms.
- [ ] Ensure scoring/drafting/news retrieval can consume these context assertions safely and with policy filtering.

#### Retrieval/score integration checklist

- [ ] Incorporate persisted relationship signals into relationship scoring (bounded contribution + evidence refs).
- [ ] Incorporate topic context into news matching and draft retrieval queries.
- [ ] Re-check graph path/ranking noise after adding topic persistence (prevent topic spam dominance).

### Workstream 3B: Improve Transcript/Speaker Contact Resolution (`R9`)

#### Resolution pipeline checklist

- [x] Extend participant resolution to support name-only transcript speakers (no email).
- [x] Add deterministic speaker-name matching against contact cache:
  - [x] exact normalized display-name match
  - [x] first/last token match
  - [ ] optional confidence scoring for fuzzy matches
- [x] When ambiguous, create identity resolution tasks with ranked candidate suggestions instead of auto-linking.
- [x] Preserve provenance of how a contact match was made (`email_exact`, `name_exact`, `name_fuzzy`, `manual_resolution`).
- [x] Avoid auto-linking internal-only or low-confidence ambiguous names.

#### UI/ops checklist

- [x] Surface speaker-resolution tasks in the resolution/cases workflow with enough context (speaker label, transcript interaction id, candidate contacts).

### Workstream 3C: Broaden Contradiction Detection and Resolution (`R10`)

#### Contradiction engine checklist

- [x] Expand contradiction detection beyond employment:
  - [x] opportunity stage/status or materially conflicting opportunity claims
  - [x] commitments / due dates / owners
  - [x] personal detail conflicts (with sensitivity-aware review)
  - [ ] relationship-relevant facts (e.g., key preferences if mutually exclusive)
- [x] Normalize comparison rules by claim type (avoid naive JSON inequality where semantic equality exists).
- [x] Generate typed resolution tasks with concise, reviewable payloads and evidence refs.
- [x] Prevent duplicate open contradiction tasks for the same semantic conflict.

#### Resolution UX checklist

- [x] Update resolution UI to render type-specific editors or helper summaries for new contradiction types.

### Workstream 3D: Opportunity Association and Next-Step Intelligence Quality Upgrade (Completes Review Scope)

This closes remaining quality gaps affecting your goals even if not isolated as a single issue ID.

- [ ] Improve `find_best_opportunity_for_interaction_v2(...)` scoring beyond thread/company/contact overlap:
  - [x] recency of last engagement on opportunity
  - [ ] lexical/semantic similarity between interaction subject/body and opportunity title/context
  - [ ] active commitments/open loops attached to opportunity
  - [ ] opportunity status/stage compatibility
- [x] Record why an interaction was linked to an opportunity (scoring components + evidence).
- [ ] Persist structured next-step suggestions at contact+opportunity level (not only free text).
- [ ] Add time decay and freshness weighting for opportunity prioritization and next-step inference.
- [ ] Ensure drafts can be generated against a specific `opportunity_id` and use opportunity-linked context.

### Workstream 3E: End-to-End Knowledge Graph Context Quality Validation

- [ ] Create seeded integration scenario covering:
  - [ ] transcript name-only speaker resolution
  - [ ] topic extraction and persistence
  - [ ] provisional opportunity creation and promotion
  - [ ] contradiction task generation for non-employment claim
  - [ ] news article match to correct contact
  - [ ] opportunity-linked next step and draft generation
- [ ] Validate recency weighting changes outputs when older/newer evidence conflicts.

### Phase 3 Testing Procedure (Must Pass Before Declaring Remediation Complete)

#### Automated tests (required)

- [ ] Add worker tests proving `topic` / `relationship_signal` claims are persisted to graph assertions with evidence.
- [ ] Add transcript resolution tests for:
  - [x] exact speaker-name match
  - [x] ambiguous match -> resolution task
  - [x] no match -> provisional / identity task behavior
- [x] Add contradiction tests for at least three non-employment claim types with semantic comparisons.
- [ ] Add opportunity matcher ranking tests covering recency and context similarity.
- [ ] Add opportunity matcher ranking tests covering recency and context similarity.
- [ ] Add draft retrieval tests for opportunity-linked drafting input and evidence-backed next-step context.

#### Manual end-to-end scenario (required)

- [ ] Sync contacts with at least two similarly named contacts (to force ambiguity handling).
- [ ] Ingest transcript with name-only speakers and confirm resolution behavior is correct.
- [ ] Ingest email mentioning a new deal thread; verify case opportunity suggestion and/or correct existing opportunity link.
- [ ] Ingest conflicting commitment or opportunity-stage info; verify contradiction task appears.
- [ ] Paste relevant news article; verify matched contacts reflect topic/company/time-aware graph context.
- [ ] Generate draft from the suggested next step for a specific opportunity and confirm citations/policy behavior.

### Phase 3 Exit Gate

- [ ] Graph context quality improved (topics/relationship signals persist and are consumable).
- [ ] Transcript speaker resolution is no longer email-only.
- [ ] Contradiction detection covers key CRM-relevant claim types beyond employment.
- [ ] Opportunity matching and next-step generation are materially more context-aware and time-aware.

---

## Traceability Matrix (Issue -> Tasks -> Gate)

### `R1` Stubbed next steps
- [x] Phase 1 / Workstream 1A completed
- [x] Phase 1 automated `/scores` next-step contract test passed
- [ ] Phase 1 manual contact page validation passed

### `R2` Synthetic opportunities UI
- [ ] Phase 1 / Workstream 1B completed
- [ ] Phase 1 homepage opportunity data-source test passed
- [ ] Phase 1 manual promotion -> homepage visibility validation passed

### `R3` Missing provisional/review UI actions
- [ ] Phase 1 / Workstream 1C and 1D completed
- [ ] Phase 1 `/cases` + `/resolution` UI action tests passed
- [ ] Phase 1 manual promote/resolve workflow passed

### `R4` News matching legacy graph reads under v2
- [x] Phase 2 / Workstream 2B completed
- [x] Phase 2 v2 graph-mode news tests passed
- [ ] Phase 2 manual news explainability validation passed

### `R5` Graph path recency sorting bug
- [x] Phase 2 / Workstream 2A completed
- [x] Phase 2 recency-order unit test passed

### `R6` Context claim drop (topics/relationship signals)
- [ ] Phase 3 / Workstream 3A completed
- [ ] Phase 3 worker persistence tests passed

### `R7` Coarse provenance/citations
- [x] Phase 2 / Workstream 2C completed
- [x] Phase 2 provenance + citation tests passed
- [ ] Phase 2 manual draft citation inspection passed

### `R8` Drafting trust/policy misalignment
- [ ] Phase 2 / Workstream 2D and 2E completed
- [x] Phase 2 policy gating tests passed
- [ ] Phase 2 manual sensitive/proposed-change drafting checks passed

### `R9` Email-only contact matching (transcripts)
- [ ] Phase 3 / Workstream 3B completed
- [x] Phase 3 transcript resolution tests passed
- [ ] Phase 3 manual ambiguous-speaker workflow passed

### `R10` Employment-only contradiction detection
- [ ] Phase 3 / Workstream 3C completed
- [x] Phase 3 multi-claim contradiction tests passed
- [ ] Phase 3 manual contradiction resolution workflow passed

---

## Final Remediation Definition of Done

- [ ] All three phases (`P0 UX`, `P0/P1 Trust`, `P1 Intelligence`) passed their exit gates in sequence.
- [ ] Every review finding `R1` to `R10` is checked off in the traceability matrix.
- [ ] Core user workflows are usable end-to-end:
  - [ ] review ranked contacts and real opportunities
  - [ ] review/approve provisional contacts and opportunities
  - [ ] resolve contradictions in UI
  - [ ] run news matching with explainable v2 graph evidence
  - [ ] generate evidence-backed, policy-compliant drafts linked to next steps/opportunities
- [ ] Test evidence for each phase is recorded (commands + results + manual verification notes).
