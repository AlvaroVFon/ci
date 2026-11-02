#!/usr/bin/env bash
# Install the review skills and the reviewer policies in the OpenCode config
# directory. Nothing is written inside the caller repository.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_path="${SKILL_PATH:?SKILL_PATH must point to the code-review skill}"
bot_path="${BOT_SKILL_PATH:-$(dirname "$skill_path")/code-review-bot}"
config="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
skills_dir="$config/skills"

install_skill() {
  local source="$1"
  local name="$2"
  if [[ ! -f "$source/SKILL.md" ]]; then
    echo "::error::no SKILL.md found at $source"
    exit 1
  fi
  rm -rf "$skills_dir/$name"
  cp -R "$source" "$skills_dir/$name"
}

if [[ ! -f "$skill_path/SKILL.md" ]]; then
  echo "::error::no SKILL.md found at $skill_path"
  exit 1
fi

mkdir -p "$skills_dir"
install_skill "$skill_path" "code-review"

# The bot is required when the caller asks for it; otherwise keep backwards
# compatibility for callers that only install code-review.
if [[ -f "$bot_path/SKILL.md" ]]; then
  if [[ ! -f "$bot_path/assets/prepare_review.py" || ! -f "$bot_path/assets/validate_review_report.py" ]]; then
    echo "::error::code-review-bot is missing its report assets at $bot_path"
    exit 1
  fi
  install_skill "$bot_path" "code-review-bot"
elif [[ -n "${BOT_SKILL_PATH:-}" ]]; then
  echo "::error::BOT_SKILL_PATH is set but has no SKILL.md: $bot_path"
  exit 1
else
  echo "::warning::code-review-bot not found at $bot_path; installing code-review only"
fi

cp "$here/reviewer-permissions.json" "$config/opencode.json"
echo "Installed skills in $skills_dir"
