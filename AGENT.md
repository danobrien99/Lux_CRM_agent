# Lux CRM Agent - Master Instructions (MVP)

**Status**
This document replaces the Habit Tracker templates. It is the single source of truth for architecture, contracts, and acceptance criteria for the Lux CRM augmentation platform.

**Purpose**
Augment a CRM contact registry (Google Sheet first, HubSpot later) with a contextual, temporal knowledge graph and intelligence layer that produces:
- A daily priority contact list with evidence-backed reasons
- Relationship scoring that evolves over time with provenance
- News-to-contact matching (GraphRAG: graph candidates + vector rerank)
- Draft email generation matched to relationship strength using graph context
- Fact change detection with human resolution for contradictions

**MVP Goals**
- Ingest emails, transcripts, and news items into a unified interaction pipeline
- Extract candidate entities and claims with evidence, then update temporal memory
- Compute relationship and priority scores with explainability
- Generate tone-matched drafts with citations and sensitive gating
- Surface resolution tasks for contradictions or identity issues

**Assumptions**
- Single-tenant MVP
- Repo root is the Lux_CRM_agent repository root
- UI is Next.js for MVP
- Embeddings use OpenAI with dimension 1536
- LLM providers supported: OpenAI, Gemini, Anthropic, local Ollama
- Cognee and Mem0 are integrated as self-hosted/open-source components (not cloud APIs)
- News matching results are computed on request and not stored
- Data cleanup is optional and user-configurable via schedule and retention settings

**Non-Negotiables**
- Every claim must have provenance: `interaction_id`, `source_system`, and `evidence_pointer` (`chunk_id` + `span_json` or message_id offsets). No provenance means the claim cannot be used in drafts.
- Sensitive facts are stored verbatim but flagged `sensitive=true` and excluded from drafts by default.
- Contradictions never overwrite current facts. Create proposed claims and resolution tasks.
- Do not store large verbatim evidence text in Neo4j. Store hashes and pointers to Postgres chunks.
- Drafts must not assert unconfirmed changes as facts.

**Recommended Defaults**
- Chunk size target: 800 to 1200 tokens or ~3,200 to 4,800 chars
- Transcript chunks: split by speaker turns, else 600 to 900 tokens
- High-confidence auto-accept threshold: `confidence >= 0.90` and `claim_type` in allowlist
- Allowlist for auto-accept: name normalization, role/title, company, non-sensitive preferences
- Relationship score update: on interaction processing and daily scheduled recompute
- Data retention defaults (configurable): raw_events 180 days, chunks 365 days, drafts 365 days

**Repo Structure**
```
.
├── README.md
├── AGENT.md
├── docker-compose.yml
├── .env.example
├── apps/
│   ├── api/
│   │   └── app/
│   │       ├── main.py
│   │       ├── api/
│   │       │   └── v1/
│   │       │       └── routes/
│   │       ├── core/
│   │       ├── db/
│   │       │   ├── pg/
│   │       │   └── neo4j/
│   │       ├── services/
│   │       ├── workers/
│   │       └── tests/
│   └── ui/
│       └── (Next.js app)
├── n8n/
│   ├── workflows/
│   └── credentials/
├── scripts/
└── docs/
```

**Module Map**
- Ingestion API: normalize incoming events and enqueue processing
- Contact Registry: Google Sheets sync and identity resolution
- Chunking + Embedding: create chunks and vectors in Postgres
- Extraction: Cognee candidates and graph claim creation
- Memory: Mem0 temporal claims, contradictions, and resolution
- Scoring: relationship and priority scoring with evidence
- News Matching: GraphRAG candidate set + vector rerank
- Drafting: retrieval bundle, tone bands, citations, sensitive gating
- Resolution: human in the loop for contradictions
- UI: priority list, contact view, news match, drafts, resolution
- Cleanup: optional scheduled data retention enforcement

