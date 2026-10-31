#!/usr/bin/env python3
"""Contract tests for the reusable code-review workflow.

These tests never run GitHub Actions. They pin the contract the workflow must
keep: how callers invoke it, what it is allowed to touch, and what it must
refuse to do. The shell scripts are exercised for real where that is cheap.

    python3 -m unittest discover -s tests -v
"""

import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "code-review.yml"
SCRIPTS = ROOT / "scripts"
PERMISSIONS = SCRIPTS / "reviewer-permissions.json"


class WorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def flat(self):
        """Collapse the file so assertions survive YAML line folding."""
        return re.sub(r"\s+", " ", self.text)

    def test_is_reusable_and_requires_a_model(self):
        self.assertIn("workflow_call:", self.text)
        self.assertIn("${{ inputs.model }}", self.flat())

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
        self.assertIn("id-token: write", flat)
        self.assertNotIn("contents: write", flat)

    def test_checks_out_the_skill_at_a_caller_pinned_ref(self):
        flat = self.flat()
        self.assertIn("repository: AlvaroVFon/skills", flat)
        self.assertIn("ref: ${{ inputs.skills-ref }}", flat)

    def test_pins_current_action_versions(self):
        self.assertIn("actions/checkout@v7", self.flat())

    def test_delegates_the_run_to_versioned_scripts(self):
        flat = self.flat()
        self.assertIn("ref: ${{ inputs.ci-ref }}", flat)
        for script in (
            ".ci/scripts/sign-in.sh",
            ".ci/scripts/check-provider-key.sh",
            ".ci/scripts/setup-reviewer.sh",
            ".ci/scripts/run-review.sh",
        ):
            with self.subTest(script=script):
                self.assertIn(script, flat)

    def test_has_no_hidden_action_pin(self):
        self.assertNotIn("uses: AlvaroVFon/ci/", self.flat())

    def test_authenticates_gh_for_the_skill(self):
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.flat())


class ReviewerScripts(unittest.TestCase):
    def run_script(self, name, **env):
        return subprocess.run(
            ["bash", str(SCRIPTS / name)],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
        )

    def test_every_script_parses(self):
        for script in sorted(SCRIPTS.glob("*.sh")):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-n", str(script)], capture_output=True, text=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_review_is_headless_and_publishes_through_gh(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        self.assertIn("opencode run --standalone --auto", text)
        self.assertIn("@opencode/cli@2", text)
        self.assertIn("/pulls/${PR_NUMBER}/reviews", text)
        self.assertIn("inline comment", text)
        self.assertIn("Skip README", text)

    def test_disables_session_sharing(self):
        config = json.loads(PERMISSIONS.read_text(encoding="utf-8"))
        self.assertEqual(config.get("share"), "disabled")

    def test_setup_installs_the_skill_and_the_policies(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill = pathlib.Path(tmp) / "code-review"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: code-review\n---\n")
            config = pathlib.Path(tmp) / "config"
            result = self.run_script(
                "setup-reviewer.sh",
                SKILL_PATH=str(skill),
                XDG_CONFIG_HOME=str(config),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = config / "opencode" / "skills" / "code-review" / "SKILL.md"
            self.assertTrue(installed.is_file())
            policies = json.loads((config / "opencode" / "opencode.json").read_text())
            self.assertIn("permissions", policies)

    def test_setup_rejects_a_path_without_the_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_script(
                "setup-reviewer.sh",
                SKILL_PATH=tmp,
                XDG_CONFIG_HOME=str(pathlib.Path(tmp) / "config"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SKILL.md", result.stdout + result.stderr)

    def test_provider_key_gate_requires_a_key(self):
        os.environ.pop("OPENCODE_API_KEY", None)
        self.assertEqual(self.run_script("check-provider-key.sh").returncode, 1)
        self.assertEqual(
            self.run_script("check-provider-key.sh", OPENCODE_API_KEY="test").returncode,
            0,
        )

    def test_sign_in_prefers_the_static_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            github_env = pathlib.Path(tmp) / "env"
            result = self.run_script(
                "sign-in.sh",
                GITHUB_ENV=str(github_env),
                STATIC_OPENCODE_API_KEY="static-key",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("OPENCODE_API_KEY=static-key", github_env.read_text())

    def test_sign_in_requires_the_id_token_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_script(
                "sign-in.sh",
                GITHUB_ENV=str(pathlib.Path(tmp) / "env"),
                STATIC_OPENCODE_API_KEY="",
                ACTIONS_ID_TOKEN_REQUEST_URL="",
                ACTIONS_ID_TOKEN_REQUEST_TOKEN="",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("id-token", result.stdout + result.stderr)

    def test_sign_in_exchanges_the_oidc_token(self):
        text = (SCRIPTS / "sign-in.sh").read_text(encoding="utf-8")
        self.assertIn("https://opencode.ai/console", text)
        self.assertIn("token-exchange", text)
        self.assertIn("::add-mask::", text)
        self.assertIn("GITHUB_ENV", text)

    def test_permissions_deny_mutations(self):
        rules = json.loads(PERMISSIONS.read_text(encoding="utf-8"))["permissions"]
        denied = {(r["action"], r["resource"]) for r in rules if r["effect"] == "deny"}
        self.assertIn(("shell", "git push*"), denied)
        self.assertIn(("shell", "gh pr merge*"), denied)
        self.assertIn(("edit", "*"), denied)


if __name__ == "__main__":
    unittest.main()
