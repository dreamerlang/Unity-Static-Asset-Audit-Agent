"""Integration tests for CLI Agent mode (Section 13.10)."""
import os
import subprocess
import sys

PYTHON = sys.executable
CLI_MODULE = "unity_audit.cli"


def _run_scan(project, args=None):
    """Run the CLI scan command."""
    cmd = [PYTHON, "-m", CLI_MODULE, "scan", project]
    if args:
        cmd.extend(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.returncode, result.stdout, result.stderr


class TestCLIAgent:
    """CLI-002, CLI-003: Agent mode tests."""

    def test_agent_mode_with_fake_model(self, tmp_path):
        """Agent mode with fake model should complete and output agent files."""
        output_dir = tmp_path / "output"
        test_proj = os.path.join(os.path.dirname(__file__), "..", "..", "test_project")

        # Use fake model so no API key needed
        exit_code, stdout, stderr = _run_scan(
            test_proj,
            [
                "--platform", "Android",
                "--output", str(output_dir),
                "--agent",
                "--model", "fake:test",
                "--max-agent-steps", "5",
            ],
        )
        assert exit_code == 0, f"CLI agent mode failed: {stderr}"

        # Should output agent files
        assert (output_dir / "run.json").exists()
        assert (output_dir / "agent_assessments.json").exists()

    def test_agent_mode_without_api_key_completes(self, tmp_path):
        """CLI-003: Agent mode without API key falls back successfully."""
        output_dir = tmp_path / "output"
        test_proj = os.path.join(os.path.dirname(__file__), "..", "..", "test_project")

        # Unset any API keys
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)

        cmd = [
            PYTHON, "-m", CLI_MODULE, "scan", test_proj,
            "--platform", "Android",
            "--output", str(output_dir),
            "--agent",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=30, env=env)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        assert "fallback" in (result.stdout + result.stderr).lower() or \
               "Fallback" in result.stdout

    def test_agent_mode_without_config_completes(self, tmp_path):
        """Agent mode without config file should still work."""
        output_dir = tmp_path / "output"
        test_proj = os.path.join(os.path.dirname(__file__), "..", "..", "test_project")

        exit_code, stdout, stderr = _run_scan(
            test_proj,
            [
                "--platform", "Android",
                "--output", str(output_dir),
                "--agent",
                "--model", "fake:test",
                "--no-trace",
            ],
        )
        assert exit_code == 0

    def test_deterministic_scan_still_works(self, tmp_path):
        """CLI-001: Old scan command (no --agent) still works."""
        output_dir = tmp_path / "output"
        test_proj = os.path.join(os.path.dirname(__file__), "..", "..", "test_project")

        exit_code, stdout, stderr = _run_scan(
            test_proj,
            ["--platform", "Android", "--output", str(output_dir)],
        )
        assert exit_code == 0
        assert (output_dir / "report.md").exists()
