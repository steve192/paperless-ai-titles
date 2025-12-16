#!/usr/bin/env bash
set -euo pipefail

DOCUMENT_ID="$1"

if [[ -z "${DOCUMENT_ID}" ]]; then
  echo "Usage: $0 <document_id>" >&2
  exit 1
fi

API_BASE="${PAPERLESS_AI_TITLES_URL:-http://localhost:8080}"
HOOK_TOKEN="${PAPERLESS_AI_HOOK_TOKEN:-}"

JSON_PAYLOAD="{\"document_id\": ${DOCUMENT_ID}"

if [[ -n "${HOOK_TOKEN}" ]]; then
  JSON_PAYLOAD+=", \"token\": \"${HOOK_TOKEN}\""
fi

JSON_PAYLOAD+="}"

curl -sSf \
  -H "Content-Type: application/json" \
  -X POST \
  -d "${JSON_PAYLOAD}" \
  "${API_BASE%/}/api/hooks/paperless" >/dev/null

echo "Queued document ${DOCUMENT_ID} via ${API_BASE}/api/hooks/paperless"
