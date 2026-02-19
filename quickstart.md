# Quickstart

## 1. Backend Python Environment (required)

### Build + run:
```
docker compose up -d --build api worker
```
### Fast restarts:
```
docker compose restart api worker
```

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent/apps/api
deactivate 2>/dev/null || true
rm -rf .venv
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
python --version

pip install -U pip setuptools wheel hatchling
pip install -e .
# Install local OSS repos (non-editable avoids hatchling/editables editable hook issues)
pip install ../../../third_party/cognee --no-build-isolation
pip install ../../../third_party/mem0 --no-build-isolation
```

Optional editable installs (only if you need to edit third-party code live):
```bash
pip install editables
pip install -e ../../../third_party/cognee --no-build-isolation
pip install -e ../../../third_party/mem0 --no-build-isolation
```

## 2. Environment file

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
cp .env.example .env
```

Set at minimum:
- `NEO4J_URI=neo4j://neo4j:7687`
- `NEO4J_USER=neo4j`
- `NEO4J_PASSWORD=changeme`
- `NEO4J_HTTP_PORT=7474` (change if occupied, e.g. `7475`)
- `NEO4J_BOLT_PORT=7687` (change if occupied, e.g. `7688`)
- `QUEUE_MODE=redis`
- `COGNEE_BACKEND=local`
- `COGNEE_LOCAL_MODULE=app.integrations.cognee_oss_adapter`
- `COGNEE_LOCAL_FUNCTION=extract_candidates`
- `MEM0_BACKEND=local`
- `MEM0_LOCAL_MODULE=app.integrations.mem0_oss_adapter`
- `MEM0_LOCAL_FUNCTION=propose_memory_ops`

## 3. Run full stack with Docker Compose

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose up --build
```

Services:
- API: `http://localhost:8000/v1`
- UI: `http://localhost:3000`
- Neo4j Browser: `http://localhost:7474`
- n8n: `http://localhost:5679` (or `N8N_PORT` if overridden)
- Redis: `localhost:6379`

## 4. Initialize Neo4j schema

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
source apps/api/.venv/bin/activate
python scripts/init_neo4j_schema.py
```

## 5. Run API without Docker (optional)

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent/apps/api
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

## 6. Smoke checks

```bash
curl http://localhost:8000/v1/health
curl http://localhost:8000/v1/scores/today
```

## 7. Dependency conflict recovery (if pip reports Mem0 conflicts)

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent/apps/api
source .venv/bin/activate
pip install "protobuf>=5.29,<6" "posthog>=3.5.0" "qdrant-client>=1.9.1"
pip install ../../../third_party/cognee --no-build-isolation
pip install ../../../third_party/mem0 --no-build-isolation
pip check
```

## 8. Stop services

```bash
cd /Users/dobrien/code/Lux/Lux_CRM_agent
docker compose down
```
