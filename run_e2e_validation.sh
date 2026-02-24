#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE_DIR="${ROOT_DIR}/e2e_fixtures"
BASE_URL="${BASE_URL:-http://localhost:8000/v1}"
POLL_TIMEOUT_SECONDS="${POLL_TIMEOUT_SECONDS:-240}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-3}"
NEO4J_ASSERT="${NEO4J_ASSERT:-auto}" # auto|on|off

load_env_file() {
  local env_file="$1"
  local line key value
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line}" ]] && continue
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" != *"="* ]] && continue

    key="${line%%=*}"
    value="${line#*=}"

    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"

    if [[ ! "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      continue
    fi

    export "${key}=${value}"
  done < "${env_file}"
}

if [[ -f "${ROOT_DIR}/.env" ]]; then
  load_env_file "${ROOT_DIR}/.env"
fi

required_bins=(curl python3)
for bin in "${required_bins[@]}"; do
  if ! command -v "${bin}" >/dev/null 2>&1; then
    echo "Missing required command: ${bin}" >&2
    exit 1
  fi
done

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { log "FAIL: $*"; exit 1; }
pass() { PASS_COUNT=$((PASS_COUNT + 1)); log "PASS: $*"; }

PASS_COUNT=0

WEBHOOK_SECRET="${N8N_WEBHOOK_SECRET:-}"
HEADER_ARGS=()
if [[ -n "${WEBHOOK_SECRET}" ]]; then
  HEADER_ARGS=(-H "X-Webhook-Secret: ${WEBHOOK_SECRET}")
fi

http_post_file() {
  local path="$1"
  local file="$2"
  local response http body
  response="$(curl -sS -X POST "${BASE_URL}${path}" "${HEADER_ARGS[@]}" -H "Content-Type: application/json" --data @"${file}" -w $'\n%{http_code}')"
  http="${response##*$'\n'}"
  body="${response%$'\n'*}"
  if [[ ! "${http}" =~ ^2 ]]; then
    echo "${body}" >&2
    fail "POST ${path} failed (HTTP ${http})"
  fi
  printf '%s' "${body}"
}

http_get() {
  local path="$1"
  local response http body
  response="$(curl -sS "${BASE_URL}${path}" "${HEADER_ARGS[@]}" -w $'\n%{http_code}')"
  http="${response##*$'\n'}"
  body="${response%$'\n'*}"
  if [[ ! "${http}" =~ ^2 ]]; then
    echo "${body}" >&2
    fail "GET ${path} failed (HTTP ${http})"
  fi
  printf '%s' "${body}"
}

json_assert() {
  local label="$1"
  local body="$2"
  local python_expr="$3"
  BODY_JSON="${body}" PY_EXPR="${python_expr}" python3 - <<'PY'
import json
import os
import sys

body = os.environ["BODY_JSON"]
expr = os.environ["PY_EXPR"]
payload = json.loads(body)
ns = {"payload": payload}
ok = bool(eval(expr, {}, ns))
if not ok:
    print(json.dumps(payload, indent=2))
    sys.exit(1)
PY
  if [[ $? -ne 0 ]]; then
    fail "${label}"
  fi
}

json_eval() {
  local body="$1"
  local python_expr="$2"
  BODY_JSON="${body}" PY_EXPR="${python_expr}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["BODY_JSON"])
expr = os.environ["PY_EXPR"]
value = eval(expr, {}, {"payload": payload})
if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PY
}

RUN_TS="$(date -u +%Y%m%d%H%M%S)"
RUN_ID="e2e-${RUN_TS}"
OWNER_CONTACT_ID="contact-owner-${RUN_TS}"
EXISTING_CONTACT_ID="contact-existing-${RUN_TS}"
OWNER_EMAIL="owner.${RUN_TS}@luxcrm.ai"
EXISTING_EMAIL="existing.${RUN_TS}@acme-example.com"
UNKNOWN_EMAIL="prospect.${RUN_TS}@example.com"
THREAD_UNKNOWN="thread-unknown-${RUN_TS}"
THREAD_OPP="thread-opp-${RUN_TS}"
UNKNOWN_EXTERNAL_EVENT_ID="evt-unknown-${RUN_TS}"
OPP_EVENT_ID="evt-opp-${RUN_TS}"
OPP_FOLLOWUP_EVENT_ID="evt-opp-followup-${RUN_TS}"

TS_SERIALIZED="$(python3 - <<'PY'
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc).replace(microsecond=0)
values = []
for minute_offset in (0, 1, 2):
    values.append((now + timedelta(minutes=minute_offset)).isoformat().replace("+00:00", "Z"))
