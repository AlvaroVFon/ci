#!/usr/bin/env bash
# Run the code-review-bot skill against the pull request that triggered the run.
# The transcript never reaches the workflow log unless VERBOSE=true: on a public
# repository the log is public, and the agent prints every file and command.
set -euo pipefail

: "${MODEL:?MODEL is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

api="repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"

config="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
bot_dir="$config/skills/code-review-bot"
prepare="$bot_dir/assets/prepare_review.py"
validator="$bot_dir/assets/validate_review_report.py"
report_dir="${TMPDIR:-/tmp}/opencode"
report="$report_dir/code-review-report.json"
input="$report_dir/code-review-input.json"
payload="$report_dir/code-review-payload.json"
check_payload="$report_dir/code-review-check.json"
log="${RUNNER_TEMP:-/tmp}/opencode-review.log"

if [[ ! -f "$bot_dir/SKILL.md" ]]; then
  echo "::error::code-review-bot is not installed at $bot_dir"
  exit 1
fi
if [[ ! -f "$prepare" ]]; then
  echo "::error::code-review-bot is missing $prepare"
  exit 1
fi
if [[ ! -f "$validator" ]]; then
  echo "::error::code-review-bot is missing $validator"
  exit 1
fi

# The report directory can survive a reused runner or a retry. Drop the
# artifacts so a previous run cannot satisfy the checks below.
mkdir -p "$report_dir"
rm -f "$report" "$input" "$payload" "$check_payload" "$check_payload.tmp"
npm install --global @opencode/cli@2 >/dev/null 2>&1

prompt="$(cat <<EOF
Load the code-review-bot skill with the skill tool and review pull request #${PR_NUMBER} in ${GITHUB_REPOSITORY}.

Follow its automation contract exactly. It loads code-review for review judgment; keep the automation, idempotency and report rules from code-review-bot.

Scope: the diff against the base branch. Read only the files the diff touches and the code they call. Skip README, docs, lockfiles, generated files and tests unless a finding depends on them.

Collect the changed files with their patches from gh api ${api}/files --paginate and read .ai-review.yml from the base SHA. Do not use gh pr diff to decide anchors.

Write the input to ${input}, then run:
python3 ${bot_dir}/assets/prepare_review.py --input ${input} --report ${report} --payload ${payload} --check-payload ${check_payload}

Publish the review in a single gh api call to ${api}/reviews with --input ${payload} when the payload exists. The payload carries commit_id, event, body and one inline comment per finding the helper marks inline, plus the file-level fallbacks; the body carries the summary and every finding that cannot be anchored. Post at most one inline comment per finding, never one review per finding. Keep the <!-- ai-review --> marker from the payload body.

For a skipped or error verdict the helper writes no payload: write the report, do not post a review and stop.

Do not create the check run yourself; run-review.sh posts ${check_payload} after the review.

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

# code-review-bot restarts once when the head moves and then reports the new
# SHA. Re-read it so the report and the published review are compared against
# the current head, not the event's stale capture.
head_sha="$(gh api "$api" --jq .head.sha)"

fail() {
  echo "::error::$1"
  echo "Last 10 lines of the agent transcript:"
  tail -n 10 "$log"
  exit 1
}

check_run_enabled() {
  if [[ ! -f "$input" ]]; then
    return 0
  fi
  python3 - "$input" <<'PY'
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
config = data.get("config") if isinstance(data, dict) else None
value = config.get("check_run", True) if isinstance(config, dict) else True
raise SystemExit(0 if value is not False else 1)
PY
}