**Configuration**
`.env.example` must include:
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`
- `NEON_PG_DSN`
- `REDIS_URL`
- `N8N_WEBHOOK_SECRET`
- `GOOGLE_SHEETS_ID`, `GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON`
- `LLM_PROVIDER=openai|anthropic|gemini|ollama`
- `LLM_MODEL=...`
- `EMBEDDING_PROVIDER=openai`
- `EMBEDDING_MODEL=text-embedding-3-small`
- `EMBEDDING_DIM=1536`
- `COGNEE_BACKEND=local|http`
- `COGNEE_LOCAL_MODULE`, `COGNEE_LOCAL_FUNCTION`
- `COGNEE_ENDPOINT` (optional, self-hosted HTTP fallback)
- `MEM0_BACKEND=local|http`
- `MEM0_LOCAL_MODULE`, `MEM0_LOCAL_FUNCTION`
- `MEM0_ENDPOINT` (optional, self-hosted HTTP fallback)
- `DATA_RETENTION_RAW_DAYS=180`
- `DATA_RETENTION_CHUNKS_DAYS=365`
- `DATA_RETENTION_DRAFTS_DAYS=365`
- `DATA_CLEANUP_ENABLED=true|false`
- `DATA_CLEANUP_SCHEDULE_CRON=0 3 * * *`

**Data Contracts**
All timestamps are ISO-8601 in UTC.

**InteractionEvent**
```json
{
  "source_system": "gmail|calendar|sheets|news|manual|transcript",
  "event_type": "email_received|email_sent|meeting_transcript|news_item|note",
  "external_id": "string",
  "timestamp": "2026-02-09T14:30:00Z",
  "thread_id": "string",
  "direction": "in|out|na",
  "subject": "string",
  "participants": {
    "from": [{"email": "a@x.com", "name": "A"}],
    "to": [{"email": "b@y.com", "name": "B"}],
    "cc": []
  },
  "body_plain": "string",
  "attachments": []
}
```

**NewsMatchRequest**
```json
{
  "title": "string",
  "url": "string",
  "published_at": "2026-02-09T10:00:00Z",
  "body_plain": "string",
  "max_results": 10
}
```

**Claim**
```json
{
  "claim_id": "uuid",
  "claim_type": "employment|personal_detail|preference|commitment|relationship_signal|topic",
  "value_json": {"key": "value"},
  "status": "proposed|accepted|rejected|superseded",
  "sensitive": false,
  "confidence": 0.0,
  "valid_from": "2026-02-09T00:00:00Z",
  "valid_to": null,
  "source_system": "mem0|cognee|manual",
  "evidence_refs": [
    {"interaction_id": "uuid", "chunk_id": "uuid", "span_json": {"start": 0, "end": 120}}
  ]
}
```

**API Surface**
Base path: `/v1`

**Health**
- `GET /health`

**Ingestion**
- `POST /ingest/interaction_event` -> enqueue `process_interaction`
- `POST /ingest/news_item` -> enqueue `process_news`

**Contacts**
- `POST /contacts/sync` with either `mode="pull"` or `rows[]` for push
- `GET /contacts/lookup?email=` -> returns contact or resolution task

**Scores**
- `GET /scores/today?limit=50` -> daily priority list with reasons and evidence
- `GET /scores/contact/{contact_id}` -> score trend and components

**Drafts**
- `POST /drafts` -> generate draft for contact with optional objective
- `GET /drafts/{draft_id}`
- `POST /drafts/{draft_id}/status` -> `proposed|edited|approved|discarded`

**News Matching**
- `POST /news/match` -> compute matches, do not store request or results

**Resolution**
- `GET /resolution/tasks?status=open`
- `POST /resolution/tasks/{task_id}/resolve`

**Admin**
- `POST /admin/reprocess` -> requeue processing for an interaction
- `POST /admin/cleanup` -> run data cleanup based on retention policy

**Postgres Schema (Neon + pgvector)**
Use SQLAlchemy 2.0 with Alembic migrations.

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE raw_events (
  id uuid PRIMARY KEY,
  source_system text NOT NULL,
  event_type text NOT NULL,
  external_id text NOT NULL,
  payload_json jsonb NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_system, external_id)
);

CREATE TABLE interactions (
  interaction_id uuid PRIMARY KEY,
  source_system text NOT NULL,
  type text NOT NULL,
  timestamp timestamptz NOT NULL,
  direction text NOT NULL,
  subject text,
  thread_id text,
  participants_json jsonb NOT NULL,
  contact_ids_json jsonb,
  status text NOT NULL DEFAULT 'new'
);

CREATE TABLE chunks (
  chunk_id uuid PRIMARY KEY,
  interaction_id uuid NOT NULL REFERENCES interactions(interaction_id),
  chunk_type text NOT NULL,
  text text NOT NULL,
  span_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE embeddings (
  chunk_id uuid PRIMARY KEY REFERENCES chunks(chunk_id),
  embedding vector(1536) NOT NULL,
  embedding_model text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX embeddings_hnsw ON embeddings USING hnsw (embedding vector_cosine_ops);

CREATE TABLE drafts (
  draft_id uuid PRIMARY KEY,
  contact_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  prompt_json jsonb NOT NULL,
  draft_text text NOT NULL,
  citations_json jsonb NOT NULL,
  tone_band text NOT NULL,
  status text NOT NULL
);

CREATE TABLE resolution_tasks (
  task_id uuid PRIMARY KEY,
  contact_id text NOT NULL,
  task_type text NOT NULL,
  proposed_claim_id text NOT NULL,
  current_claim_id text,
  payload_json jsonb NOT NULL,
  status text NOT NULL DEFAULT 'open',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE contact_cache (
  contact_id text PRIMARY KEY,
  primary_email text NOT NULL,
  display_name text,
  owner_user_id text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

**Neo4j Schema (Aura)**
Cypher DDL applied by `scripts/init_neo4j_schema.py`.

```cypher
CREATE CONSTRAINT contact_id_unique IF NOT EXISTS
FOR (c:Contact) REQUIRE c.contact_id IS UNIQUE;

