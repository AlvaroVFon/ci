#!/usr/bin/env bash
# Exchange the GitHub Actions OIDC token for a short-lived OpenCode key.
# Falls back to the OPENCODE_API_KEY secret when one is provided.
set -euo pipefail

: "${GITHUB_ENV:?GITHUB_ENV is not set}"

if [[ -n "${STATIC_OPENCODE_API_KEY:-}" ]]; then
  echo "Using the OPENCODE_API_KEY secret."
  echo "OPENCODE_API_KEY=${STATIC_OPENCODE_API_KEY}" >> "$GITHUB_ENV"
  exit 0
fi

: "${ACTIONS_ID_TOKEN_REQUEST_URL:?the job needs id-token: write}"
: "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?the job needs id-token: write}"
: "${OIDC_CLIENT_ID:?the Console GitHub Actions key id is required}"

audience="${OIDC_AUDIENCE:-https://opencode.ai/console}"

oidc="$(curl -sSf --oauth2-bearer "$ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${audience}" | jq -r .value)"

response="$(curl -sS -X POST https://opencode.ai/console/auth/oauth/token \
  -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
  -d subject_token_type=urn:ietf:params:oauth:token-type:jwt \
  -d client_id="$OIDC_CLIENT_ID" \
  -d subject_token="$oidc")" || {
  echo "::error::Could not reach the OpenCode token endpoint."
  exit 1
}

token="$(jq -r '.access_token // empty' <<<"$response" 2>/dev/null || true)"

if [[ -z "$token" ]]; then
  reason="$(jq -r '.error_description // .error // empty' <<<"$response" 2>/dev/null || true)"
  echo "::error::OpenCode rejected the OIDC exchange: ${reason:-empty response}"
  echo "::error::The GitHub Actions key ${OIDC_CLIENT_ID} must be created for ${GITHUB_REPOSITORY:-the calling repository}. A key bound to another repository fails here."
  exit 1
fi

echo "::add-mask::$token"
echo "OPENCODE_API_KEY=$token" >> "$GITHUB_ENV"
