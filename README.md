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
    uses: AlvaroVFon/ci/.github/workflows/code-review.yml@v1
    with:
      model: opencode-go/deepseek-v4.1-flash
      skills-ref: main
    secrets:
      OPENCODE_API_KEY: ${{ secrets.OPENCODE_API_KEY }}
```

Add the provider key as a repository or organization secret. The workflow accepts
`OPENCODE_API_KEY`, `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`; pass the one your
`model` needs.

### Inputs

| Input        | Required | Description                                                                 |
| ------------ | -------- | --------------------------------------------------------------------------- |
| `model`      | yes      | OpenCode model in `provider/model` format, optionally `provider/model#variant` |
| `skills-ref` | yes      | Tag or commit of `AlvaroVFon/skills` that provides the skill                 |

Pin `skills-ref` to a tag or commit. `main` gives you the latest skill at the cost
of reviews that change without any change in your repo.

### What it does

1. Skips drafts and pull requests from forks.
2. Checks out the caller repo and the skills repo at `skills-ref`.
3. Installs the skill in the OpenCode config directory, outside the caller repo.
4. Installs reviewer policies: the review is read-only and can run `gh`, but
   `git push`, `gh pr merge` and file edits are denied.
5. Runs `opencode run --standalone --auto` with a prompt that loads the skill.
6. The skill publishes the review with `gh pr review`.

A new push to the same pull request cancels the run in progress.

### Versioning

Call `@v1`, not `@main`. With a branch reference, a change here breaks every
project at once.

### Limits

- Pull requests from forks never run: the token is read-only and secrets are not
  available.
- `github-actions[bot]` can post `REQUEST_CHANGES` and `COMMENT`. An `APPROVE` from
  the bot does not count as a human approval for branch protection rules.
- The job fails when the provider rejects the request or the agent cannot publish
  the review. The workflow needs network access to npm and the provider API.

## Local verification

```sh
bash <(curl -sSL https://raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash) 1.7.12
./actionlint -color
python3 -m unittest discover -s tests -v
```

`tests/` holds contract tests for the workflow: triggers, permissions, denied
commands and prompt wiring. They do not execute GitHub Actions.
