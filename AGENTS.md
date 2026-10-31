# AGENTS.md

Reusable GitHub Actions workflow (`code-review.yml`) plus the shell scripts it
runs. Consumed by other repos via `workflow_call`; there is no app, package
manifest, or build step. The README is the user-facing contract — keep it in
sync when inputs or permissions change.

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

`test_uses_and_scripts_ref_match` fails when the `uses:` ref and `ci-ref` diverge.
Tag only after the suite passes.

## Workflow gotchas

- `code-review.yml` is `workflow_call` only; `review.yml` is this repo's own
  caller. `.github/workflows/ci.yml` only lints and runs tests.
- A reusable workflow runs in the *caller's* workspace, so this repo's scripts
  are checked out explicitly at `inputs.ci-ref` into `.ci/` and invoked as
  `.ci/scripts/*.sh`. Do NOT replace them with a composite action or an
  `uses: AlvaroVFon/ci/...` step: `test_has_no_hidden_action_pin` guards the pin
  drift bug that already shipped once (a workflow kept calling an old version).
- Callers must grant `contents: read`, `pull-requests: write`, `id-token: write`;
  a called workflow cannot elevate them.
- Draft and fork PRs are skipped on purpose: fork tokens are read-only and have
  no secrets.
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
  never into the caller repo, and copies `reviewer-permissions.json` to
  `opencode.json`.
- That permissions file intentionally disables sharing and subagents, denies all
  edits/commits/pushes/merges, and allows edits only under `/tmp`. Do not relax
  these to make a review "work".
- `sign-in.sh` prefers a static `OPENCODE_API_KEY` secret; otherwise it exchanges
  the runner's OIDC token and masks it with `::add-mask::`.
- `run-review.sh` installs `@opencode/cli@2` globally and publishes through
  `gh api repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER/reviews`.

## Contract tests are source-text assertions

`tests/test_code_review_workflow.py` reads YAML and scripts with regex and
asserts literal snippets (step names, `actions/checkout@v7`, permission strings,
`${api}/reviews`, `tail -n 10`). Reformatting the workflow or renaming a step can
fail tests even when behavior is unchanged. Run the suite after editing anything
under `.github/workflows/` or `scripts/`; update the test when the contract
changes intentionally.
