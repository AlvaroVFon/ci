#!/usr/bin/env bash
# Fail fast when neither the OIDC exchange nor the secret produced a key.
set -euo pipefail

if [[ -z "${OPENCODE_API_KEY:-}" ]]; then
  echo "::error::No OpenCode key. Grant id-token: write or pass the OPENCODE_API_KEY secret."
  exit 1
fi
