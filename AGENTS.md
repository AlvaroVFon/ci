# AGENTS.md

Reusable GitHub Actions workflow (`code-review.yml`) plus the shell scripts it
runs. It drives `code-review-bot` from `AlvaroVFon/skills`, which loads
`code-review` for review judgment and adds the unattended contract. Consumed by
other repos via `workflow_call`; there is no app, package manifest, or build
step. The README is the user-facing contract — keep it in sync when inputs or
permissions change.

## Verify

```sh
bash <(curl -sSL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash) 1.7.12
./actionlint -color
python3 -m unittest discover -s tests -v
```

actionlint is pinned to 1.7.12 in CI and in the README; use the same version.
Tests use the Python stdlib only and must stay hermetic (no network, `gh`, or
`opencode` calls). They run scripts against temp dirs and assert exact source
text.

## Release / version bump (easy to get wrong)

Callers pin `@vN`, never `@main`. A release requires all of these to agree:

- `.github/workflows/review.yml`: `uses: .../code-review.yml@vN` **and** `ci-ref: vN`
- `tests/test_code_review_workflow.py` → `test_pins_the_reusable_workflow`
  hardcodes the `@vN` string
- the README example
- `skills-ref` must point to a `skills` release that contains both
  `code-review` and `code-review-bot`; `main` is only a temporary tracking ref

`test_uses_and_scripts_ref_match` fails when the `uses:` ref and `ci-ref` diverge.
Tag only after the suite passes. A feature PR that prepares `vN` keeps
`review.yml` on the previous tag; the caller bump is a release commit after the
tag exists, so the PR's own review never points at an unpublished workflow.

## Workflow gotchas

- `code-review.yml` is `workflow_call` only; `review.yml` is this repo's own
  caller. `.github/workflows/ci.yml` only lints and runs tests.
- A reusable workflow runs in the *caller's* workspace, so this repo's scripts
  are checked out explicitly at `inputs.ci-ref` into `.ci/` and invoked as
  `.ci/scripts/*.sh`. Do NOT replace them with a composite action or an
  `uses: AlvaroVFon/ci/...` step: `test_has_no_hidden_action_pin` guards the pin
  drift bug that already shipped once (a workflow kept calling an old version).
- Callers must grant `contents: read`, `pull-requests: write`, `checks: write`
  and `id-token: write`; a called workflow cannot elevate them.
- Draft and fork PRs are skipped on purpose: fork tokens are read-only and have
  no secrets.
- `setup-reviewer.sh` installs both skills side by side because
  `code-review-bot/SKILL.md` references `../code-review/SKILL.md`. Do not install
  only the bot.
- `run-review.sh` writes and validates
  `${TMPDIR:-/tmp}/opencode/code-review-report.json`. The workflow sets
  `TMPDIR: /tmp` so the report lands where the reviewer permissions allow edits.
- The success check is a report for the current head SHA plus a review carrying
  the `<!-- ai-review -->` marker. Do not go back to "any review exists": a human
  review or a previous bot review would satisfy that.
- The bot must never approve. `run-review.sh` rejects an `APPROVED` review that
  carries the bot marker, and the permissions deny `gh pr review`.
- Do not add `contents: write`, remove `cancel-in-progress`, or stream the
  transcript by default. Workflow logs are public on public repos, so
  `run-review.sh` writes to a file and only tails 10 lines on failure.
- Success is "a review exists for the head SHA", not OpenCode's exit code: the
  CLI can exit non-zero after publishing. Keep that check.
- `--standalone --auto` in `run-review.sh` and the prompt wording are asserted
  by tests (`inline comment`, `Skip README`); change tests alongside them.

## Script contract

- Every script is `bash` with `set -euo pipefail` and fails loudly on missing env.
- Step order matters: `sign-in.sh` → `check-provider-key.sh` →
  `setup-reviewer.sh` → `run-review.sh`.
- `setup-reviewer.sh` writes to `${XDG_CONFIG_HOME:-$HOME/.config}/opencode`,
  never into the caller repo, installs `code-review` and (when
  `BOT_SKILL_PATH` is set or found as a sibling) `code-review-bot`, rejects a bot
  without `assets/prepare_review.py` and `assets/validate_review_report.py`, and
  copies `reviewer-permissions.json` to `opencode.json`.
- That permissions file intentionally disables sharing and subagents, denies all
  edits/commits/pushes/merges, and allows edits only under `/tmp`. Do not relax
  these to make a review "work".
- `sign-in.sh` prefers a static `OPENCODE_API_KEY` secret; otherwise it exchanges
  the runner's OIDC token and masks it with `::add-mask::`.
- `run-review.sh` installs `@opencode/cli@2` globally and publishes through
  `gh api repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/reviews`. After the agent it
  re-reads the head SHA (the bot may restart once when the head moves), validates
  the report with `code-review-bot/assets/validate_review_report.py`, rejects
  stale/dry-run/approved reports, posts the `AI review` check run from the
  helper's check payload when `check_run` is enabled, and fails the job when
  `blocking_gate` is enabled and the verdict is blocking.

## Contract tests are source-text assertions

`tests/test_code_review_workflow.py` reads YAML and scripts with regex and
asserts literal snippets (step names, `actions/checkout@v7`, permission strings,
`${api}/reviews`, `tail -n 10`, the bot marker and the report validator).
Reformatting the workflow or renaming a step can fail tests even when behavior is
unchanged. Run the suite after editing anything under `.github/workflows/` or
`scripts/`; update the test when the contract changes intentionally.