print("|".join(values))
PY
)"
IFS='|' read -r TIMESTAMP_1 TIMESTAMP_2 TIMESTAMP_3 <<< "${TS_SERIALIZED}"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

render_template() {
  local template_path="$1"
  local output_path="$2"
  sed \
    -e "s|{{RUN_ID}}|${RUN_ID}|g" \
    -e "s|{{OWNER_CONTACT_ID}}|${OWNER_CONTACT_ID}|g" \
    -e "s|{{EXISTING_CONTACT_ID}}|${EXISTING_CONTACT_ID}|g" \
    -e "s|{{OWNER_EMAIL}}|${OWNER_EMAIL}|g" \
    -e "s|{{EXISTING_EMAIL}}|${EXISTING_EMAIL}|g" \
    -e "s|{{UNKNOWN_EMAIL}}|${UNKNOWN_EMAIL}|g" \
    -e "s|{{THREAD_UNKNOWN}}|${THREAD_UNKNOWN}|g" \
    -e "s|{{THREAD_OPP}}|${THREAD_OPP}|g" \
    -e "s|{{UNKNOWN_EXTERNAL_EVENT_ID}}|${UNKNOWN_EXTERNAL_EVENT_ID}|g" \
    -e "s|{{OPP_EVENT_ID}}|${OPP_EVENT_ID}|g" \
    -e "s|{{OPP_FOLLOWUP_EVENT_ID}}|${OPP_FOLLOWUP_EVENT_ID}|g" \
    -e "s|{{TIMESTAMP_1}}|${TIMESTAMP_1}|g" \
    -e "s|{{TIMESTAMP_2}}|${TIMESTAMP_2}|g" \
    -e "s|{{TIMESTAMP_3}}|${TIMESTAMP_3}|g" \
    "$template_path" > "$output_path"
}

render_template "${FIXTURE_DIR}/contacts_seed.json.template" "${TMP_DIR}/contacts_seed.json"
render_template "${FIXTURE_DIR}/interaction_unknown_external.json.template" "${TMP_DIR}/interaction_unknown_external.json"
render_template "${FIXTURE_DIR}/interaction_new_opportunity.json.template" "${TMP_DIR}/interaction_new_opportunity.json"
render_template "${FIXTURE_DIR}/interaction_followup_opportunity.json.template" "${TMP_DIR}/interaction_followup_opportunity.json"

log "Run ID: ${RUN_ID}"
log "Base URL: ${BASE_URL}"

log "1) Seed canonical contacts"
contacts_sync_resp="$(http_post_file "/contacts/sync" "${TMP_DIR}/contacts_seed.json")"
json_assert "contacts sync did not upsert 2 rows" "${contacts_sync_resp}" "payload.get('upserted') == 2"
pass "contacts seeded"

log "2) Ingest unknown external interaction"
unknown_ingest_resp="$(http_post_file "/ingest/interaction_event" "${TMP_DIR}/interaction_unknown_external.json")"
json_assert "unknown ingest status should be enqueued/requeued" "${unknown_ingest_resp}" "payload.get('status') in {'enqueued','requeued'}"
UNKNOWN_INTERACTION_ID="$(json_eval "${unknown_ingest_resp}" "payload.get('interaction_id')")"
[[ -n "${UNKNOWN_INTERACTION_ID}" ]] || fail "missing interaction_id for unknown interaction ingest"
pass "unknown interaction accepted (${UNKNOWN_INTERACTION_ID})"

log "3) Wait for CaseContact auto-creation and evidence"
start_wait="$(date +%s)"
while true; do
  open_cases_poll="$(http_get "/cases/contacts?status=open")"
  if OPEN_CASES_JSON="${open_cases_poll}" CHECK_EMAIL="${UNKNOWN_EMAIL}" python3 - <<'PY'
import json
import os
import sys
payload=json.loads(os.environ["OPEN_CASES_JSON"])
email=os.environ["CHECK_EMAIL"].lower()
for item in payload.get('items', []):
    if str(item.get('email', '')).lower() == email and str(item.get('entity_status')) == 'provisional' and int(item.get('evidence_count', 0)) >= 1:
        raise SystemExit(0)
