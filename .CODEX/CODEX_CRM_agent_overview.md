> **Deprecated / Historical Notes**  
> This document is retained for historical context only.  
> The current source of truth is `AGENT.md`.

Below are **Codex-ready build instructions** broken into modules. They assume:

* **Backend + UI run locally** (Docker), but connect to:

  * **Neo4j Aura** (graph)
  * **Neon Postgres + pgvector** (chunks/embeddings/citations)
* **n8n runs locally** (Docker) and is responsible for **connectors + triggers + routing**, not business logic.
* **Google Sheet is the contact registry** for MVP (HubSpot later).
* **Cognee** is used for extraction (entities/relations/topics) and returns *candidates*.
* **Mem0** maintains **temporal claims** with add/update/supersede and contradiction detection → “needs resolution.”

I’m going to give you:

1. Repo structure and cross-module contracts
2. Detailed instructions per module (what to build, acceptance criteria, tests)
3. n8n workflow specs (nodes, inputs/outputs, retries)
4. Graph + Postgres schemas
5. Worker pipelines for your 5 MVP proof features

---

# 0) Global build rules for Codex

**Non-negotiables**

* Every extracted memory/claim must have **provenance**: `interaction_id` + `source_system` + `evidence_pointer` (chunk_id + span or message_id). No provenance → cannot be used in drafts.
* Personal facts are stored **verbatim** but gated by:

  * `sensitive: true/false`
  * `use_in_drafts_default: false` (default deny)
* Discrepancies (e.g., company change) never auto-overwrite “current.” Create **proposed** claims and create a **resolution task**.

**Coding conventions**

* Python 3.11
* FastAPI + Pydantic v2
* SQLAlchemy 2.0 + Alembic
* Redis + RQ (simpler than Celery) for background jobs
* Neo4j official Python driver
* pgvector in Neon
* Tests: pytest; use VCR-like stubs or mocked connectors (don’t hit Gmail in CI)

---

# 1) Repo layout (Codex must create exactly this)

```
luxcrm/
  README.md
  docker-compose.yml
  .env.example

  apps/
    api/                      # FastAPI app
      app/
        main.py
        api/
          v1/
            routes/
              health.py
              ingest.py       # /interaction_event, /news_item
              contacts.py     # /contacts sync state, /resolution queue
              scores.py       # /today
              drafts.py       # /draft request + preview
              admin.py        # /reprocess, /deadletter
            deps.py
        core/
          config.py
          logging.py
          security.py
        db/
          pg/
            base.py
            models.py
            session.py
            migrations/
          neo4j/
            driver.py
            schema.py
            queries.py
        services/
          contacts_registry/
            sheets_client.py
            sync.py
          ingest/
            normalize.py
            store_raw.py
          chunking/
            chunk_email.py
            chunk_transcript.py
          embeddings/
            embedder.py
            vector_store.py
          extraction/
            cognee_client.py
            cognee_mapper.py
          memory/
            mem0_client.py
            mem0_mapper.py
            temporal_claims.py
            contradiction.py
          scoring/
            relationship_score.py
            priority_score.py
            snapshots.py
          news/
            ingest_news.py
            match_contacts.py
          drafting/
            retriever.py
            composer.py
            tone.py
            citations.py
          resolution/
            tasks.py
            ui_payloads.py
        workers/
          queue.py
          jobs.py
        tests/
          test_ingest.py
          test_claims.py
          test_scoring.py
          test_news_match.py
          test_drafting.py

    ui/                       # Minimal web UI (Next.js or simple Vite+React)
      ...

  n8n/
    workflows/
      gmail_ingest.json
      sheets_sync.json
      news_ingest.json
      transcript_ingest.json
    credentials/
      README.md               # manual setup instructions only (no secrets)

  scripts/
    init_neo4j_schema.py
    backfill_embeddings.py
```

---

# 2) Module A — Infrastructure & local runtime

## A1. docker-compose

Codex: create `docker-compose.yml` for local services:

* `api` (FastAPI)
* `worker` (RQ worker)
* `redis`
* `n8n`
* (optional) `ui`

Do **not** run local Postgres/Neo4j because you’re using **Neon + Aura**. Keep local state minimal.

**Acceptance criteria**

* `docker compose up` starts api/worker/redis/n8n.
* `GET /v1/health` returns ok.

## A2. Environment config

Codex: create `.env.example` with:

