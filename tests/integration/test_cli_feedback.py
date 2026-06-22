"""Integration tests for the project feedback CLI."""

import json
import subprocess
import sys


def test_feedback_command_records_project_context(tmp_path):
    cmd = [
        sys.executable,
        "-m",
        "unity_audit.cli",
        "feedback",
        str(tmp_path),
        "--rule-id",
        "TEX_READ_WRITE_ENABLED",
        "--asset-pattern",
        "Textures/Runtime/**",
        "--decision",
        "rejected_fix",
        "--reason",
        "Runtime textures require pixel access.",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr
    assert "Feedback recorded" in result.stdout
    feedback_path = tmp_path / ".unity-audit" / "feedback.jsonl"
    record = json.loads(feedback_path.read_text(encoding="utf-8").strip())
    assert record["rule_id"] == "TEX_READ_WRITE_ENABLED"
    assert record["decision"] == "rejected_fix"
