"""Tests for project-local human feedback storage and retrieval."""

import json

from unity_audit.application.models import AuditResult
from unity_audit.harness.default_tools import build_default_tools
from unity_audit.harness.feedback import (
    append_project_feedback,
    default_feedback_path,
    find_relevant_feedback,
    load_project_feedback,
)
from unity_audit.harness.tools import ToolRegistry
from unity_audit.rules.engine import Issue


def test_feedback_round_trip_and_specificity(tmp_path):
    append_project_feedback(
        str(tmp_path),
        rule_id="TEX_READ_WRITE_ENABLED",
        asset_path_pattern="Textures/**",
        decision="rejected_fix",
        reason="Shared runtime textures require review.",
    )
    exact = append_project_feedback(
        str(tmp_path),
        rule_id="TEX_READ_WRITE_ENABLED",
        asset_path_pattern="Textures/Runtime/generated.png",
        decision="manual_exception",
        reason="Generated texture is read by runtime code.",
    )

    records, warnings = load_project_feedback(str(tmp_path))
    matches = find_relevant_feedback(
        records,
        "TEX_READ_WRITE_ENABLED",
        "Textures/Runtime/generated.png",
    )

    assert warnings == []
    assert len(records) == 2
    assert matches[0]["feedback_id"] == exact.feedback_id
    assert matches[0]["decision"] == "manual_exception"


def test_feedback_loader_skips_malformed_records(tmp_path):
    path = default_feedback_path(str(tmp_path))
    path_obj = tmp_path / ".unity-audit"
    path_obj.mkdir()
    with open(path, "w", encoding="utf-8") as f:
        f.write("not json\n")
        f.write(json.dumps({"rule_id": "TEX_READ_WRITE_ENABLED"}) + "\n")

    records, warnings = load_project_feedback(str(tmp_path))

    assert records == []
    assert len(warnings) == 2


def test_feedback_rejects_unknown_decision(tmp_path):
    try:
        append_project_feedback(
            str(tmp_path),
            rule_id="TEX_READ_WRITE_ENABLED",
            asset_path_pattern="Textures/**",
            decision="always_fix",
            reason="Unsafe custom decision.",
        )
    except ValueError as error:
        assert "Invalid feedback decision" in str(error)
    else:
        raise AssertionError("Expected invalid feedback decision to fail")


def test_issue_detail_includes_relevant_feedback(tmp_path):
    append_project_feedback(
        str(tmp_path),
        rule_id="TEX_READ_WRITE_ENABLED",
        asset_path_pattern="Textures/**",
        decision="rejected_fix",
        reason="Runtime texture policy.",
    )
    issue = Issue(
        issue_id="ISSUE_1",
        rule_id="TEX_READ_WRITE_ENABLED",
        severity="high",
        asset_path="Textures/runtime.png",
        title="Read/Write enabled",
        message="Texture has Read/Write enabled.",
    )
    audit_result = AuditResult(
        project_root=str(tmp_path),
        platform="Android",
        issues=[issue],
    )
    registry = ToolRegistry()
    for tool in build_default_tools(audit_result):
        registry.register(tool)

    result = registry.execute("get_issue_detail", {"issue_id": issue.issue_id})

    assert result.ok
    feedback = result.data["historical_feedback"]
    assert len(feedback) == 1
    assert feedback[0]["decision"] == "rejected_fix"