raise SystemExit(1)
PY
  then
    pass "case contact created for unknown external participant"
    break
  fi
  now_wait="$(date +%s)"
  if (( now_wait - start_wait >= POLL_TIMEOUT_SECONDS )); then
    fail "case contact created for unknown external participant (timed out after ${POLL_TIMEOUT_SECONDS}s)"
  fi
  sleep "${POLL_INTERVAL_SECONDS}"
done

open_cases_resp="$(http_get "/cases/contacts?status=open")"
CASE_CONTACT_JSON="$(OPEN_CASES_JSON="${open_cases_resp}" CHECK_EMAIL="${UNKNOWN_EMAIL}" python3 - <<'PY'
import json
import os
payload=json.loads(os.environ["OPEN_CASES_JSON"])
email=os.environ["CHECK_EMAIL"].lower()
for item in payload.get('items',[]):
    if str(item.get('email','')).lower()==email:
        print(json.dumps(item))
        break
PY
)"
[[ -n "${CASE_CONTACT_JSON}" ]] || fail "unable to find CaseContact JSON for ${UNKNOWN_EMAIL}"
CASE_CONTACT_ID="$(json_eval "${CASE_CONTACT_JSON}" "payload.get('case_id')")"
PROVISIONAL_CONTACT_ID="$(json_eval "${CASE_CONTACT_JSON}" "payload.get('provisional_contact_id')")"
[[ -n "${CASE_CONTACT_ID}" ]] || fail "case contact id not found"
[[ -n "${PROVISIONAL_CONTACT_ID}" ]] || fail "provisional contact id not found"
pass "CaseContact detected (${CASE_CONTACT_ID})"

log "4) Validate promotion gate failure (without override)"
promote_fail_resp="$(http_post_file "/cases/contacts/${CASE_CONTACT_ID}/promote" "${FIXTURE_DIR}/promote_case_contact_fail.json")"
json_assert "case contact promotion should remain provisional without override" \
  "${promote_fail_resp}" \
  "payload.get('status') == 'open' and payload.get('entity_status') == 'provisional' and payload.get('promoted_id') is None"
pass "promotion gate blocks weak/unapproved promotion as expected"

log "5) Promote CaseContact with override"
promote_success_resp="$(http_post_file "/cases/contacts/${CASE_CONTACT_ID}/promote" "${FIXTURE_DIR}/promote_case_contact_override.json")"
json_assert "case contact promotion override did not produce canonical contact" \
  "${promote_success_resp}" \
  "payload.get('status') == 'promoted' and payload.get('entity_status') == 'canonical' and bool(payload.get('promoted_id'))"
PROMOTED_CONTACT_ID="$(json_eval "${promote_success_resp}" "payload.get('promoted_id')")"
[[ -n "${PROMOTED_CONTACT_ID}" ]] || fail "missing promoted contact id"
pass "CaseContact promoted (${PROMOTED_CONTACT_ID})"

log "6) Ingest interaction expected to open CaseOpportunity"
opp_ingest_resp="$(http_post_file "/ingest/interaction_event" "${TMP_DIR}/interaction_new_opportunity.json")"
json_assert "opportunity ingress status should be enqueued/requeued" "${opp_ingest_resp}" "payload.get('status') in {'enqueued','requeued'}"

start_wait="$(date +%s)"
while true; do
  open_opp_poll="$(http_get "/cases/opportunities?status=open")"
  if OPEN_OPP_JSON="${open_opp_poll}" CHECK_THREAD="${THREAD_OPP}" python3 - <<'PY'
import json
import os
payload=json.loads(os.environ["OPEN_OPP_JSON"])
thread=os.environ["CHECK_THREAD"]
for item in payload.get('items', []):
    if str(item.get('thread_id')) == thread and str(item.get('entity_status')) == 'provisional':
        raise SystemExit(0)
raise SystemExit(1)
PY
  then
    pass "case opportunity opened for new thread"
    break
  fi
  now_wait="$(date +%s)"
  if (( now_wait - start_wait >= POLL_TIMEOUT_SECONDS )); then
    fail "case opportunity opened for new thread (timed out after ${POLL_TIMEOUT_SECONDS}s)"
  fi
  sleep "${POLL_INTERVAL_SECONDS}"
done

