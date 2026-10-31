#!/usr/bin/env python3
"""Contract tests for the reusable code-review workflow.

These tests never run GitHub Actions. They pin the contract the workflow must
keep: how callers invoke it, what it is allowed to touch, and what it must
refuse to do. Run with:

    python3 -m unittest discover -s tests -v
"""

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "code-review.yml"


class CodeReviewWorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def flat(self):
        """Collapse the file so assertions survive YAML line folding."""
        return re.sub(r"\s+", " ", self.text)

    def test_is_reusable_and_requires_a_model(self):
        flat = self.flat()
        self.assertIn("workflow_call:", self.text)
        self.assertIn("${{ inputs.model }}", flat)

    def test_skips_draft_and_fork_pull_requests(self):
        flat = self.flat()
        self.assertIn("github.event.pull_request.draft == false", flat)
        self.assertIn(
            "github.event.pull_request.head.repo.full_name == github.repository", flat
        )

    def test_cancels_stale_runs_per_pull_request(self):
        flat = self.flat()
        self.assertIn("cancel-in-progress: true", flat)
        self.assertIn("github.event.pull_request.number", flat)

    def test_grants_only_what_the_skill_needs(self):
        flat = self.flat()
        self.assertIn("contents: read", flat)
        self.assertIn("pull-requests: write", flat)
        self.assertNotIn("contents: write", flat)

    def test_checks_out_the_skill_at_a_caller_pinned_ref(self):
        flat = self.flat()
        self.assertIn("repository: AlvaroVFon/skills", flat)
        self.assertIn("ref: ${{ inputs.skills-ref }}", flat)

    def test_pins_current_action_versions(self):
        self.assertIn("actions/checkout@v7", self.flat())

    def test_installs_a_pinned_cli(self):
        self.assertIn("@opencode/cli@2", self.flat())

    def test_runs_headless_with_the_skill_prompt(self):
        flat = self.flat()
        self.assertIn("opencode run --standalone --auto", flat)
        self.assertIn("code-review", flat)
        self.assertIn("${{ github.event.pull_request.number }}", flat)

    def test_authenticates_gh_for_the_skill(self):
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.flat())

    def test_fails_fast_without_a_provider_key(self):
        self.assertIn(
            'if [ -z "${OPENCODE_API_KEY}${ANTHROPIC_API_KEY}${OPENAI_API_KEY}" ]',
            self.flat(),
        )

    def test_denies_pushes_merges_and_workspace_edits(self):
        block = re.search(r"<<'JSON'\n(.*?)\n\s*JSON", self.text, re.S)
        self.assertIsNotNone(block, "permissions JSON heredoc not found")
        rules = json.loads(block.group(1))["permissions"]
        denied = {(r["action"], r["resource"]) for r in rules if r["effect"] == "deny"}
        self.assertIn(("shell", "git push*"), denied)
        self.assertIn(("shell", "gh pr merge*"), denied)
        self.assertIn(("edit", "*"), denied)


if __name__ == "__main__":
    unittest.main()
