# ci

Reusable GitHub workflows for my personal projects.

## `code-review.yml`

Reviews a pull request with the `code-review-bot` skill from
[AlvaroVFon/skills](https://github.com/AlvaroVFon/skills) and publishes the verdict
on the PR with the `gh` CLI. `code-review-bot` loads `code-review` for the review
judgment and adds the unattended contract: idempotent comments, anchor fallbacks,
a repository policy, a validated JSON report and a check run.

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
      checks: write
      id-token: write
    uses: AlvaroVFon/ci/.github/workflows/code-review.yml@v8
    with:
      model: opencode-go/deepseek-v4.1-flash
      # Tag or commit of AlvaroVFon/skills with code-review and code-review-bot.
      skills-ref: <skills-tag-or-commit>
      ci-ref: v8
      oidc-client-id: sk_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Authentication is OIDC: create a **GitHub Actions** key in the OpenCode Console,
copy its client id into `oidc-client-id`, and the workflow exchanges the runner's
OIDC token for a short-lived key. Nothing long-lived is stored, but the caller must
grant `id-token: write` because a called workflow cannot elevate permissions.

The Console binds a GitHub Actions key to the repository it was created for, so
**each caller needs its own key**. Reusing the key of another repository fails at
sign-in, before the agent runs, with `OpenCode rejected the OIDC exchange:
The token could not be exchanged for this credential`. The workflow ships no
default client id for that reason.

To use a static key instead, store it as the `OPENCODE_API_KEY` secret. It takes
precedence over the exchange and does not need `id-token: write`.

### Inputs

| Input           | Required | Description                                                                   |
| --------------- | -------- | ----------------------------------------------------------------------------- |
| `model`         | yes      | OpenCode model in `provider/model` format, optionally `provider/model#variant` |
| `skills-ref`    | yes      | Tag or commit of `AlvaroVFon/skills` that provides `code-review` and `code-review-bot` |
| `ci-ref`        | yes      | Tag or commit of `AlvaroVFon/ci` with the scripts; must match the `uses:` ref  |
| `oidc-client-id` | no      | Console key id created for the calling repository; empty only with `OPENCODE_API_KEY` |
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
   - `setup-reviewer.sh` installs `code-review` and `code-review-bot` in the
     OpenCode config directory, outside the caller repo, together with the
     reviewer policies: the review is read-only, `git push`, `git commit`,
     `git rebase`, `gh pr merge`, `gh pr review`, file edits, `.env` reads
     through the read tool, web fetch/search and common exfiltration commands
     are denied, and session sharing is disabled so the transcript is never
     published;
   - `run-review.sh` installs the CLI and runs the agent with a prompt that limits
     reading to the diff and the code it calls. The agent writes a
     `code-review-report.json`, a review payload and a check payload, validates
     the report, then publishes one review with `gh api .../reviews`.
4. The agent posts the inline comments and file-level fallbacks from the payload.
   It never approves and never merges.
5. `run-review.sh` re-reads the head and state, validates the report again,
   requires the `<!-- ai-review -->` marker on a review for the current head SHA,
   rejects an approval, sanitizes the check-run annotations against the checkout,
   and posts the `AI review` check run from the helper's check payload when
   `check_run` is enabled. It fails the job when the report is stale or when
   `blocking_gate` is enabled and the verdict is blocking.

A new push to the same pull request cancels the run in progress.

```text
.github/workflows/code-review.yml   reusable workflow
scripts/sign-in.sh                  OIDC exchange, or the static key fallback
scripts/check-provider-key.sh       fail fast without a key
scripts/setup-reviewer.sh           install both skills and the policies
scripts/run-review.sh               install the CLI, run and verify the review
scripts/reviewer-permissions.json   policies copied into the OpenCode config
```

The scripts live outside the workflow because a reusable workflow runs in the
caller's workspace: this repo is not checked out there. The workflow checks them
out at `ci-ref` instead of using a composite action, because an action pinned
inside the workflow is a second, hidden version that silently drifts from the tag
the caller picked. That exact bug shipped once: the workflow at `v2` kept calling
`review-pr@v1`, so every v2 change was ignored.

### Verdict and merge gate

The bot is advisory by default. It never approves and never merges:

| Report verdict | Review event   | Check run (`check_run: true`) | Job result |
| -------------- | -------------- | ----------------------------- | ---------- |
| `clean`        | `COMMENT`      | `success`                     | pass       |
| `findings`     | `COMMENT`      | `neutral`                     | pass       |
| `blocking`     | `COMMENT`      | `neutral`                     | pass       |
| `blocking`     | `REQUEST_CHANGES` | `failure`                  | fail       |
| `skipped`      | none           | `neutral`                     | pass       |
| `error`        | none           | `failure`                     | fail       |

The last two blocking rows depend on the caller repository's `.ai-review.yml`:
`blocking_gate: true` turns blocking findings into `REQUEST_CHANGES`, a failing
`AI review` check and a failing job. Leave it `false` during rollout. Required
status checks are the merge gate; a bot approval is not.

The report lives at `${TMPDIR:-/tmp}/opencode/code-review-report.json` on the
runner. `run-review.sh` validates it with the skill's own validator and refuses to
accept a review that lacks the `<!-- ai-review -->` marker, targets another commit,
is stale, is a dry run, or contains an approval.

### Versioning

Call `@vN`, not `@main`. With a branch reference, a change here breaks every
project at once. `ci-ref` must carry the same value as the `uses:` ref; a contract
test in `tests/` fails when they diverge. `skills-ref` must point to a `skills`
release that contains both `code-review` and `code-review-bot`.

The feature PR that introduces `v8` keeps `review.yml` on `v7` so its own review
uses the last released workflow. After merging, tag `v8` and bump `review.yml`
(`uses` and `ci-ref`), the pinned test and this README to `v8` in a release
commit. In that same commit, keep `skills-ref` on a `skills` tag or commit that
contains both `code-review` and `code-review-bot`; a ref from before the bot
existed fails when `setup-reviewer.sh` cannot find `BOT_SKILL_PATH`.

### Logs

On a public repository the workflow log is public, and the agent prints every file
it reads and every command it runs. The run therefore stays silent by default:

- session sharing is disabled (`"share": "disabled"`), so no share link exists;
- the agent transcript goes to a file on the runner and never to the log;
- a failed run prints only the last 10 lines, and `verbose: true` streams
  everything for debugging when you accept the exposure.

The run counts as successful when a valid report exists and, for a published
review, a review with the `<!-- ai-review -->` marker exists for the head commit.
The CLI exit code also reflects tool attempts that do not matter once the review is
published, so it is reported as a warning instead of failing the check.

Tokens are masked by the runner and by `::add-mask::`, so they never reach the log
either way. What can appear in the log is repository content the agent read, and
that content is already visible to whoever can read the repository.

### Limits

- Pull requests from forks never run: the token is read-only and secrets are not
  available.
- `github-actions[bot]` can post `REQUEST_CHANGES` and `COMMENT`. An `APPROVE` from
  the bot does not count as a human approval for branch protection rules, and the
  workflow rejects it.
- The job fails when the provider rejects the request, the agent cannot publish the
  review, the report is invalid, or `blocking_gate` is enabled and the verdict is
  blocking. The workflow needs network access to npm and the provider API.
- The OIDC key is minted per run and never stored. If the Console key is revoked or
  the client id changes, the job fails at sign-in.
- The bot reads `.ai-review.yml` from the base SHA. A pull request cannot change the
  policy that judges it.
- The reviewer policy denies `.env` reads through the read tool, web fetch/search
  and common exfiltration commands, but shell and grep still run with runner
  privileges and grep is not path-restricted. Treat it as defense in depth, not
  a sandbox.

## Local verification

```sh
bash <(curl -sSL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash) 1.7.12
./actionlint -color
python3 -m unittest discover -s tests -v
```

`tests/` holds contract tests for the workflow: triggers, permissions, denied
commands and prompt wiring. They do not execute GitHub Actions.
