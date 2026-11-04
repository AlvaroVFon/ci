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
CALLER = ROOT / ".github" / "workflows" / "review.yml"
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

    def test_grants_only_what_the_skills_need(self):
        flat = self.flat()
        self.assertIn("contents: read", flat)
        self.assertIn("pull-requests: write", flat)
        self.assertIn("checks: write", flat)
        self.assertIn("id-token: write", flat)
        self.assertNotIn("contents: write", flat)

    def test_checks_out_the_skills_at_a_caller_pinned_ref(self):
        flat = self.flat()
        self.assertIn("repository: AlvaroVFon/skills", flat)
        self.assertIn("ref: ${{ inputs.skills-ref }}", flat)

    def test_checks_out_the_pull_request_head(self):
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha }}", self.flat()
        )

    def test_installs_the_bot_skill(self):
        flat = self.flat()
        self.assertIn("BOT_SKILL_PATH", flat)
        self.assertIn("code-review-bot", flat)

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

    def test_exposes_a_verbose_switch_that_is_off_by_default(self):
        flat = self.flat()
        self.assertIn("verbose:", flat)
        self.assertIn("default: false", flat)
        self.assertIn("VERBOSE: ${{ inputs.verbose }}", flat)

    def test_ships_no_default_client_id(self):
        block = self.text.split("oidc-client-id:", 1)[1].split("verbose:", 1)[0]
        self.assertNotIn("default:", block)
        self.assertIn("required: false", block)
        self.assertNotRegex(self.text, r"sk_[0-9a-f]{8}-")

    def test_authenticates_gh_for_the_skill(self):
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.flat())


