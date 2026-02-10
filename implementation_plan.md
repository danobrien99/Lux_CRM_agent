# Lux CRM Agent Implementation Plan

Date: 2026-02-10  
Scope: Complete MVP implementation aligned to `AGENT.md` and `.CODEX/PRD.md`, using local Cognee and Mem0 repos in `/Users/dobrien/code/Lux/third_party`.

## Plan Rules
- Complete workstreams in order.
- Do not begin the next step until the current step "Expected behavior gate" is fully met.
- If a gate fails, create a fix task, resolve it, and rerun tests before proceeding.
- Keep all claims and scoring outputs evidence-backed with provenance.

## Workstream 1: Platform Bootstrap (Docker, n8n, Google OAuth)

### Step 1.1: Validate baseline runtime
Implementation checklist
- [ ] Confirm Docker services are healthy (`api`, `worker`, `redis`, `neo4j`, `n8n`, `ui`).
- [ ] Confirm `.env` has required local values for Neo4j, Redis, webhook secret, and queue mode.
- [ ] Confirm API responds on `/v1/health`.

Testing checklist
- [ ] `curl http://localhost:8000/v1/health`
- [ ] Confirm n8n UI loads at `http://localhost:5679`
- [ ] Confirm Neo4j Browser loads at `http://localhost:7474`

Expected behavior gate
- API health is `status=ok`.
- All core services are reachable with no crash loop.

### Step 1.2: Configure Google OAuth in n8n (start here)
Implementation checklist
- [ ] Create/choose Google Cloud project.
- [ ] Enable Gmail API, Google Sheets API, and Google Drive API.
- [ ] Configure OAuth consent screen.
- [ ] Create OAuth client and set redirect URI: `http://localhost:5680/rest/oauth2-credential/callback`.
- [ ] Add Gmail and Google credentials in n8n.

Testing checklist
- [ ] n8n "Test credential" succeeds for Gmail.
- [ ] n8n "Test credential" succeeds for Google Sheets.
- [ ] n8n "Test credential" succeeds for Google Drive (if transcript ingest will use Drive).

Expected behavior gate
- n8n credentials are valid and authorized with no refresh token errors.

### Step 1.3: Wire n8n workflows and webhook security
Implementation checklist
- [ ] Import and activate `gmail_ingest.json`, `transcript_ingest.json`, `news_ingest.json`, `sheets_sync.json`, `score_recompute.json`, `data_cleanup.json`.
- [ ] Ensure every API POST node sends `X-Webhook-Secret` from `N8N_WEBHOOK_SECRET`.
- [ ] Enable retries and dead-letter handling where absent.

Testing checklist
- [ ] Trigger each workflow manually once.
- [ ] Verify API returns 200 for each POST target.
- [ ] Verify invalid webhook secret returns 401.

Expected behavior gate
- All workflow API calls pass with correct secret and fail with wrong secret.

### Step 1.4: Use n8n push sync for contacts until backend pull is implemented
Implementation checklist
- [ ] Add n8n node flow to read sheet rows and POST `{"mode":"push","rows":[...]}` to `/v1/contacts/sync`.
- [ ] Ensure row schema includes `contact_id`, `primary_email`, optional fields.

Testing checklist
- [ ] Push a small sheet sample and verify records in `contact_cache`.
- [ ] Verify contact lookup endpoint resolves pushed contacts.

Expected behavior gate
- Contacts are syncable from Sheets via n8n without backend pull support.

## Workstream 2: Contact Registry and Ingestion Hardening

### Step 2.1: Implement real Google Sheets pull backend
Implementation checklist
- [x] Replace `apps/api/app/services/contacts_registry/sheets_client.py` placeholder with Google API implementation.
- [x] Map sheet columns to `ContactRow` contract with validation and normalization.
- [x] Add error handling for missing credentials, missing sheet, and malformed rows.

Testing checklist
- [ ] Unit test successful pull and row mapping.
- [ ] Unit test credential errors and malformed row handling.
- [ ] API test: `POST /v1/contacts/sync` with `mode=pull`.

Expected behavior gate
- Pull mode returns non-zero upserts from real sheet data and fails safely when config is invalid.