* `NEO4J_URI=neo4j+s://...` (Aura)
* `NEO4J_USER=...`
* `NEO4J_PASSWORD=...`
* `NEON_PG_DSN=postgresql+psycopg://...` (Neon)
* `OPENAI_API_KEY=...`
* `LLM_PROVIDER=openai|anthropic`
* `COGNEE_ENDPOINT=http://...` (or local if you run it)
* `MEM0_ENDPOINT=http://...`
* `N8N_WEBHOOK_SECRET=...`
* `GOOGLE_SHEETS_ID=...`
* `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON=...` (or OAuth, but SA is simplest for MVP)

---

# 3) Module B — Postgres schema (Neon + pgvector)

Codex: implement SQLAlchemy models + Alembic migrations.

## B1. Tables

### `raw_events`

Stores everything coming from n8n. Immutable.

* `id (uuid)`
* `source_system` (gmail|calendar|sheets|news|manual)
* `event_type` (email_received, email_sent, transcript_uploaded, contact_sheet_sync, news_item)
* `external_id` (gmail message id, calendar event id, article url hash)
* `payload_json`
* `received_at`

### `interactions`

Normalized interaction header row.

* `interaction_id (uuid)`
* `source_system`
* `type` (email|meeting|note|news)
* `timestamp`
* `direction` (in|out|na)
* `subject`
* `thread_id`
* `participants_json` (emails, names, roles)
* `contact_ids_json` (resolved contacts)
* `status` (new|processed|error)

### `chunks`

Chunked text with pointers back to interaction.

* `chunk_id (uuid)`
* `interaction_id`
* `chunk_type` (email_body|email_quote|transcript_segment|news_paragraph)
* `text`
* `span_json` (start/end, or message-id offsets)
* `created_at`

### `embeddings`

Vectors for each chunk.

* `chunk_id (uuid, pk/fk)`
* `embedding vector(1536 or 3072 depending on model)`
* `embedding_model`
* `created_at`

### `drafts`

* `draft_id`
* `contact_id`
* `created_at`
* `prompt_json`
* `draft_text`
* `citations_json`
* `tone_band`
* `status` (proposed|edited|approved|discarded)

### `resolution_tasks`

* `task_id`
* `contact_id`
* `task_type` (employment_discrepancy, personal_detail_discrepancy, identity_resolution, etc.)
* `proposed_claim_id`
* `current_claim_id` (nullable)
* `payload_json` (provenance, evidence pointers)
* `status` (open|resolved|dismissed)
* `created_at`

**Acceptance criteria**

