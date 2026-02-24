# Quickstart (Docker + Your Contacts/Gmail/Slack/Transcripts)

This runbook is for testing the CRM end-to-end with your own contact list and live data sources.

It covers:
- starting the stack in Docker
- optional data reset (Postgres, Neo4j, n8n state)
- loading your contacts
- Gmail backfill
- live Gmail polling
- Slack ingest (webhook)
- transcript ingest (Google Drive folder monitor or direct webhook)

## 0. What You Need Before Starting

What this step does: prepares the credentials and data sources the workflows depend on.

- A `.env` file (`cp .env.example .env`)
- A Postgres database DSN (`NEON_PG_DSN`) for app tables
- OpenAI credentials (if used in your environment)
- Google OAuth credentials in n8n for:
  - Gmail (Gmail Trigger / Gmail Get Many)
  - Google Sheets (contact list sync + Gmail backfill queue build)
  - Google Drive (transcript folder monitor)
- A contact list (recommended: Google Sheet with a `Contacts` tab)
- Optional Slack source that can POST events to an n8n webhook

Recommended production-like setting:
- `COGNEE_ENABLE_HEURISTIC_FALLBACK=false` (strict extraction mode)

## 1. Configure `.env`

What this step does: sets runtime config for API, worker, Neo4j, n8n, and extraction behavior.

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
cp .env.example .env
```

Set or verify at minimum:
- `NEON_PG_DSN=postgresql://...` (use a dev/test database, not production)
- `NEO4J_URI=neo4j://neo4j:7687`
- `NEO4J_USER=neo4j`
- `NEO4J_PASSWORD=changeme` (or your own)
- `N8N_WEBHOOK_SECRET=<long-random-secret>`
- `N8N_PORT=5679`
- `QUEUE_MODE=redis`
- `COGNEE_BACKEND=local`
- `MEM0_BACKEND=local`
- `COGNEE_ENABLE_HEURISTIC_FALLBACK=false` (prod-like strict mode)
- `GRAPH_V2_ENABLED=true`
- `GRAPH_V2_DUAL_WRITE=true`

Note:
- Any `.env` change requires restarting `api` and `worker` (and `n8n` if the change affects n8n).

## 2. Start the Stack (Docker)

What this step does: starts API, worker, Neo4j, Redis, n8n, and optionally UI.

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose up -d --build api worker neo4j redis n8n ui
```

Fast restart after config-only changes:
```bash
docker compose restart api worker n8n
```

Services:
- API: `http://localhost:8000/v1`
- UI: `http://localhost:3000`
- Neo4j Browser: `http://localhost:7477` (mapped from container `7474` in this repo)
- Neo4j Bolt: `localhost:7690` (mapped from container `7687`)
- n8n: `http://localhost:5679` (or your `N8N_PORT`)
- Redis: `localhost:6379`

## 3. Run DB Migrations

What this step does: ensures the Postgres schema matches the current code (including V2 changes).

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose exec -T api sh -lc 'cd /workspace/apps/api && /opt/venv/bin/alembic -c alembic.ini upgrade head'
```

## 4. Initialize Neo4j Schema + Ontology + SHACL

What this step does: creates graph constraints/indexes and loads ontology/SHACL assets used by the V2 graph model.

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose exec -T api sh -lc 'cd /workspace/apps/api && /opt/venv/bin/python /workspace/scripts/init_neo4j_schema.py && /opt/venv/bin/python /workspace/scripts/load_ontology_and_shacl.py'
```

Optional manual inference run:
```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose exec -T api sh -lc 'cd /workspace/apps/api && /opt/venv/bin/python /workspace/scripts/run_inference_rulepack.py'
```

## 5. Optional: Clear Existing Data (Postgres / Neo4j / n8n)

What this step does: resets prior example/test data so you can run a clean trial with your own accounts.

Do this only against a dev/test environment.

### 5A. Reset local Docker state (Neo4j + Redis + container state)

What this clears: local Neo4j graph volume, Redis data, container state.

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose down -v
docker compose up -d --build api worker neo4j redis n8n ui
```

Then rerun:
- Step 3 (migrations)
- Step 4 (Neo4j schema + ontology/SHACL)

### 5B. Clear Postgres app tables (keeps schema, deletes app data)

What this clears: `raw_events`, `interactions`, `chunks`, `embeddings`, `drafts`, `resolution_tasks`, `contact_cache`.

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose exec -T api sh -lc 'cd /workspace/apps/api && /opt/venv/bin/python - <<\"PY\"
from app.db.pg.session import engine
with engine.begin() as conn:
    conn.exec_driver_sql(\"\"\"
        TRUNCATE TABLE
            embeddings,
            chunks,
            drafts,
            resolution_tasks,
            interactions,
            raw_events,
            contact_cache
        RESTART IDENTITY CASCADE
    \"\"\")
print(\"Postgres app tables cleared.\")
PY'
```

