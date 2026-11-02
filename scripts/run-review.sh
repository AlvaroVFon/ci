#!/usr/bin/env bash
# Run the code-review-bot skill against the pull request that triggered the run.
# The transcript never reaches the workflow log unless VERBOSE=true: on a public
# repository the log is public, and the agent prints every file and command.
set -euo pipefail

: "${MODEL:?MODEL is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

api="repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"
head_sha="$(gh api "$api" --jq .head.sha)"

config="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
bot_dir="$config/skills/code-review-bot"
validator="$bot_dir/assets/validate_review_report.py"
report_dir="${TMPDIR:-/tmp}/opencode"
report="$report_dir/code-review-report.json"
log="${RUNNER_TEMP:-/tmp}/opencode-review.log"

if [[ ! -f "$bot_dir/SKILL.md" ]]; then
  echo "::error::code-review-bot is not installed at $bot_dir"
  exit 1
fi
if [[ ! -f "$validator" ]]; then
  echo "::error::code-review-bot is missing $validator"
  exit 1
fi

mkdir -p "$report_dir"
npm install --global @opencode/cli@2 >/dev/null 2>&1

prompt="$(cat <<EOF
Load the code-review-bot skill with the skill tool and review pull request #${PR_NUMBER} in ${GITHUB_REPOSITORY}.

Follow its automation contract exactly. It loads code-review for review judgment; keep the automation, idempotency and report rules from code-review-bot.

Scope: the diff against the base branch. Read only the files the diff touches and the code they call. Skip README, docs, lockfiles, generated files and tests unless a finding depends on them.

Collect the changed files with their patches from gh api ${api}/files --paginate and read .ai-review.yml from the base SHA. Do not use gh pr diff to decide anchors.

Write the input to ${report_dir}/code-review-input.json, then run:
python3 ${bot_dir}/assets/prepare_review.py --input ${report_dir}/code-review-input.json --report ${report} --payload ${report_dir}/code-review-payload.json --check-payload ${report_dir}/code-review-check.json

Publish the review in a single gh api call to ${api}/reviews with:
- commit_id: ${head_sha};
- event and body from the payload;
- one inline comment per finding the helper marks inline, plus the file-level fallbacks; the body carries the summary and every finding that cannot be anchored. Post at most one inline comment per finding, never one review per finding;
- keep the <!-- ai-review --> marker from the payload body.

Create the check run from ${report_dir}/code-review-check.json with gh api repos/${GITHUB_REPOSITORY}/check-runs when configured.

Never approve. Never merge. Do not edit files, commit, push or run linters, typechecks or tests.

Before finishing, run python3 ${validator} --file ${report} until it prints OK, and leave the report at ${report}.
EOF
)"

status=0
if [[ "${VERBOSE:-false}" == "true" ]]; then
  opencode run --standalone --auto --model "$MODEL" "$prompt" 2>&1 | tee "$log" || status=$?
else
  opencode run --standalone --auto --model "$MODEL" "$prompt" >"$log" 2>&1 || status=$?
fi

fail() {
  echo "::error::$1"
  echo "Last 10 lines of the agent transcript:"
  tail -n 10 "$log"
  exit 1
}

if [[ ! -f "$report" ]]; then
  fail "No review report at ${report} (OpenCode exited with status ${status})."
fi

if ! python3 "$validator" --file "$report"; then
  fail "The review report is invalid."
fi

read -r verdict blocking_gate report_sha stale dry_run < <(python3 - "$report" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1]))
print(
    report["verdict"],
    str(report["policy"]["blocking_gate"]).lower(),
    report["head_sha"],
    str(report["stale"]).lower(),
    str(report["dry_run"]).lower(),
)
PY
)

if [[ "$report_sha" != "$head_sha" ]]; then
  fail "The report targets ${report_sha}, but the pull request head is ${head_sha}."
fi
if [[ "$stale" == "true" ]]; then
  fail "The report is stale: the head moved while the review was running."
fi
if [[ "$dry_run" == "true" ]]; then
  fail "The report is a dry run; the review was not published."
fi

if [[ "$(gh api "$api" --jq .merged)" == "true" ]]; then
  fail "The pull request is already merged; no review should be published."
fi

published="$(gh api "$api/reviews" --paginate --slurp --jq "(add // []) | [.[] | select(.commit_id == \"${head_sha}\" and ((.body // \"\") | contains(\"<!-- ai-review -->\")))] | length")"
approved="$(gh api "$api/reviews" --paginate --slurp --jq "(add // []) | [.[] | select(.commit_id == \"${head_sha}\" and .state == \"APPROVED\" and ((.body // \"\") | contains(\"<!-- ai-review -->\")))] | length")"

if [[ "${approved:-0}" -gt 0 ]]; then
  fail "The bot posted an APPROVE review; code-review-bot must never approve."
fi

if [[ "$verdict" == "skipped" ]]; then
  reason="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("skip_reason") or "")' "$report")"
  if [[ $status -ne 0 ]]; then
    echo "::warning::OpenCode exited with status $status on a skipped review."
  fi
  echo "Review skipped for ${head_sha}: ${reason}."
  exit 0
fi

if [[ "$verdict" == "error" ]]; then
  errors="$(python3 -c 'import json,sys; print("; ".join(json.load(open(sys.argv[1])).get("errors") or []))' "$report")"
  fail "The review failed: ${errors}"
fi

if [[ "${published:-0}" -eq 0 ]]; then
  fail "No AI review published for ${head_sha} (OpenCode exited with status ${status})."
fi

if [[ $status -ne 0 ]]; then
  echo "::warning::OpenCode exited with status $status after publishing the review."
fi

if [[ "$verdict" == "blocking" && "$blocking_gate" == "true" ]]; then
  echo "::error::Blocking findings reported and blocking_gate is enabled; the required check fails."
  exit 1
fi

if [[ "$verdict" == "blocking" ]]; then
  echo "::warning::Blocking findings reported; blocking_gate is disabled, so the check stays green."
fi

echo "Review published for ${head_sha} (verdict ${verdict}). The transcript stayed on the runner."
exit 0