* Alembic migration applies cleanly to Neon.
* `pgvector` extension enabled and used. (Neon supports pgvector as an extension; ensure migration includes `CREATE EXTENSION IF NOT EXISTS vector;`.)
  (Reference: pgvector docs.) [https://github.com/pgvector/pgvector](https://github.com/pgvector/pgvector)

---

# 4) Module C — Neo4j Aura schema (context graph)

Codex: create `apps/api/app/db/neo4j/schema.py` with:

* Constraints
* Indexes
* A script `scripts/init_neo4j_schema.py` to apply them.

## C1. Node labels + required properties

### `Contact`

* `contact_id` (string uuid) **unique**
* `primary_email` **indexed**
* `display_name`
* `owner_user_id`

### `Interaction`

* `interaction_id` **unique**
* `type`, `timestamp`, `source_system`, `direction`

### `Claim`

* `claim_id` **unique**
* `claim_type` (employment, personal_detail, preference, buying_signal, objection, commitment, relationship_signal)
* `value_json` (stringified JSON)
* `status` (proposed|accepted|rejected|superseded)
* `sensitive` boolean
* `valid_from`, `valid_to` (nullable)
* `confidence`
* `created_at`
* `source_system` (mem0|cognee|manual)

### `Evidence`

* `evidence_id` **unique**
* `interaction_id`
* `chunk_id`
* `span_json`
* `quote_hash` (don’t store verbatim text)

### `ScoreSnapshot`

* `asof` (date string)
* `relationship_score`
* `priority_score`
* `components_json`

## C2. Relationships

* `(Contact)-[:PARTICIPATED_IN]->(Interaction)`
* `(Interaction)-[:HAS_CLAIM]->(Claim)`
* `(Claim)-[:SUPPORTED_BY]->(Evidence)`
* `(Contact)-[:HAS_CLAIM]->(Claim)`
* `(Contact)-[:HAS_SCORE]->(ScoreSnapshot)`
* `(Contact)-[:WORKS_FOR_CURRENT]->(Company)` (only if resolved; Company optional in MVP)

**Acceptance criteria**

* Schema init script runs against Aura.
* Can query a contact → claims → evidence → interactions.

---

# 5) Module D — Contact Registry (Google Sheet proxy)

Codex: implement `contacts_registry/sync.py`.

## D1. Google Sheet format (assume columns)

* `contact_id` (uuid string)
* `primary_email`
* `display_name`
* `owner_user_id`
* `notes` (optional seed context)
* `use_sensitive_in_drafts` (true/false; default false)

## D2. Sync behavior

* n8n triggers sheet sync daily + on manual run.
* Backend endpoint: `POST /v1/contacts/sync` with payload `{sheet_revision, rows[]}` OR backend pulls directly from Sheets (either is fine; pick one and stick to it).

**Preferred MVP approach:** backend pulls from Sheets directly to reduce n8n complexity.

## D3. Output

* Create/merge `Contact` nodes in Neo4j keyed by `contact_id`.
* Maintain a Postgres cache table (optional) for quick lookup email→contact.

**Acceptance criteria**

* Given an email address in ingestion, system resolves to a Contact or emits a `resolution_task` (“identity_resolution”).

---

# 6) Module E — n8n workflows (connectors + triggers)

Codex: create workflow JSON files under `n8n/workflows/` + README setup.

## E1. Workflow: Gmail Ingest

**Trigger:** Gmail new message (polling is OK for MVP)
**Steps:**

1. Gmail node: fetch message(s) since last cursor
2. Function node: map to `InteractionEvent` JSON:

   * `source_system="gmail"`
   * `event_type="email_received"|"email_sent"`
   * `external_id=messageId`
   * `timestamp`
   * `thread_id`
   * `subject`
   * `from/to/cc`
   * `body_plain` (or best-effort)
3. HTTP Request node: POST to `http://api:8000/v1/ingest/interaction_event`

   * add header `X-Webhook-Secret`
4. If response != 200: route to “dead letter” sheet/log

**Acceptance criteria**

* New email appears in Postgres `raw_events` and creates `interactions` row.

## E2. Workflow: Transcript Ingest

**Trigger:** file upload to a watched folder or Google Drive (choose simplest)
**Steps:**

* fetch transcript text
* POST `interaction_event` with `type="meeting"`, `body_plain=transcript`

## E3. Workflow: Sheets Sync (optional if backend pulls)

* Either call backend `/contacts/sync` or do nothing.

## E4. Workflow: News Ingest

**Trigger:** manual webhook + optional RSS later
**Steps:**

* User provides URL/text → n8n fetches article content (basic)
* POST to `/v1/ingest/news_item`

**Codex deliverable**

* Each workflow JSON + setup docs (no secrets committed).

---

# 7) Module F — Ingestion API + normalization

Codex: implement endpoints.

## F1. Endpoint: `POST /v1/ingest/interaction_event`

Payload (Pydantic):

* `source_system`
* `event_type`
* `external_id`
* `timestamp`
* `thread_id?`
* `direction?`
* `subject?`
* `participants` {from,to,cc}
* `body_plain`
* `attachments?` (ignored MVP)

Behavior:

1. Write `raw_events`
2. Normalize into `interactions`
3. Enqueue worker job `process_interaction(interaction_id)`

## F2. Endpoint: `POST /v1/ingest/news_item`

Payload:

* `url?`
* `title`
* `published_at?`
* `body_plain`
  Behavior:
* store as interaction type `news`
* enqueue `process_news(interaction_id)`

**Acceptance criteria**

* Idempotency: same `external_id` from same source doesn’t create duplicates.

---

# 8) Module G — Chunking + embeddings (Neon pgvector)

Codex: implement `chunking/*` and `embeddings/*`.

## G1. Email chunking

MVP:

* Strip signatures (best-effort)
* Split into paragraphs with max ~800–1200 tokens per chunk (approx chars heuristic)
* Store chunk spans as paragraph indices

## G2. Transcript chunking

* Split by speaker turns if available; else paragraphs

## G3. Embeddings

* Implement `embedder.embed_texts(list[str]) -> list[vector]`
* Store into `embeddings` table linked to `chunks`

**Acceptance criteria**

* For each interaction, chunks exist + embeddings exist.
* Vector search function `search_chunks(query, contact_id=None, top_k=10)` works.

---

# 9) Module H — Cognee extraction (candidates → graph)

Codex: implement `extraction/cognee_client.py` and `cognee_mapper.py`.

## H1. Cognee call contract

Input:

* `interaction_id`
* `text` (concatenate chunks or provide chunk list)
  Output (normalize to your internal model):
* `entities[]`: `{name, type, external_refs?, confidence}`
* `relations[]`: `{subject, predicate, object, confidence, evidence_spans?}`
* `topics[]`: `{label, confidence}`

## H2. Write to Neo4j

Create:

* `Claim` nodes for:

  * personal_detail candidates
  * employment candidates
  * preferences
  * commitments/buying signals if Cognee extracts them
    Status: **proposed** by default (unless trivial, like name normalization).

Create Evidence nodes linking to chunk spans.

**Acceptance criteria**

* After processing an email, there are proposed claims with evidence pointers.

---

# 10) Module I — Mem0 memory updater (temporal claims + contradictions)

Codex: implement:

* `memory/mem0_client.py`
* `memory/temporal_claims.py`
* `contradiction.py`
* `resolution/tasks.py`

## I1. Mem0 input bundle

For a given contact + new interaction:

* `new_interaction_summary` (LLM summary with strict length)
* `recent_claims` (accepted + proposed in last 90 days)
* `cognee_candidates` (claims proposed this interaction)
* `relationship_context` (optional)

## I2. Mem0 expected output (your internal format)

* `ops[]` where op ∈ {ADD, UPDATE, SUPERSEDE, REJECT}
  Each op includes:
* `claim_type`
* `value_json`
* `sensitive`
* `confidence`
* `evidence_refs` (chunk_id + span)
* `target_claim_id` (for update/supersede)

## I3. Apply ops to Neo4j

Rules:

* Employment change:

  * create **proposed** new employment claim
  * do **not** change `WORKS_FOR_CURRENT`
  * create `resolution_task` with provenance comparing current accepted vs proposed
* Personal detail change:

  * same pattern: proposed update + resolution task
* If Mem0 indicates confidence very high and no current accepted exists:

  * allow auto-accept for non-sensitive low-risk types (configurable), but keep it conservative in MVP.

**Acceptance criteria**

* Contradictions generate `resolution_tasks` rows in Postgres with provenance payload.
* UI can fetch open tasks and resolve (accept proposed / reject proposed).

---

# 11) Module J — Scoring engine (evolves over time with evidence)

Codex: implement scoring as deterministic + bounded ML inputs.

## J1. Relationship score (0–100)

Components (store breakdown):

* Recency score (days since last meaningful interaction)
* Frequency score (30/90 day counts)
* Reciprocity score (response times, initiation ratio)
* Warmth score (from claims of type relationship_signal; bounded to e.g. ±10 points)
* Depth score (count of accepted claims with evidence; sensitive excluded by default)

Store daily `ScoreSnapshot` in Neo4j and also in Postgres if you want faster reads.

## J2. Priority score (0–100)

Components:

* inactivity decay
* open loops (commitments not closed)
* triggers (news match score)
* relationship temperature band (affects suggested touch type, not just priority)

**Evidence requirement**
For every non-trivial component (warmth, commitments, triggers), store `evidence_refs` in `components_json`.

**Acceptance criteria**

* Score changes after new interactions.
* You can show “why score moved” using the component breakdown.

---

# 12) Module K — News matching (GraphRAG: graph candidate set + vector rerank)

Codex: implement `news/match_contacts.py`.

## K1. Processing a news item

1. Chunk + embed the article

2. Extract topics/entities (Cognee or LLM; pick one path for MVP)

3. Candidate generation (Neo4j query):

   * contacts with accepted claims/topics matching extracted topics
   * contacts with recent interactions mentioning those topics
   * contacts with company associations matching entities (optional if you have Company nodes)

4. Rerank using vectors:

   * Create a “contact profile text” on the fly:

     * last 3 interaction summaries + top accepted claims (non-sensitive)
   * Embed profile (cache it per day per contact)
   * Compute similarity with article embedding

5. Output top N contacts with:

   * `match_score`
   * reason chain:

     * topic overlap + cited claims + cited interactions

Store results as `news_matches` table (optional) or compute on request.

**Acceptance criteria**

* Given a pasted article about “X”, system returns a ranked list and shows why each contact is relevant, with citations.

---

# 13) Module L — Drafting engine (tone-matched, provenance-backed, notes when facts change)

Codex: implement:

* `drafting/retriever.py`
* `drafting/tone.py`
* `drafting/composer.py`
* `drafting/citations.py`

## L1. Tone bands derived from relationship score

Example:

* 0–35: “cool/professional”
* 36–70: “warm/professional”
* 71–100: “friendly/personal”

Codex: implement `tone.py` returning:

* greeting style
* directness
* personal reference allowance
* sentence length targets
* closing style

## L2. Retrieval bundle (GraphRAG)

For a contact + optional objective:

* Neo4j query:

  * accepted claims (non-sensitive unless user allowed)
  * last 3 meaningful interactions
  * open commitments
  * any proposed contradictory claims (for internal note)
* pgvector search:

  * top chunks relevant to objective/news item

## L3. Composer prompt constraints

* Must include a `CITATIONS` section in internal output:

  * map each paragraph to evidence_refs (chunk_id + interaction_id)
* Must not use `sensitive=true` claims unless allowed
* Must not assert unconfirmed changes as facts

  * If there’s a proposed employment change, composer produces:

    * internal note: “Potential change detected…”
    * optional external line only if user toggles

**Acceptance criteria**

* Draft text matches tone band.
* Draft includes citations JSON.
* Draft notes discrepancies internally.

---

# 14) Module M — UI (minimal but demo-grade)

Codex: build minimal UI pages:

1. **Today**

* list of priority contacts
* each row: name, score, “why now” summary
* click opens Contact

2. **Contact**

* timeline (recent interactions)
* accepted claims (with sensitive hidden by default)
* proposed changes + “resolve” button
* score trend

3. **News**

* paste article text/url
* show matched contacts + reasons

4. **Drafts**

* generate draft for a contact (optionally from news match)
* toggle “allow sensitive facts”
* show citations (internal panel)
* approve/save (sending later)

UI can be a thin client calling API endpoints.

---

# 15) Module N — Resolution workflow (human in the loop)

Codex: implement endpoints:

* `GET /v1/resolution/tasks?status=open`
* `POST /v1/resolution/tasks/{task_id}/resolve` with action:

  * `accept_proposed`
  * `reject_proposed`
  * `edit_and_accept` (user edits value_json)

Behavior:

* Update claim statuses in Neo4j
* If accepting employment claim, update `WORKS_FOR_CURRENT`
* Write an audit entry in Postgres `resolution_tasks.payload_json` append log

**Acceptance criteria**

* Discrepancy appears in UI; user resolves; graph updates accordingly.

---

# 16) Worker pipeline (the “single source of truth” for processing)

Codex: implement in `workers/jobs.py`:

## `process_interaction(interaction_id)`

1. load interaction + raw text
2. resolve participants → contacts (from sheet cache)
3. chunk + embed; store chunks + embeddings
4. Cognee extract → proposed claims + evidence
5. For each resolved contact:

   * Mem0 update bundle → ops
   * apply ops to Neo4j
6. update scoring for contact(s)
7. mark interaction processed

## `process_news(interaction_id)`

1. chunk + embed article
2. extract topics/entities
3. match contacts (graph candidates + vector rerank)
4. store match results for UI

---

# 17) What Codex should *not* do (common failure modes)

* Do not put scoring logic inside n8n.
* Do not overwrite Contact properties for temporal facts; use claims + resolution.
* Do not store large verbatim evidence text in Neo4j; store hashes + pointers to Postgres chunks.
* Do not allow drafts to reference anything without evidence pointers.

---

# 18) Concrete “Definition of Done” for your MVP proof points

1. **Priority contacts**

   * Daily list returns top N with explanations + evidence.

2. **News → contacts**

   * Paste article → matched contacts + reason chain + citations.

3. **Evolving relationship score**

   * Score snapshots show change after new interactions; each component is explainable.

4. **Tone-matched drafts**

   * Draft style changes across score bands; cites specific prior context.

5. **Facts change handling**

   * Discrepancy produces a resolution task; draft flags it internally and never asserts it as confirmed.

---

## Next: I can generate the Codex “work package” files

If you want, I’ll output the **exact Codex task list** as a sequence of atomic tickets (e.g., `T-001`…`T-040`) with:

* file paths to create/edit
* function signatures
* example payloads
* sample Neo4j Cypher queries for each retrieval step
* sample n8n workflow node configurations (including mapping code)

Say “generate the ticketed Codex plan” and I’ll produce it in one shot.