class CallerContract(unittest.TestCase):
    """The caller lives in this repo, so its wiring is testable too."""

    @classmethod
    def setUpClass(cls):
        cls.caller = CALLER.read_text(encoding="utf-8")
        cls.reusable = WORKFLOW.read_text(encoding="utf-8")

    def test_pins_the_reusable_workflow(self):
        self.assertIn(
            "uses: AlvaroVFon/ci/.github/workflows/code-review.yml@v7", self.caller
        )

    def test_uses_and_scripts_ref_match(self):
        workflow_ref = re.search(r"code-review\.yml@(\S+)", self.caller).group(1)
        scripts_ref = re.search(r"(?m)^      ci-ref:\s*(\S+)", self.caller).group(1)
        self.assertEqual(workflow_ref, scripts_ref)

    def test_pins_the_skills_ref(self):
        skills_ref = re.search(r"(?m)^      skills-ref:\s*(\S+)", self.caller).group(1)
        self.assertRegex(skills_ref, r"^(?:[0-9a-f]{40}|v\d+(?:\.\d+)*)$")

    def test_grants_the_permissions_the_called_job_needs(self):
        for permission in (
            "contents: read",
            "pull-requests: write",
            "checks: write",
            "id-token: write",
        ):
            with self.subTest(permission=permission):
                self.assertIn(permission, self.caller)

    def test_passes_a_client_id_scoped_to_this_repository(self):
        self.assertRegex(self.caller, r"(?m)^      oidc-client-id: sk_[0-9a-f-]+$")

    def test_passes_only_inputs_declared_by_the_workflow(self):
        declared = set(self.input_specs())
        passed = self.passed_inputs()
        self.assertTrue(declared, "no inputs parsed from the reusable workflow")
        self.assertLessEqual(passed, declared, passed - declared)

    def test_passes_every_required_input(self):
        required = {name for name, spec in self.input_specs().items() if spec["required"]}
        self.assertTrue(required, "no required inputs parsed from the workflow")
        self.assertLessEqual(required, self.passed_inputs())

    def input_specs(self):
        block = self.reusable.split("workflow_call:", 1)[1].split("secrets:", 1)[0]
        specs, current = {}, None
        for line in block.splitlines():
            name = re.match(r"^      ([^\s:#]+):\s*$", line)
            if name:
                current = name.group(1)
                specs[current] = {"required": False}
            elif current and re.match(r"^        required:\s*true\s*$", line):
                specs[current]["required"] = True
        return specs

    def passed_inputs(self):
        block = self.caller.split("with:", 1)[1].split("secrets:", 1)[0]
        return set(re.findall(r"(?m)^      ([^\s:#]+):", block))


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
        self.assertIn("${api}/reviews", text)
        self.assertIn("inline comment", text)
        self.assertIn("Skip README", text)

    def test_disables_session_sharing(self):
        config = json.loads(PERMISSIONS.read_text(encoding="utf-8"))
        self.assertEqual(config.get("share"), "disabled")

    def test_run_review_keeps_the_transcript_off_the_log(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        self.assertIn('>"$log" 2>&1', text)
        self.assertIn("VERBOSE", text)
        self.assertNotIn('cat "$log"', text)
        self.assertIn("tail -n 10", text)

    def test_run_review_succeeds_when_the_review_exists(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        self.assertIn(".head.sha", text)
        self.assertIn("${published:-0}", text)
        self.assertIn('exit 0', text)

    def test_run_review_uses_the_bot_skill_and_report(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        for snippet in (
            "code-review-bot",
            "code-review-report.json",
            "code-review-input.json",
            "prepare_review.py",
            "validate_review_report.py",
            ".ai-review.yml",
            "from the base SHA",
            "<!-- ai-review -->",
            "Never approve",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, text)

    def test_run_review_requires_the_prepare_helper(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        self.assertIn('prepare="$bot_dir/assets/prepare_review.py"', text)
        self.assertIn("missing $prepare", text)

    def test_run_review_clears_stale_artifacts(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        self.assertIn(
            'rm -f "$report" "$input" "$payload" "$check_payload"', text
        )

    def test_run_review_rejects_a_closed_pull_request(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        self.assertIn("--jq .state", text)
        self.assertIn('"$pr_state" != "open"', text)

    def test_run_review_rechecks_the_head_after_the_agent(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        after_agent = text.split("opencode run", 1)[1]
        self.assertIn('gh api "$api" --jq .head.sha', after_agent)

    def test_run_review_posts_the_check_run_it_verifies(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        for snippet in (
            "code-review-check.json",
            "check-runs",
            "--method POST",
            "check_run_enabled",
            "check_run is disabled in .ai-review.yml",
        ):
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, text)
        self.assertIn("check_run", text)

    def test_run_review_does_not_mix_slurp_with_jq(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"--slurp\s+--jq")

    def test_run_review_rejects_stale_or_approved_reviews(self):
        text = (SCRIPTS / "run-review.sh").read_text(encoding="utf-8")
        self.assertIn('"$stale" == "true"', text)
        self.assertIn('"$dry_run" == "true"', text)
        self.assertIn("${approved:-0}", text)
        self.assertIn('"$verdict" == "blocking"', text)
        self.assertIn('"$blocking_gate" == "true"', text)

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

    def test_setup_installs_the_bot_skill_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            review = root / "code-review"
            bot = root / "code-review-bot"
            review.mkdir()
            (review / "SKILL.md").write_text("---\nname: code-review\n---\n")
            (bot / "assets").mkdir(parents=True)
            (bot / "SKILL.md").write_text("---\nname: code-review-bot\n---\n")
            (bot / "assets" / "prepare_review.py").write_text("# prepare\n")
            (bot / "assets" / "validate_review_report.py").write_text("# validate\n")
            config = root / "config"
            result = self.run_script(
                "setup-reviewer.sh",
                SKILL_PATH=str(review),
                BOT_SKILL_PATH=str(bot),
                XDG_CONFIG_HOME=str(config),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = config / "opencode" / "skills" / "code-review-bot"
            self.assertTrue((installed / "SKILL.md").is_file())
            self.assertTrue((installed / "assets" / "prepare_review.py").is_file())

    def test_setup_rejects_a_bot_without_its_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            review = root / "code-review"
            bot = root / "code-review-bot"
            review.mkdir()
            bot.mkdir()
            (review / "SKILL.md").write_text("---\nname: code-review\n---\n")
            (bot / "SKILL.md").write_text("---\nname: code-review-bot\n---\n")
            result = self.run_script(
                "setup-reviewer.sh",
                SKILL_PATH=str(review),
                BOT_SKILL_PATH=str(bot),
                XDG_CONFIG_HOME=str(root / "config"),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("report assets", result.stdout + result.stderr)

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

    def test_sign_in_explains_a_rejected_exchange(self):
        text = (SCRIPTS / "sign-in.sh").read_text(encoding="utf-8")
        self.assertIn("%{http_code}", text)
        self.assertIn("error_description", text)
        self.assertIn("GITHUB_REPOSITORY", text)

    def test_permissions_deny_mutations_and_approval(self):
        rules = json.loads(PERMISSIONS.read_text(encoding="utf-8"))["permissions"]
        denied = {(r["action"], r["resource"]) for r in rules if r["effect"] == "deny"}
        for rule in (
            ("shell", "git push*"),
            ("shell", "git commit *"),
            ("shell", "git rebase*"),
            ("shell", "git merge *"),
            ("shell", "git tag -d*"),
            ("shell", "gh pr merge*"),
            ("shell", "gh pr review*"),
            ("shell", "curl*"),
            ("shell", "wget*"),
            ("shell", "env *"),
            ("shell", "gh auth*"),
            ("edit", "*"),
            ("read", "*.env"),
            ("read", "*.env.*"),
            ("read", "*_runner_file_commands*"),
            ("webfetch", "*"),
            ("websearch", "*"),
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, denied)

    def test_permissions_allow_env_examples_after_denying_secrets(self):
        rules = json.loads(PERMISSIONS.read_text(encoding="utf-8"))["permissions"]
        read_rules = [
            (index, rule["resource"], rule["effect"])
            for index, rule in enumerate(rules)
            if rule["action"] == "read"
        ]
        self.assertIn(("*.env", "deny"), [(r, e) for _, r, e in read_rules])
        self.assertIn(("*.env.*", "deny"), [(r, e) for _, r, e in read_rules])
        self.assertIn(("*.env.example", "allow"), [(r, e) for _, r, e in read_rules])
        env_deny = next(i for i, r, e in read_rules if r == "*.env.*" and e == "deny")
        env_example = next(
            i for i, r, e in read_rules if r == "*.env.example" and e == "allow"
        )
        self.assertGreater(env_example, env_deny)

    def test_permissions_keep_read_only_git_commands_available(self):
        rules = json.loads(PERMISSIONS.read_text(encoding="utf-8"))["permissions"]
        denied = {r["resource"] for r in rules if r["effect"] == "deny"}
        for overbroad in ("git merge*", "git tag*"):
            with self.subTest(rule=overbroad):
                self.assertNotIn(overbroad, denied)


class RunReviewIntegration(unittest.TestCase):
    """Run the script against stubbed gh/opencode/npm binaries.

    The stub for `gh` rejects `--slurp` combined with `--jq`, which is the exact
    failure the real CLI reports. Nothing here touches the network.
    """

    SHA = "a" * 40

    def write_executable(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def test_publishes_and_posts_the_check_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            bin_dir = root / "bin"
            config = root / "config"
            reports = root / "reports"
            bot = config / "opencode" / "skills" / "code-review-bot"
            (bot / "assets").mkdir(parents=True)
            (bot / "SKILL.md").write_text("---\nname: code-review-bot\n---\n")
            (bot / "assets" / "prepare_review.py").write_text("# prepare\n")
            (bot / "assets" / "validate_review_report.py").write_text(
                "import sys\nsys.exit(0)\n"
            )
            gh_log = root / "gh.log"

            report = {
                "verdict": "clean",
                "policy": {"blocking_gate": False},
                "head_sha": self.SHA,
                "stale": False,
                "dry_run": False,
            }
            payload = {
                "commit_id": self.SHA,
                "event": "COMMENT",
                "body": "<!-- ai-review -->",
                "comments": [],
            }
            check = {
                "name": "AI review",
                "output": {
                    "title": "AI review",
                    "summary": "test",
                    "annotations": [
                        {
                            "path": "src/app.py",
                            "start_line": 2,
                            "end_line": 2,
                            "annotation_level": "warning",
                            "message": "keep",
                        },
                        {
                            "path": "src/app.py",
                            "start_line": 99,
                            "end_line": 99,
                            "annotation_level": "warning",
                            "message": "drop-out-of-range",
                        },
                        {
                            "path": "missing.py",
                            "start_line": 1,
                            "end_line": 1,
                            "annotation_level": "warning",
                            "message": "drop-missing",
                        },
                    ],
                },
            }
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("one\ntwo\nthree\nfour\n")

            self.write_executable(
                bin_dir / "npm",
                "#!/usr/bin/env bash\nexit 0\n",
            )
            self.write_executable(
                bin_dir / "opencode",
                "#!/usr/bin/env bash\n"
                'for artifact in code-review-report.json code-review-input.json code-review-payload.json code-review-check.json; do\n'
                '  if [[ -e "$TMPDIR/opencode/$artifact" ]]; then\n'
                '    echo "stale artifact was not cleared: $artifact" >&2\n'
                "    exit 1\n"
                "  fi\n"
                "done\n"
                'mkdir -p "$TMPDIR/opencode"\n'
                f"printf '%s' '{json.dumps(report)}' > \"$TMPDIR/opencode/code-review-report.json\"\n"
                f"printf '%s' '{json.dumps({'config': {'check_run': True}})}' > \"$TMPDIR/opencode/code-review-input.json\"\n"
                f"printf '%s' '{json.dumps(payload)}' > \"$TMPDIR/opencode/code-review-payload.json\"\n"
                f"printf '%s' '{json.dumps(check)}' > \"$TMPDIR/opencode/code-review-check.json\"\n"
                "exit 0\n",
            )
            self.write_executable(
                bin_dir / "gh",
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                f'log="{gh_log}"\n'
                "printf '%s\\n' \"$*\" >> \"$log\"\n"
                'if [[ "$1" == "api" ]]; then shift; fi\n'
                "slurp=false\n"
                "jq=false\n"
                'for arg in "$@"; do\n'
                '  if [[ "$arg" == "--slurp" ]]; then slurp=true; fi\n'
                '  if [[ "$arg" == "--jq" ]]; then jq=true; fi\n'
                "done\n"
                'if [[ "$slurp" == true && "$jq" == true ]]; then\n'
                '  echo "the --slurp option is not supported with --jq or --template" >&2\n'
                "  exit 1\n"
                "fi\n"
                'if [[ "$*" == *"/contents/"* && "$*" != *"-X GET"* ]]; then\n'
                '  echo "the contents endpoint requires GET" >&2\n'
                "  exit 1\n"
                "fi\n"
                'if [[ "$*" == *"/check-runs --method POST"* ]]; then\n'
                '  previous=""\n'
                '  for arg in "$@"; do\n'
                '    if [[ "$previous" == "--input" ]]; then cp "$arg" "' + str(root / "check-posted.json") + '"; fi\n'
                '    previous="$arg"\n'
                "  done\n"
                "fi\n"
                'case "$*" in\n'
                f'  *"/pulls/12 --jq .head.sha"*) printf \'%s\\n\' "{self.SHA}" ;;\n'
                '  *"/pulls/12 --jq .state"*) printf \'open\\n\' ;;\n'
                f'  *"/pulls/12/reviews --paginate --slurp"*) printf \'[[{{"commit_id":"{self.SHA}","state":"COMMENTED","body":"<!-- ai-review -->"}}]]\\n\' ;;\n'
                '  *"/contents/src/app.py -f ref="*) printf \'%s\\n\' "b25lCnR3bwp0aHJlZQpmb3VyCg==" ;;\n'
                '  *"/check-runs --method POST"*) printf \'https://github.com/o/r/runs/1\\n\' ;;\n'
                '  *) echo "unexpected gh call: $*" >&2; exit 1 ;;\n'
                "esac\n",
            )

            env = {
                **os.environ,
                "MODEL": "test/model",
                "PR_NUMBER": "12",
                "GITHUB_REPOSITORY": "o/r",
                "XDG_CONFIG_HOME": str(config),
                "TMPDIR": str(reports),
                "RUNNER_TEMP": str(root),
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            }
            stale = reports / "opencode"
            stale.mkdir(parents=True)
            for name in (
                "code-review-report.json",
                "code-review-input.json",
                "code-review-payload.json",
                "code-review-check.json",
            ):
                (stale / name).write_text("stale\n", encoding="utf-8")

            result = subprocess.run(
                ["bash", str(SCRIPTS / "run-review.sh")],
                env=env,
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("Posted the AI review check run", result.stdout)
            self.assertIn("Dropped 2 invalid check annotation(s).", result.stdout)
            self.assertIn("check-runs", gh_log.read_text(encoding="utf-8"))
            posted = json.loads((root / "check-posted.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [item["message"] for item in posted["output"]["annotations"]],
                ["keep"],
            )


if __name__ == "__main__":
    unittest.main()