### Step 2.2: Harden ingestion idempotency and event normalization
Implementation checklist
- [x] Verify/extend dedupe behavior for `raw_events` and `interactions`.
- [x] Ensure timestamp handling is UTC and consistent.
- [x] Ensure participant parsing and event typing align with contracts.

Testing checklist
- [x] Repeat-ingest same interaction and assert single raw/interaction row.
- [x] Ingest transcript and verify type mapping to `meeting`.
- [x] Negative tests for invalid payload shape.

Expected behavior gate
- Ingestion remains idempotent and contract-compliant across sources.

### Step 2.3: Improve participant-to-contact resolution
Implementation checklist
- [x] Resolve contacts from `from/to/cc` with case-insensitive email matching.
- [x] Create identity-resolution tasks only when contact cannot be resolved.
- [x] Prevent empty/duplicate contact ID artifacts.

Testing checklist
- [ ] Unit tests for multi-participant matching.
- [x] Verify unknown participant generates resolution task.

Expected behavior gate
- Contact IDs attached to interactions are accurate and deterministic.

## Workstream 3: Real Cognee and Mem0 Integration (Local OSS Repos)

### Step 3.1: Replace Cognee heuristic adapter with real pipeline calls
Implementation checklist
- [x] Update `apps/api/app/integrations/cognee_oss_adapter.py` to call real Cognee APIs from local package.
- [x] Use Cognee flow: `add` -> `cognify` -> `search` with appropriate search type.
- [x] Map Cognee results into internal candidate entity/relation/topic contract.
- [x] Keep explicit fallback path behind a feature flag, not default.

Testing checklist
- [ ] Adapter unit test with real function signatures (mocked Cognee backend responses).
- [ ] Integration test ensures candidates include graph-derived relations, not heuristic-only tokens.

Expected behavior gate
- Extraction output is generated from Cognee pipeline and passes schema contract consistently.

### Step 3.2: Replace Mem0 heuristic adapter with real Memory operations
Implementation checklist
- [x] Update `apps/api/app/integrations/mem0_oss_adapter.py` to use `Memory.from_config(...)`.
- [x] Configure Mem0 for Neo4j graph store and supported vector store in local environment.
- [x] Use Mem0 `add`/`search` results (`results` and `relations`) to produce deterministic memory ops for claims.
- [x] Preserve strict scoping via `user_id`/`agent_id`/`run_id`.

Testing checklist
- [ ] Unit tests for Mem0 config creation and operation mapping.
- [ ] Integration test for add+search cycle with scoped filters.

Expected behavior gate
- Memory ops are derived from Mem0 outputs with predictable schema and no placeholder-only behavior.

### Step 3.3: Validate third-party config and runtime compatibility
Implementation checklist
- [x] Align `.env` and adapter config with chosen Mem0/Cognee local modes.
- [x] Verify async/sync bridging inside worker flow (no event loop misuse).
- [x] Document required local dependency versions and startup assumptions.

Testing checklist
- [ ] End-to-end ingestion run that exercises both adapters.
- [ ] Confirm no import-time failures and no silent fallback to heuristics.

Expected behavior gate
- Worker pipeline can run extraction and memory stages using local OSS stacks in a stable way.

## Workstream 4: Memory/Claim Pipeline Correctness and Provenance

### Step 4.1: Use existing claims state for memory ops and contradiction checks
Implementation checklist
- [x] Fetch existing accepted claims for each contact before applying new ops.
- [x] Stop passing empty claim lists in worker memory flow.
- [x] Ensure contradiction detection compares proposed vs accepted factual state.

Testing checklist
- [ ] Ingest employment change against existing accepted employment and assert resolution task creation.
- [ ] Assert no contradiction task when values match.

Expected behavior gate
- Contradiction tasks reflect real claim state changes, not synthetic empty baselines.

### Step 4.2: Enforce provenance and evidence non-negotiables
Implementation checklist
- [ ] Ensure every stored claim has `interaction_id`, `chunk_id`, and span metadata.
- [ ] Ensure drafts only use claims with valid evidence refs.
- [ ] Ensure large verbatim evidence text is not stored in graph nodes.

Testing checklist
- [ ] Unit tests for claim write rejection when evidence is missing.
- [ ] Draft generation test confirms unsupported claims are excluded.

