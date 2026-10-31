# ci

Reusable GitHub workflows for my personal projects.

## `code-review.yml`

Reviews a pull request with the `code-review` skill from
[AlvaroVFon/skills](https://github.com/AlvaroVFon/skills) and publishes the verdict
on the PR with the `gh` CLI.

It is a `workflow_call` workflow: it does not run on its own, a project calls it.

```yaml
name: review

on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

jobs:
  review:
    permissions:
      contents: read
      pull-requests: write
      id-token: write
    uses: AlvaroVFon/ci/.github/workflows/code-review.yml@v3
    with:
      model: opencode-go/deepseek-v4.1-flash
      skills-ref: main
      ci-ref: v3
```

Authentication is OIDC by default: create a **GitHub Actions** key in the OpenCode
Console, copy its client id into `oidc-client-id`, and the workflow exchanges the
runner's OIDC token for a short-lived key. Nothing long-lived is stored, but the
caller must grant `id-token: write` because a called workflow cannot elevate
permissions.

To use a static key instead, store it as the `OPENCODE_API_KEY` secret. It takes
precedence over the exchange and does not need `id-token: write`.

### Inputs

| Input           | Required | Description                                                                   |
| --------------- | -------- | ----------------------------------------------------------------------------- |
| `model`         | yes      | OpenCode model in `provider/model` format, optionally `provider/model#variant` |
| `skills-ref`    | yes      | Tag or commit of `AlvaroVFon/skills` that provides the skill                   |
| `ci-ref`        | yes      | Tag or commit of `AlvaroVFon/ci` with the scripts; must match the `uses:` ref  |
| `oidc-client-id` | no      | Console key id used for the OIDC exchange (defaults to the pull request scoped key) |
| `verbose`       | no      | Stream the agent transcript to the workflow log (default `false`)              |

Pin `skills-ref` to a tag or commit. `main` gives you the latest skill at the cost
of reviews that change without any change in your repo.

### What it does

1. Skips drafts and pull requests from forks.
2. Checks out the caller repo, the skills repo at `skills-ref`, and this repo's
   scripts at `ci-ref`.
3. Runs the scripts in order:
   - `sign-in.sh` signs in to OpenCode with OIDC, or reuses `OPENCODE_API_KEY`;
   - `check-provider-key.sh` stops when no key is available;
   - `setup-reviewer.sh` installs the skill in the OpenCode config directory,
     outside the caller repo, together with the reviewer policies: the review is
     read-only, `git push`, `gh pr merge` and file edits are denied, and session
     sharing is disabled so the transcript is never published;
   - `run-review.sh` installs the CLI and runs the agent with a prompt that limits
     reading to the diff and the code it calls, and requires every finding that can
     be anchored to a changed line to be posted as an inline comment in one review.
4. The agent publishes the review with `gh api .../reviews`.

A new push to the same pull request cancels the run in progress.

```text
.github/workflows/code-review.yml   reusable workflow
scripts/sign-in.sh                  OIDC exchange, or the static key fallback
scripts/check-provider-key.sh       fail fast without a key
scripts/setup-reviewer.sh           install skill and policies
scripts/run-review.sh               install the CLI and run the review
scripts/reviewer-permissions.json   policies copied into the OpenCode config
```

The scripts live outside the workflow because a reusable workflow runs in the
caller's workspace: this repo is not checked out there. The workflow checks them
out at `ci-ref` instead of using a composite action, because an action pinned
inside the workflow is a second, hidden version that silently drifts from the tag
the caller picked. That exact bug shipped once: the workflow at `v2` kept calling
`review-pr@v1`, so every v2 change was ignored.

### Versioning

Call `@vN`, not `@main`. With a branch reference, a change here breaks every
project at once. `ci-ref` must carry the same value as the `uses:` ref; a contract
test in `tests/` fails when they diverge.

### Logs

On a public repository the workflow log is public, and the agent prints every file
it reads and every command it runs. The run therefore stays silent by default:

- session sharing is disabled (`"share": "disabled"`), so no share link exists;
- the agent transcript goes to a file on the runner and never to the log;
- a failed run prints only the last 10 lines, and `verbose: true` streams
  everything for debugging when you accept the exposure.

The run counts as successful when a review exists for the head commit. The CLI
exit code also reflects tool attempts that do not matter once the review is
published, so it is reported as a warning instead of failing the check.

Tokens are masked by the runner and by `::add-mask::`, so they never reach the log
either way. What can appear in the log is repository content the agent read, and
that content is already visible to whoever can read the repository.

### Limits

- Pull requests from forks never run: the token is read-only and secrets are not
  available.
- `github-actions[bot]` can post `REQUEST_CHANGES` and `COMMENT`. An `APPROVE` from
  the bot does not count as a human approval for branch protection rules.
- The job fails when the provider rejects the request or the agent cannot publish
  the review. The workflow needs network access to npm and the provider API.
- The OIDC key is minted per run and never stored. If the Console key is revoked or
  the client id changes, the job fails at sign-in.

## Local verification

```sh
bash <(curl -sSL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash) 1.7.12
./actionlint -color
python3 -m unittest discover -s tests -v
```

`tests/` holds contract tests for the workflow: triggers, permissions, denied
commands and prompt wiring. They do not execute GitHub Actions.