CREATE CONSTRAINT interaction_id_unique IF NOT EXISTS
FOR (i:Interaction) REQUIRE i.interaction_id IS UNIQUE;

CREATE CONSTRAINT claim_id_unique IF NOT EXISTS
FOR (cl:Claim) REQUIRE cl.claim_id IS UNIQUE;

CREATE CONSTRAINT evidence_id_unique IF NOT EXISTS
FOR (e:Evidence) REQUIRE e.evidence_id IS UNIQUE;

CREATE INDEX contact_primary_email IF NOT EXISTS
FOR (c:Contact) ON (c.primary_email);
```

**Node Labels**
- `Contact`: `contact_id`, `primary_email`, `display_name`, `owner_user_id`
- `Interaction`: `interaction_id`, `type`, `timestamp`, `source_system`, `direction`
- `Claim`: `claim_id`, `claim_type`, `value_json`, `status`, `sensitive`, `valid_from`, `valid_to`, `confidence`, `created_at`, `source_system`
- `Evidence`: `evidence_id`, `interaction_id`, `chunk_id`, `span_json`, `quote_hash`
- `ScoreSnapshot`: `asof`, `relationship_score`, `priority_score`, `components_json`
- `Company` (optional MVP): `company_id`, `name`, `domain`

**Relationships**
- `(Contact)-[:PARTICIPATED_IN]->(Interaction)`
- `(Interaction)-[:HAS_CLAIM]->(Claim)`
- `(Claim)-[:SUPPORTED_BY]->(Evidence)`
- `(Contact)-[:HAS_CLAIM]->(Claim)`
- `(Contact)-[:HAS_SCORE]->(ScoreSnapshot)`
- `(Contact)-[:WORKS_FOR_CURRENT]->(Company)`

**n8n Workflows**
Workflows live in `n8n/workflows/` and must include retry and dead-letter handling.

**Gmail Ingest**
- Trigger: Gmail polling
- Map to `InteractionEvent`
- POST to `/v1/ingest/interaction_event` with `X-Webhook-Secret`
- On error: dead-letter log or sheet

**Transcript Ingest**
- Trigger: Drive upload or webhook
- Extract transcript text
- POST to `/v1/ingest/interaction_event` with `event_type=meeting_transcript`

**Sheets Sync**
- Trigger: daily cron and manual run
- Either call `/v1/contacts/sync` with `mode=pull` or push `rows[]`

**News Ingest**
- Trigger: webhook or RSS poll
- Fetch article text
- POST to `/v1/ingest/news_item`

**Score Recompute**
- Trigger: cron daily
- POST `/v1/admin/recompute_scores`

**Cleanup**
- Trigger: cron using `DATA_CLEANUP_SCHEDULE_CRON`
- POST `/v1/admin/cleanup`

**Worker Jobs**
All jobs must log with `interaction_id` and `contact_id`.

**process_interaction(interaction_id)**
1. Load interaction + raw text
2. Resolve participants to contacts via contact cache
3. Chunk text and store chunks
4. Embed chunks and store vectors
5. Cognee extraction to candidate claims and evidence
6. Mem0 update bundle and apply ops
7. Update scores for affected contacts
8. Mark interaction processed

**process_news(interaction_id)**
1. Chunk + embed article
2. Extract topics/entities
3. Match contacts (GraphRAG)
4. Return matches to caller or store on request

**recompute_scores()**
- Recompute scores for all contacts with recent activity

**cleanup_data()**
- Enforce retention rules, redact or delete as configured

**Scoring Models**
**Relationship Score (0 to 100)**
- Recency score: decay based on days since last meaningful interaction
- Frequency score: interaction counts over 30 and 90 days
- Reciprocity score: response times and initiation ratio
- Warmth score: relationship_signal claims with evidence
- Depth score: count of accepted non-sensitive claims with evidence

**Priority Score (0 to 100)**
- Inactivity decay
- Open loops: commitments not closed
- Triggers: news match relevance
- Temperature band modifier for suggested touch type

Every non-trivial component must include `evidence_refs` in `components_json`.

**News Matching (GraphRAG)**
1. Extract topics/entities from article
2. Candidate generation from Neo4j:
   - contacts with matching topics or claims
   - contacts with recent interactions mentioning topics
   - contacts with matching company associations
3. Rerank with vector similarity:
   - build contact profile text from recent summaries and claims
   - embed profile and compare to article embedding
4. Return top N with reason chain and citations

**Drafting Module**
**Tone Bands**
- 0 to 35: cool professional
- 36 to 70: warm professional
- 71 to 100: friendly personal

**Retrieval Bundle**
- Accepted claims (non-sensitive unless explicitly allowed)
- Last 3 meaningful interactions
- Open commitments
- Proposed contradictory claims for internal notes
- Top relevant chunks from vector search

**Draft Output Requirements**
- Must produce `draft_text` and `citations_json`
- Citations map each paragraph to evidence refs
- Sensitive claims excluded by default
- Proposed changes only mentioned as tentative with internal note

**Resolution Workflow**
- Contradictions generate `resolution_tasks` with provenance
- Resolution actions update claim statuses and relationships
- Accepting employment updates `WORKS_FOR_CURRENT`

**UI Obligations (Next.js)**
- Today: priority list with score and reasons
- Contact: timeline, claims, proposed changes, score trend
- News: paste article and show matched contacts
- Drafts: generate, view citations, approve or discard
- Resolution: queue of tasks with accept/reject/edit

**Acceptance Criteria and Sample Tests**
**Ingestion**
- Ingested emails appear in `raw_events` and `interactions`
- Duplicate `external_id` does not create duplicates
- `process_interaction` is enqueued exactly once

Sample tests:
1. POST a Gmail message twice and assert a single `raw_events` row exists.
2. POST a transcript and verify `interactions.type=meeting`.

**Extraction and Memory**
- Cognee creates proposed claims with evidence nodes
- Mem0 ops apply and contradictions create resolution tasks

Sample tests:
1. Ingest an email with a job change and verify a resolution task is created.
2. Verify claims without evidence are not attached to drafts.

**Scoring**
- Scores update after ingestion and daily recompute
- Score explanations include evidence refs

Sample tests:
1. Ingest two interactions 10 days apart and verify recency changes.
2. Verify `components_json` includes evidence for warmth or commitments.

**News Matching**
- News match returns ranked contacts with reason chain
- No persistence of request or result

Sample tests:
1. POST a news article and ensure no new database rows are created for matches.
2. Validate top contact has topic overlap evidence.

**Drafting**
- Draft tone matches relationship band
- Draft includes citations and excludes sensitive claims by default

Sample tests:
1. Generate draft for low-score contact and check tone band label.
2. Verify citations map to chunk and interaction ids.

**Resolution**
- Resolution updates claim statuses and graph relationships
- Accepted employment updates `WORKS_FOR_CURRENT`

Sample tests:
1. Resolve employment discrepancy and verify the relationship is updated.
2. Reject proposed claim and verify status changes to `rejected`.

**Cleanup**
- Cleanup respects retention config and can be disabled

Sample tests:
1. Set retention to 1 day and verify old raw events are deleted.
2. Disable cleanup and verify no deletion occurs on cron.