Expected behavior gate
- No evidence-less claims are used in drafting or scoring paths.

### Step 4.3: Complete resolution side effects
Implementation checklist
- [x] Ensure resolve actions update claim status transitions correctly.
- [x] Ensure employment acceptance updates graph relationship (current employer edge).
- [x] Ensure audit trail is persisted in task payload.

Testing checklist
- [x] Resolve acceptance path test validates graph relationship updates.
- [x] Resolve reject path test validates claim/task status updates.

Expected behavior gate
- Resolution workflow updates both task state and graph/claim state consistently.

## Workstream 5: GraphRAG Retrieval and News Matching (addresses heuristic fallbacks)

Targets to replace:
- `apps/api/app/services/embeddings/vector_store.py` (current fallback search)
- `apps/api/app/services/news/match_contacts.py` (current name-overlap heuristic)

### Step 5.1: Implement real pgvector similarity retrieval
Implementation checklist
- [x] Replace fallback scoring in `search_chunks` with true vector similarity query.
- [x] Support `top_k`, stable ordering, and optional contact scoping.
- [x] Return similarity scores and provenance payload for citation use.

Testing checklist
- [ ] Unit test with seeded embeddings verifies nearest-neighbor ordering.
- [ ] Integration test verifies retrieval quality for draft bundle query.

Expected behavior gate
- Chunk retrieval ranking is based on embedding similarity, not substring heuristics.

### Step 5.2: Implement graph candidate generation for news matching
Implementation checklist
- [x] Add Neo4j query layer to generate candidate contacts using topic/claim/company/recent interaction paths.
- [x] Deduplicate and cap candidate pool before reranking.
- [x] Preserve explainability with path-based reason fragments.

Testing checklist
- [ ] Unit/integration tests with seeded graph ensure candidates reflect graph relationships.
- [ ] Validate candidate set includes expected contacts and excludes unrelated contacts.

Expected behavior gate
- Candidate generation is graph-driven and explainable.

### Step 5.3: Implement vector rerank over graph candidates
Implementation checklist
- [x] Build per-contact profile text from accepted claims + recent interaction summaries.
- [x] Embed article text and profiles, then rerank candidate contacts by similarity.
- [x] Combine graph and vector signals into final `match_score`.
- [x] Return reason chain with evidence refs.

Testing checklist
- [ ] Ranking test ensures high-relevance contact appears at top for seeded scenario.
- [ ] Response contract test validates reason/evidence format.

Expected behavior gate
- Final ranking reflects GraphRAG strategy: graph candidates + vector rerank.

### Step 5.4: Complete `process_news` worker flow and no-persistence rule
Implementation checklist
- [x] Implement chunking/embedding/topic extraction/matching in `process_news`.
- [x] Enforce rule: `/news/match` request and match results are not persisted.
- [x] Keep ingestion and matching workflows clearly separated.

Testing checklist
- [ ] API test confirms `/news/match` returns ranked results.
- [ ] DB row-count test confirms no additional persistence for match requests/results.

Expected behavior gate
- News matching behaves as on-demand GraphRAG computation with zero result persistence.

## Workstream 6: Scoring, Drafting, and Per-Contact Correctness

### Step 6.1: Fix per-contact scoping in score computation
Implementation checklist
- [x] Scope "last interaction", counts, inactivity, and open loops by contact.
- [x] Remove global latest-interaction leakage from score endpoints and recompute jobs.
- [x] Persist score snapshots with evidence refs.

Testing checklist
- [ ] Multi-contact integration test validates independent score values and ordering.
- [ ] Trend endpoint test validates expected contact-specific history shape.

Expected behavior gate
- Scores are contact-specific and reproducible for the same underlying data.

### Step 6.2: Improve draft retrieval bundle and tone/citation behavior
Implementation checklist
- [ ] Ensure retrieval bundle is filtered by target contact and policy flags.
- [ ] Ensure sensitive facts are excluded unless explicitly enabled.
- [ ] Ensure citations map draft paragraphs to valid interaction/chunk spans.

Testing checklist
- [ ] Draft tests for low/medium/high relationship tone bands.
- [ ] Citation coverage tests ensuring each paragraph has supporting refs where applicable.

