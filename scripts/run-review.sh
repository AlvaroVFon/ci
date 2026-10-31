#!/usr/bin/env bash
# Run the code-review skill against the pull request that triggered the run.
set -euo pipefail

: "${MODEL:?MODEL is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

npm install --global @opencode/cli@2

prompt="$(cat <<EOF
Load the code-review skill with the skill tool and review pull request #${PR_NUMBER} in ${GITHUB_REPOSITORY}.
Read the diff against the base branch, verify each claim against the surrounding code, and cite file:line.
Publish the review with gh pr review using the verdict required by the skill.
Do not edit files, do not commit, do not push and never merge.
EOF
)"

opencode run --standalone --auto --model "$MODEL" "$prompt"
