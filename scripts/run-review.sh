#!/usr/bin/env bash
# Run the code-review skill against the pull request that triggered the run.
# The transcript never reaches the workflow log unless VERBOSE=true: on a public
# repository the log is public, and the agent prints every file and command.
set -euo pipefail

: "${MODEL:?MODEL is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

npm install --global @opencode/cli@2 >/dev/null 2>&1

prompt="$(cat <<EOF
Load the code-review skill with the skill tool and review pull request #${PR_NUMBER} in ${GITHUB_REPOSITORY}.

Scope: the diff against the base branch. Read only the files the diff touches and the code they call. Skip README, docs and tests unless a finding depends on them.

Publish the review in a single gh api call to /repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}/reviews with:
- event: the verdict the skill requires;
- commit_id: the head SHA of the pull request;
- comments: one inline comment per finding that can be anchored to a changed line, each with path, line and the severity prefix from the skill;
- body: the summary and counters, plus only the findings that cannot be anchored to a line.

Do not post findings one by one. Do not edit files, do not commit, do not push and never merge.
EOF
)"

log="${RUNNER_TEMP:-/tmp}/opencode-review.log"
status=0
if [[ "${VERBOSE:-false}" == "true" ]]; then
  opencode run --standalone --auto --model "$MODEL" "$prompt" 2>&1 | tee "$log" || status=$?
else
  opencode run --standalone --auto --model "$MODEL" "$prompt" >"$log" 2>&1 || status=$?
fi

if [[ $status -ne 0 ]]; then
  echo "::error::OpenCode exited with status $status. Last 10 lines of the run:"
  tail -n 10 "$log"
  exit "$status"
fi

echo "Review finished. The transcript stayed on the runner."