### 5C. Clear Neo4j graph only (keep container running)

What this clears: all graph nodes/relationships (canonical/case/evidence layers and legacy graph nodes).

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose exec -T neo4j cypher-shell -u neo4j -p changeme "MATCH (n) DETACH DELETE n;"
```

Then rerun Step 4:
```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose exec -T api sh -lc 'cd /workspace/apps/api && /opt/venv/bin/python /workspace/scripts/init_neo4j_schema.py && /opt/venv/bin/python /workspace/scripts/load_ontology_and_shacl.py'
```

### 5D. Clear n8n runtime state (optional)

What this clears: n8n credentials, executions, and activated workflows stored in the local `n8n` folder database.

This does **not** delete workflow JSON exports in `n8n/workflows/`.

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
rm -f n8n/database.sqlite n8n/database.sqlite-shm n8n/database.sqlite-wal n8n/crash.journal
docker compose restart n8n
```

## 6. Smoke Check Core Services

What this step does: confirms the API and worker are reachable before you configure connectors.

```bash
curl -s http://localhost:8000/v1/health
docker compose ps
docker compose logs --tail=50 worker
```

If running strict extraction mode (`COGNEE_ENABLE_HEURISTIC_FALLBACK=false`), keep an eye on worker logs during testing:
```bash
docker compose logs -f worker
```

## 7. Open n8n and Import Workflows

What this step does: loads the repo’s connector workflows into your n8n instance.

1. Open `http://localhost:5679` (or your `N8N_PORT`).
2. Import these workflow JSON files from `/Users/dobrien/code/Lux/Lux_CRM_agent/n8n/workflows/`:
   - `sheets_sync.json`
   - `gmail_contact_backfill.json`
   - `gmail_ingest.json`
   - `slack_ingest.json`
   - `transcript_folder_monitor.json`
   - `transcript_ingest.json`
   - Optional support workflows:
     - `score_recompute.json`
     - `inference_recompute.json`
     - `data_cleanup.json`

## 8. Configure n8n Credentials (Google / Slack)

What this step does: authorizes n8n to read your Gmail, Google Sheets, and Google Drive.

In n8n, create and assign credentials for:
- Gmail OAuth2 (used by `gmail_ingest` and `gmail_contact_backfill`)
- Google Sheets OAuth2 (used by `sheets_sync` and `gmail_contact_backfill`)
- Google Drive OAuth2 (used by `transcript_folder_monitor`)

Slack:
- `slack_ingest` is a webhook-based workflow.
- You can send Slack events from your Slack app/automation into the n8n webhook URL (see Step 12A).

## 9. Load Your Contact List (Recommended First)

What this step does: seeds canonical contacts in Postgres and CRM graph so interactions can be linked correctly.

### Option A (recommended): Google Sheets via `sheets_sync`

Use this if your contact list is in Google Sheets.

1. Create/prepare a Google Sheet with a `Contacts` tab.
2. Include at least:
   - `contact_id`
   - `primary_email`
3. Recommended additional columns:
   - `display_name`, `first_name`, `last_name`, `company`, `owner_user_id`, `notes`, `use_sensitive_in_drafts`
4. In n8n `sheets_sync`:
   - Update `Read Contacts Sheet` node:
     - `sheetId`
     - `range` (default `Contacts!A:Z`)
5. Run `sheets_sync` manually once.

What it does:
- Reads your sheet
- Normalizes rows
- Calls `POST /v1/contacts/sync`
- Populates/updates `contact_cache` and contact graph nodes

### Option B: Direct API import via `POST /v1/contacts/sync`

Use this if you want to push a JSON file directly.

```bash
curl -sS -X POST http://localhost:8000/v1/contacts/sync \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: <YOUR_N8N_WEBHOOK_SECRET>" \
  -d '{
    "mode": "push",
    "rows": [
      {
        "contact_id": "contact_001",
        "primary_email": "person@example.com",
        "display_name": "Example Person",
        "company": "Example Co",
        "use_sensitive_in_drafts": false
      }
    ]
  }'
```

## 10. Run Gmail Contact Backfill (Historical Email)

What this step does: backfills prior Gmail messages for contacts from your sheet into the ingestion pipeline.

1. In n8n `gmail_contact_backfill`, update:
   - `Read Contacts Sheet` node:
     - `sheetId`
     - `range`
   - `Build Contact Queue` code node constants:
     - `YEARS_BACK`
     - `WINDOW_SIZE_MONTHS`
     - `MAX_CONTACTS_PER_RUN`
     - `BACKFILL_CONTACT_MODE` (`skip_previously_processed` or `reprocess_all`)
2. Run the workflow manually.

What it does:
- Reads contact emails from your sheet
- Builds Gmail search windows per contact
- Fetches messages from Gmail
- Maps them to `/v1/ingest/interaction_event`
- Sends them to API in batches
- Records dead-letter items and a backfill summary

