"""Tests for structured Agent fix plans."""

import json

from unity_audit.harness.policy import validate_finish_action
from unity_audit.harness.state import AgentAssessment, RunState


def _fix_plan(requires_approval: bool = True) -> dict:
    return {
        "fix_type": "importer_setting",
        "target_asset": "Textures/UI/button.png",
        "changes": {"mipmapEnabled": False},
        "verification_steps": ["Reimport asset", "Run UI smoke test"],
        "requires_approval": requires_approval,
    }


def test_finish_policy_accepts_approved_structured_fix_plan():
    valid, error = validate_finish_action(
        {
            "action": "finish",
            "assessment": {
                "issue_id": "ISSUE_1",
                "risk_level": "low",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.9,
                "summary": "Safe UI importer change.",
                "usage_context": "ui",
                "evidence_strength": "direct",
                "fix_plan": _fix_plan(),
            },
        },
        existing_tool_result_ids=set(),
    )

    assert valid is True
    assert error is None


def test_finish_policy_rejects_plan_without_approval():
    valid, error = validate_finish_action(
        {
            "action": "finish",
            "assessment": {
                "issue_id": "ISSUE_1",
                "risk_level": "low",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.9,
                "summary": "Unsafe unapproved plan.",
                "fix_plan": _fix_plan(requires_approval=False),
            },
        },
        existing_tool_result_ids=set(),
    )

    assert valid is False
    assert "requires_approval" in error


def test_run_state_persists_assessment_and_fix_plan(tmp_path):
    state = RunState(run_id="run_fix_plan", project_root="/project", platform="Android")
    state.add_assessment(AgentAssessment(
        issue_id="ISSUE_1",
        risk_level="low",
        recommended_action="auto_fix_candidate",
        confidence=0.9,
        summary="Safe UI importer change.",
        usage_context="ui",
        evidence_strength="direct",
        fix_plan=_fix_plan(),
    ))
    state_path = tmp_path / "run.json"
    plans_path = tmp_path / "agent_fix_plans.json"

    state.save(str(state_path))
    state.save_fix_plans(str(plans_path))
    loaded = RunState.load(str(state_path))
    plans = json.loads(plans_path.read_text(encoding="utf-8"))

    assert loaded.agent_assessments[0].usage_context == "ui"
    assert loaded.agent_assessments[0].fix_plan["changes"]["mipmapEnabled"] is False
    assert plans[0]["issue_id"] == "ISSUE_1"
    assert plans[0]["fix_plan"]["requires_approval"] is True