open_opp_resp="$(http_get "/cases/opportunities?status=open")"
CASE_OPP_JSON="$(OPEN_OPP_JSON="${open_opp_resp}" CHECK_THREAD="${THREAD_OPP}" python3 - <<'PY'
import json
import os
payload=json.loads(os.environ["OPEN_OPP_JSON"])
thread=os.environ["CHECK_THREAD"]
for item in payload.get('items',[]):
    if str(item.get('thread_id'))==thread:
        print(json.dumps(item))
        break
PY
)"
[[ -n "${CASE_OPP_JSON}" ]] || fail "unable to find CaseOpportunity JSON for ${THREAD_OPP}"
CASE_OPP_ID="$(json_eval "${CASE_OPP_JSON}" "payload.get('case_id')")"
[[ -n "${CASE_OPP_ID}" ]] || fail "missing case opportunity id"
json_assert "CaseOpportunity motivators were not populated" "${CASE_OPP_JSON}" "len(payload.get('motivators') or []) >= 1"
pass "CaseOpportunity opened with motivators (${CASE_OPP_ID})"

log "7) Promote CaseOpportunity to canonical opportunity"
promote_opp_resp="$(http_post_file "/cases/opportunities/${CASE_OPP_ID}/promote" "${FIXTURE_DIR}/promote_case_opportunity.json")"
json_assert "case opportunity promotion failed" "${promote_opp_resp}" \
  "payload.get('status') == 'promoted' and payload.get('entity_status') == 'canonical' and bool(payload.get('promoted_id'))"
PROMOTED_OPP_ID="$(json_eval "${promote_opp_resp}" "payload.get('promoted_id')")"
[[ -n "${PROMOTED_OPP_ID}" ]] || fail "missing promoted opportunity id"
pass "CaseOpportunity promoted (${PROMOTED_OPP_ID})"

log "8) Ingest follow-up interaction on same thread"
followup_ingest_resp="$(http_post_file "/ingest/interaction_event" "${TMP_DIR}/interaction_followup_opportunity.json")"
json_assert "follow-up ingest status should be enqueued/requeued" "${followup_ingest_resp}" "payload.get('status') in {'enqueued','requeued'}"
FOLLOWUP_INTERACTION_ID="$(json_eval "${followup_ingest_resp}" "payload.get('interaction_id')")"
[[ -n "${FOLLOWUP_INTERACTION_ID}" ]] || fail "missing follow-up interaction id"
sleep 5

open_opp_after_followup="$(http_get "/cases/opportunities?status=open")"
OPEN_OPP_JSON="${open_opp_after_followup}" CHECK_THREAD="${THREAD_OPP}" python3 - <<'PY'
import json
import os
payload=json.loads(os.environ["OPEN_OPP_JSON"])
thread=os.environ["CHECK_THREAD"]
for item in payload.get("items", []):
    if str(item.get("thread_id")) == thread:
        raise SystemExit(1)
PY
if [[ $? -ne 0 ]]; then
  fail "unexpected open CaseOpportunity remained/was created for promoted thread ${THREAD_OPP}"
fi
pass "no open CaseOpportunity remains for promoted thread"

log "9) Draft generation must include provenance trace"
render_template "${FIXTURE_DIR}/draft_request.json.template" "${TMP_DIR}/draft_request.json"
sed -i.bak "s|{{PROMOTED_CONTACT_ID}}|${PROMOTED_CONTACT_ID}|g" "${TMP_DIR}/draft_request.json" && rm -f "${TMP_DIR}/draft_request.json.bak"
draft_resp="$(http_post_file "/drafts" "${TMP_DIR}/draft_request.json")"
json_assert "draft response missing retrieval trace with evidence provenance" "${draft_resp}" \
  "isinstance(payload.get('retrieval_trace'), dict) and isinstance(payload['retrieval_trace'].get('assertion_evidence_trace'), list) and len(payload['retrieval_trace'].get('assertion_evidence_trace')) >= 1 and 'motivator_signals' in payload['retrieval_trace']"
pass "draft retrieval trace includes assertion evidence and motivator signals"

log "10) Score endpoint should return promoted contact"
score_resp="$(http_get "/scores/contact/${PROMOTED_CONTACT_ID}")"
json_assert "score endpoint did not return promoted contact id" "${score_resp}" "payload.get('contact_id') == '${PROMOTED_CONTACT_ID}'"
pass "score detail available for promoted contact"