Expected behavior gate
- Drafts are contact-relevant, tone-appropriate, and evidence-cited.

### Step 6.3: Align score and draft APIs with acceptance criteria
Implementation checklist
- [ ] Verify `/scores/today`, `/scores/contact/{id}`, `/drafts` behavior against PRD/AGENT acceptance points.
- [ ] Add any missing response fields needed for UI explainability.

Testing checklist
- [ ] API contract tests for schema and key fields.
- [ ] UI smoke checks for pages dependent on these endpoints.

Expected behavior gate
- UI can consume score and draft outputs without fallback assumptions.

## Workstream 7: Test Coverage, Observability, and Operations

### Step 7.1: Establish runnable test harness
Implementation checklist
- [ ] Install missing dev test deps in `apps/api/.venv` (`pytest`, etc.).
- [ ] Add shared test fixtures for Postgres/Neo4j integration scenarios.
- [ ] Classify tests into unit vs integration markers.

Testing checklist
- [ ] Run unit suite successfully.
- [ ] Run integration subset against local Docker services.

Expected behavior gate
- Test suite is runnable and produces stable pass/fail feedback locally.

### Step 7.2: Add missing acceptance tests from AGENT
Implementation checklist
- [ ] Implement tests for ingestion, extraction/memory contradictions, scoring evidence refs, news no-persist rule, drafting citations/sensitive gating, resolution state updates, cleanup policy.
- [ ] Ensure each acceptance criterion maps to at least one automated test.

Testing checklist
- [ ] Execute acceptance test group and record results in repo docs.

Expected behavior gate
- Acceptance criteria are covered by passing automated tests.

### Step 7.3: Logging, retries, and dead-letter operations
Implementation checklist
- [ ] Ensure structured logs include `interaction_id` and `contact_id` in worker jobs.
- [ ] Add retry policies and dead-letter handling in n8n and worker queue paths.
- [ ] Add operator-visible error reporting for failed extraction/memory stages.

Testing checklist
- [ ] Failure injection test verifies retries then dead-letter path.
- [ ] Log inspection verifies required IDs are present.

Expected behavior gate
- Operational failures are diagnosable and recoverable without data loss.

## Workstream 8: Deployment Hardening and Release Readiness

### Step 8.1: Migration and startup sequencing
Implementation checklist
- [ ] Standardize DB migration flow (Alembic) and startup order.
- [ ] Ensure Neo4j schema init script is run reliably in deployment workflow.
- [ ] Validate compose overrides for local/dev/prod-like runs.

Testing checklist
- [ ] Fresh environment bootstrap test from zero data.
- [ ] Restart/upgrade test with preserved data volume.

Expected behavior gate
- Deployment is repeatable with deterministic startup and schema state.

### Step 8.2: Documentation and runbooks
Implementation checklist
- [ ] Update `README.md` and `quickstart.md` with final setup and smoke flow.
- [ ] Add operator runbook for OAuth, n8n, queue recovery, and reprocess/cleanup commands.
- [ ] Document third-party dependency expectations for Cognee and Mem0 local repos.

Testing checklist
- [ ] Follow docs in a clean shell and validate successful bring-up.

Expected behavior gate
- A new engineer can bootstrap and run core workflows from docs alone.

### Step 8.3: Final release checklist
Implementation checklist
- [ ] Confirm all gates in this plan are checked.
- [ ] Confirm API/UI smoke tests and acceptance tests pass.
- [ ] Confirm no placeholder heuristics remain in production path for retrieval/matching.

Testing checklist
- [ ] Execute final end-to-end scenario: contact sync -> interaction ingest -> worker processing -> scores today -> news match -> draft -> resolution.

Expected behavior gate
- End-to-end MVP flow is complete, test-backed, and operationally supportable.

## Final Definition of Done
- [ ] Real Cognee and Mem0 integrations are active in worker pipeline.
- [ ] GraphRAG retrieval strategy is implemented for chunk retrieval and news matching.
- [ ] Scoring, drafting, and resolution are per-contact, evidence-backed, and acceptance-tested.
- [ ] n8n + Google OAuth ingestion workflows run reliably.
- [ ] Deployment and runbook docs are complete and validated.