# GitHub validates annotations against the report head SHA, not the merge
# checkout. Resolve each path at head_sha and drop annotations whose lines do
# not exist there; otherwise one bad annotation can reject the whole check run.
sanitize_check_payload() {
  python3 - "$check_payload" "$PWD" "$head_sha" "$GITHUB_REPOSITORY" <<'PY'
import base64
import binascii
import json
import os
import subprocess
import sys

path, root, head_sha, repo = sys.argv[1:5]

def count_lines(data):
    return data.count(b"\n") + (0 if data.endswith(b"\n") or not data else 1)

cache = {}

def file_lines(relative):
    if relative in cache:
        return cache[relative]
    cache[relative] = None
    result = subprocess.run(
        ["git", "show", f"{head_sha}:{relative}"],
        cwd=root,
        capture_output=True,
    )
    if result.returncode == 0:
        cache[relative] = count_lines(result.stdout)
        return cache[relative]
    result = subprocess.run(
        [
            "gh",
            "api",
            "-X",
            "GET",
            f"repos/{repo}/contents/{relative}",
            "-f",
            f"ref={head_sha}",
            "--jq",
            ".content",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        data = base64.b64decode(result.stdout)
    except (binascii.Error, ValueError):
        return None
    cache[relative] = count_lines(data)
    return cache[relative]

with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
output = payload.get("output")
if isinstance(output, dict) and isinstance(output.get("annotations"), list):
    kept = []
    for annotation in output["annotations"]:
        if not isinstance(annotation, dict):
            continue
        relative = annotation.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or relative.startswith("/")
            or ".." in relative.split("/")
        ):
            continue
        lines = file_lines(relative)
        if lines is None:
            continue
        start = annotation.get("start_line")
        end = annotation.get("end_line", start)
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 1
            or end < start
            or end > lines
        ):
            continue
        kept.append(annotation)
    dropped = len(output["annotations"]) - len(kept)
    if dropped:
        print(f"Dropped {dropped} invalid check annotation(s).")
    output["annotations"] = kept
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
os.replace(tmp, path)
PY
}

post_check() {
  if ! check_run_enabled; then
    echo "check_run is disabled in .ai-review.yml; skipping the AI review check run."
    return 0
  fi
  if [[ ! -f "$check_payload" ]]; then
    return 1
  fi
  if ! sanitize_check_payload; then
    echo "::error::Could not sanitize the AI review check payload."
    return 1
  fi
  local url
  if ! url="$(gh api "repos/${GITHUB_REPOSITORY}/check-runs" --method POST --input "$check_payload" --jq .html_url)"; then
    echo "::error::Failed to create the AI review check run."
    return 1
  fi
  echo "Posted the AI review check run: ${url}"
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

if [[ "$verdict" == "skipped" ]]; then
  post_check || echo "::warning::The AI review check run was not created for a skipped review."
  reason="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("skip_reason") or "")' "$report")"
  if [[ $status -ne 0 ]]; then
    echo "::warning::OpenCode exited with status $status on a skipped review."
  fi
  echo "Review skipped for ${head_sha}: ${reason}."
  exit 0
fi

if [[ "$verdict" == "error" ]]; then
  post_check || true
  errors="$(python3 -c 'import json,sys; print("; ".join(json.load(open(sys.argv[1])).get("errors") or []))' "$report")"
  fail "The review failed: ${errors}"
fi

pr_state="$(gh api "$api" --jq .state)"
if [[ "$pr_state" != "open" ]]; then
  fail "The pull request is ${pr_state}; no review should be published."
fi

# gh api rejects --slurp combined with --jq, so paginate into one array and
# filter with jq in a separate step.
published="$(gh api "$api/reviews" --paginate --slurp | jq -r "(add // []) | [.[] | select(.commit_id == \"${head_sha}\" and ((.body // \"\") | contains(\"<!-- ai-review -->\")))] | length")"
approved="$(gh api "$api/reviews" --paginate --slurp | jq -r "(add // []) | [.[] | select(.commit_id == \"${head_sha}\" and .state == \"APPROVED\" and ((.body // \"\") | contains(\"<!-- ai-review -->\")))] | length")"

if [[ "${approved:-0}" -gt 0 ]]; then
  fail "The bot posted an APPROVE review; code-review-bot must never approve."
fi

if [[ "${published:-0}" -eq 0 ]]; then
  fail "No AI review published for ${head_sha} (OpenCode exited with status ${status})."
fi

if ! post_check; then
  fail "No AI review check run was created (expected ${check_payload})."
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
