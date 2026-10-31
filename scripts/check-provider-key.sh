#!/usr/bin/env bash
# Fail fast when the workflow cannot reach any provider.
set -euo pipefail

if [[ -z "${OPENCODE_API_KEY:-}${ANTHROPIC_API_KEY:-}${OPENAI_API_KEY:-}" ]]; then
  echo "::error::No provider API key. Pass OPENCODE_API_KEY, ANTHROPIC_API_KEY or OPENAI_API_KEY."
  exit 1
fi
