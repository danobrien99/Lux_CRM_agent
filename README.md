# Lux_CRM_agent

CRM relationship intelligence augmentation platform.

## What is implemented
- FastAPI backend scaffold with contract-driven routes under `/v1`
- SQLAlchemy models for raw events, interactions, chunks, embeddings, drafts, and resolution tasks
- Neo4j schema bootstrapping script and graph query helpers
- RQ worker job skeletons for ingestion, scoring, and cleanup
- Local-first Cognee and Mem0 adapter hooks for open-source codebase integration
- n8n workflow JSON templates for Gmail, transcripts, contacts sync, news, score recompute, and cleanup
- Next.js MVP UI pages for priority list, contact detail, news matching, drafts, and resolution queue

## Repository layout
- `AGENT.md`: master architecture and contract specification
- `apps/api`: backend API and workers
- `apps/ui`: Next.js UI
- `n8n/workflows`: workflow exports
- `scripts`: operational scripts

## Local development
1. Copy `.env.example` to `.env` and fill in credentials.
2. Start services:
```bash
docker compose up
```
3. API base URL: `http://localhost:8000/v1`
4. UI URL: `http://localhost:3000`
5. n8n URL: `http://localhost:${N8N_PORT:-5679}`

## Backend standalone
```bash
cd apps/api
# Use Python 3.11 or 3.12 (3.14 is not supported by Cognee).
pip install -e .
uvicorn app.main:app --reload --port 8000
```

## Local OSS adapters
Use local open-source packages for extraction and memory:
```bash
cd apps/api
# If repos are at /Users/dobrien/code/Lux/Third_Party/
pip install ../../../Third_Party/cognee --no-build-isolation
pip install ../../../Third_Party/mem0 --no-build-isolation
```
For editable installs, first install `editables`, then use `pip install -e ... --no-build-isolation`.

Default adapter entrypoints are configured in `.env.example`:
- `COGNEE_LOCAL_MODULE=app.integrations.cognee_oss_adapter`
- `MEM0_LOCAL_MODULE=app.integrations.mem0_oss_adapter`
- `COGNEE_REPO_PATH=../Third_Party/cognee`
- `MEM0_REPO_PATH=../Third_Party/mem0`

Fallback policy:
- `COGNEE_ENABLE_HEURISTIC_FALLBACK=false` by default.
- `MEM0_ENABLE_RULES_FALLBACK=false` by default.
- Set these to `true` only as temporary rescue mode in local development.

Backfill behavior:
- `BACKFILL_CONTACT_MODE=skip_previously_processed` skips contacts that already have processed interactions.
- `BACKFILL_CONTACT_MODE=reprocess_all` processes all contacts and requeues duplicate interactions for reprocessing.
- `BACKFILL_API_BASE_URL` is used by the n8n backfill workflow to read processed-contact status.
- `BACKFILL_YEARS_BACK`, `BACKFILL_WINDOW_SIZE_MONTHS`, and `BACKFILL_MAX_CONTACTS_PER_RUN` control backfill range/window/limit without editing workflow code.

## Tests
```bash
cd apps/api
pytest app/tests -q
```

## Notes
- `.CODEX/CODEX_CRM_agent_overview.md` is retained as deprecated historical notes.
- `AGENT.md` is the active source of truth.
- Cognee and Mem0 defaults are local OSS adapters; cloud API usage is not required.