Important:
- In strict mode (`COGNEE_ENABLE_HEURISTIC_FALLBACK=false`), some interactions may ingest successfully but later fail during worker extraction if Cognee extraction fails.

## 11. Enable Live Gmail Listening (New Email)

What this step does: continuously polls Gmail (every minute) and sends new messages into the CRM ingestion API.

1. Open `gmail_ingest` in n8n.
2. Attach your Gmail OAuth2 credential.
3. Activate the workflow.

What it does:
- Gmail Trigger polls every minute
- Maps inbound/outbound messages to the normalized interaction payload
- Calls `POST /v1/ingest/interaction_event`

## 12. Enable Slack and Transcript Intake

What this step does: lets the system ingest non-email interactions for richer relationship and opportunity context.

### 12A. Slack (`slack_ingest`)

1. Open `slack_ingest` in n8n.
2. Activate the workflow.
3. Send Slack message payloads to the n8n webhook:
   - n8n webhook path in workflow: `slack-ingest`
   - Typical URL: `http://localhost:5679/webhook/slack-ingest`

Example test payload:
```bash
curl -sS -X POST http://localhost:5679/webhook/slack-ingest \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "slack-test-001",
    "timestamp": "2026-02-22T12:00:00Z",
    "thread_id": "slack-thread-001",
    "channel": "customer-pilot",
    "from": {"email": "person@example.com", "name": "Example Person"},
    "to": [{"email": "owner@luxcrm.ai", "name": "Owner"}],
    "text": "We need a lightweight onboarding path and want to decide this month."
  }'
```

### 12B. Transcripts (two patterns)

#### Option 1: `transcript_folder_monitor` (Google Drive text files)

What it does:
- Runs hourly
- Searches Google Drive for text files
- Maps files to `meeting_notes` interactions
- Sends them to the API

Steps:
1. Open `transcript_folder_monitor`.
2. Attach Google Drive OAuth2 credential.
3. Update `Search Transcript Files` query to target your folder/file pattern (current default is broad `mimeType='text/plain' and trashed=false`).
4. Activate the workflow.

Note:
- This workflow monitors **Google Drive**, not a local filesystem directory.

#### Option 2: `transcript_ingest` (direct webhook from your transcription pipeline)

What it does:
- Accepts transcript payloads over HTTP
- Maps them to the normalized transcript interaction payload
- Sends them to the API

Steps:
1. Open `transcript_ingest`.
2. Activate the workflow.
3. POST transcript payloads to:
   - `http://localhost:5679/webhook/transcript-ingest`

Example test payload:
```bash
curl -sS -X POST http://localhost:5679/webhook/transcript-ingest \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "transcript-test-001",
    "timestamp": "2026-02-22T12:15:00Z",
    "thread_id": "meeting-001",
    "subject": "Acme pilot discovery call",
    "participants": {
      "from": [{"email": "owner@luxcrm.ai", "name": "Owner"}],
      "to": [{"email": "person@example.com", "name": "Example Person"}],
      "cc": []
    },
    "body_plain": "Customer wants short weekly updates, quick decisions, and a clear milestone owner/date."
  }'
```

## 13. Verify Data Is Flowing End-to-End

What this step does: confirms the system is ingesting, extracting, creating case entities, and exposing scoring/drafting outputs.

Check API health and top-level outputs:
```bash
curl -s http://localhost:8000/v1/health
curl -s http://localhost:8000/v1/scores/today
curl -s "http://localhost:8000/v1/cases/contacts?status=open"
curl -s "http://localhost:8000/v1/cases/opportunities?status=open"
```

Useful monitoring during live testing:
```bash
docker compose logs -f api worker n8n
```

What to expect:
- Unknown external participants create provisional `CaseContact`s automatically
- Opportunity-like messages can create `CaseOpportunity`s (or link to existing opportunities)
- Evidence-backed assertions are written to the graph
- Scores and drafts reflect relationship/opportunity context over time

## 14. (Optional) Run the Automated Validation Harness

What this step does: runs a synthetic, reproducible end-to-end test with fixture payloads to validate the pipeline.

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
./run_e2e_validation.sh
```

For strict graph assertions:
```bash
NEO4J_ASSERT=on ./run_e2e_validation.sh
```

## 15. Stop Services

What this step does: stops local containers. Use `-v` only if you intentionally want to drop local Docker volumes.

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose down
```

## Troubleshooting Notes

- `COGNEE_ENABLE_HEURISTIC_FALLBACK=false`:
  - API ingest can still return `enqueued`
  - worker may later fail the interaction if Cognee extraction fails
  - monitor `docker compose logs -f worker`
- `gmail_contact_backfill` and `sheets_sync` both contain hardcoded Google Sheet IDs in the exported JSON templates; update them in n8n after import.
- After wiping Neo4j, rerun schema + ontology/SHACL loading before ingesting new data.
- If you change Python code used by the worker, rebuild (`docker compose up -d --build api worker`) rather than only restarting.