log "11) Trigger inference job"
echo '{}' > "${TMP_DIR}/empty.json"
inference_resp="$(http_post_file "/admin/run_inference" "${TMP_DIR}/empty.json")"
json_assert "inference endpoint did not enqueue job" "${inference_resp}" "payload.get('status') == 'enqueued' and bool(payload.get('job_id'))"
pass "inference job enqueued"

run_neo4j_assertions() {
  local container="$1"
  local user="${NEO4J_USER:-neo4j}"
  local neo4j_pass="${NEO4J_PASSWORD:-changeme}"
  local query out

  query="MATCH (cc:CaseContact {case_id: '${CASE_CONTACT_ID}'})-[:PROMOTED_TO]->(c:CRMContact {external_id: '${PROMOTED_CONTACT_ID}'}) RETURN count(*) AS c;"
  out="$(docker exec "${container}" cypher-shell -u "${user}" -p "${neo4j_pass}" --format plain "${query}" 2>/dev/null | tail -n1 | tr -d '\r')"
  [[ "${out}" == "1" ]] || fail "Neo4j assertion failed: CaseContact promotion edge missing"
  pass "Neo4j promotion edge assertion"

  query="MATCH (eng:CRMEngagement {external_id: '${UNKNOWN_INTERACTION_ID}'})-[:HAS_ASSERTION]->(:KGAssertion)-[:SUPPORTED_BY]->(ev:EvidenceChunk) RETURN count(DISTINCT ev) AS c;"
  out="$(docker exec "${container}" cypher-shell -u "${user}" -p "${neo4j_pass}" --format plain "${query}" 2>/dev/null | tail -n1 | tr -d '\r')"
  [[ "${out}" =~ ^[0-9]+$ ]] || fail "Neo4j assertion failed: invalid evidence count output '${out}'"
  (( out >= 1 )) || fail "Neo4j assertion failed: no evidence chunks linked for unknown interaction"
  pass "Neo4j evidence provenance assertion"

  query="MATCH (eng:CRMEngagement {external_id: '${FOLLOWUP_INTERACTION_ID}'}) OPTIONAL MATCH (eng)-[:ENGAGED_OPPORTUNITY]->(opp:CRMOpportunity {external_id: '${PROMOTED_OPP_ID}'}) OPTIONAL MATCH (eng)-[:HAS_CASE_OPPORTUNITY]->(co:CaseOpportunity) RETURN count(opp) + count(co) AS c;"
  local waited=0
  while true; do
    out="$(docker exec "${container}" cypher-shell -u "${user}" -p "${neo4j_pass}" --format plain "${query}" 2>/dev/null | tail -n1 | tr -d '\r')"
    [[ "${out}" =~ ^[0-9]+$ ]] || fail "Neo4j assertion failed: invalid opportunity-link output '${out}'"
    if (( out >= 1 )); then
      break
    fi
    if (( waited >= POLL_TIMEOUT_SECONDS )); then
      fail "Neo4j assertion failed: follow-up engagement not linked to opportunity graph"
    fi
    sleep "${POLL_INTERVAL_SECONDS}"
    waited=$((waited + POLL_INTERVAL_SECONDS))
  done
  pass "Neo4j engagement-opportunity linkage assertion"
}

if [[ "${NEO4J_ASSERT}" != "off" ]]; then
  if command -v docker >/dev/null 2>&1; then
    NEO4J_CONTAINER="$(docker ps --format '{{.Names}}' | grep -E 'neo4j' | head -n1 || true)"
    if [[ -n "${NEO4J_CONTAINER}" ]]; then
      log "12) Running optional Neo4j assertions in container ${NEO4J_CONTAINER}"
      run_neo4j_assertions "${NEO4J_CONTAINER}"
    elif [[ "${NEO4J_ASSERT}" == "on" ]]; then
      fail "NEO4J_ASSERT=on but no running Neo4j container detected"
    else
      log "Skipping Neo4j assertions (no running Neo4j container found)"
    fi
  elif [[ "${NEO4J_ASSERT}" == "on" ]]; then
    fail "NEO4J_ASSERT=on but docker is not installed"
  else
    log "Skipping Neo4j assertions (docker not installed)"
  fi
fi

log "E2E validation completed successfully"
log "Pass checks: ${PASS_COUNT}"
log "Run ID: ${RUN_ID}"
