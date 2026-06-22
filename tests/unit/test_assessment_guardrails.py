"""Tests for agent assessment guardrails and reconciliation."""

from unity_audit.fix_planner import FixDecision
from unity_audit.harness.assessment_guardrails import (
    reconcile_agent_assessment_with_decision,
    retarget_fix_plan,
    validate_assessment_guardrails,
)
from unity_audit.harness.state import AgentAssessment


def _decision(action: str = "manual_confirm_required") -> FixDecision:
    return FixDecision(
        issue_id="ISSUE_1",
        rule_id="TEX_READ_WRITE_ENABLED",
        asset_path="Textures/test.png",
        severity="high",
        action=action,
        risk_level="high" if action == "do_not_fix" else "medium",
        reason="Deterministic reason.",
        suggestion="Review texture importer settings.",
    )


def _assessment(
    action: str = "auto_fix_candidate",
    confidence: float = 0.9,
) -> AgentAssessment:
    return AgentAssessment(
        issue_id="ISSUE_1",
        risk_level="low",
        recommended_action=action,
        confidence=confidence,
        summary="Agent believes this can be fixed.",
        evidence_refs=[],
        needs_human_review=False,
    )


def test_reconcile_does_not_upgrade_deterministic_do_not_fix():
    """Agent cannot upgrade a deterministic do_not_fix decision."""
    result = reconcile_agent_assessment_with_decision(
        _decision(action="do_not_fix"),
        _assessment(action="auto_fix_candidate", confidence=0.95),
    )

    assert result.action == "do_not_fix"
    assert result.risk_level == "high"
    assert "blocked by deterministic guardrail" in result.reason


def test_reconcile_downgrades_low_confidence_auto_fix():
    """Low-confidence agent auto-fix should become manual review."""
    result = reconcile_agent_assessment_with_decision(
        _decision(action="manual_confirm_required"),
        _assessment(action="auto_fix_candidate", confidence=0.5),
    )

    assert result.action == "manual_confirm_required"
    assert result.risk_level == "medium"
    assert "low confidence" in result.reason


def test_reconcile_accepts_high_confidence_agent_assessment():
    """High-confidence agent assessment can replace a non-blocking decision."""
    result = reconcile_agent_assessment_with_decision(
        _decision(action="manual_confirm_required"),
        _assessment(action="auto_fix_candidate", confidence=0.9),
    )

    assert result.action == "auto_fix_candidate"
    assert result.risk_level == "low"
    assert result.reason == "Agent believes this can be fixed."


def test_guardrail_rejects_fix_plan_for_wrong_asset():
    assessment = _assessment()
    assessment.fix_plan = {
        "fix_type": "importer_setting",
        "target_asset": "Textures/other.png",
        "changes": {"isReadable": False},
        "verification_steps": ["Reimport texture"],
        "requires_approval": True,
    }

    valid, error = validate_assessment_guardrails(
        {
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "asset_path": "Textures/test.png",
            "evidence_association_level": "none",
        },
        assessment,
        [],
    )

    assert valid is False
    assert "target_asset" in error


def test_guardrail_rejects_unknown_importer_change():
    assessment = _assessment()
    assessment.fix_plan = {
        "fix_type": "importer_setting",
        "target_asset": "Textures/test.png",
        "changes": {"inventedSetting": False},
        "verification_steps": ["Reimport texture"],
        "requires_approval": True,
    }

    valid, error = validate_assessment_guardrails(
        {
            "rule_id": "TEX_READ_WRITE_ENABLED",
            "asset_path": "Textures/test.png",
            "evidence_association_level": "none",
        },
        assessment,
        [],
    )

    assert valid is False
    assert "Unsupported importer changes" in error


def test_retarget_fix_plan_clones_nested_values():
    original = {
        "fix_type": "importer_setting",
        "target_asset": "Textures/a.png",
        "changes": {"isReadable": False},
        "verification_steps": ["Reimport texture"],
        "requires_approval": True,
    }

    cloned = retarget_fix_plan(original, "Textures/b.png")

    assert cloned["target_asset"] == "Textures/b.png"
    assert cloned["changes"] == original["changes"]
    assert cloned["changes"] is not original["changes"]
