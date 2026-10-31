#!/usr/bin/env bash
# Install the code-review skill and the reviewer policies in the OpenCode
# config directory. Nothing is written inside the caller repository.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_path="${SKILL_PATH:?SKILL_PATH must point to the code-review skill}"
config="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"

if [[ ! -f "$skill_path/SKILL.md" ]]; then
  echo "::error::no SKILL.md found at $skill_path"
  exit 1
fi

rm -rf "$config/skills/code-review"
mkdir -p "$config/skills"
cp -R "$skill_path" "$config/skills/code-review"
cp "$here/reviewer-permissions.json" "$config/opencode.json"
