"""Assessment guardrails for Agent-produced recommendations.

These checks are intentionally narrow: they enforce evidence-sensitive safety
rules that should not depend on the model following prompt instructions.
"""

from unity_audit.fix_planner import FixDecision
from unity_audit.harness.state import AgentAssessment, ToolCallRecord


_ALLOWED_IMPORTER_CHANGES = {
    "TEX_UI_MIPMAP_ENABLED": {"mipmapEnabled"},
    "TEX_READ_WRITE_ENABLED": {"isReadable"},
    "TEX_UI_MAX_SIZE_TOO_LARGE": {"maxTextureSize"},
    "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD": {"loadType"},
    "AUD_STEREO_SFX": {"forceToMono"},
}


def _has_successful_tool_call(
    tool_results: list[ToolCallRecord],
    tool_name: str,
) -> bool:
    """Return True if the issue has a successful call to the given tool."""
    return any(
        record.tool_name == tool_name and record.result.get("ok") is True
        for record in tool_results
    )


def validate_assessment_guardrails(
    issue_data: dict,
    assessment: AgentAssessment,
    tool_results: list[ToolCallRecord],
) -> tuple[bool, str | None]:
    """Validate safety guardrails that require tool-result context.

    Current rule:
    - For TEX_READ_WRITE_ENABLED with direct/possible code association, the
      agent may not recommend auto_fix_candidate unless it has successfully
      called read_code_context for this issue. This turns the prompt guidance
      into an enforceable harness rule.
    """
    rule_id = issue_data.get("rule_id")
    evidence_level = issue_data.get("evidence_association_level", "none")

    if (
        rule_id == "TEX_READ_WRITE_ENABLED"
        and evidence_level in {"direct", "possible"}
        and assessment.recommended_action == "auto_fix_candidate"
        and not _has_successful_tool_call(tool_results, "read_code_context")
    ):
        return False, (
            "TEX_READ_WRITE_ENABLED with direct/possible code evidence cannot "
            "be auto-fixed until read_code_context has successfully verified "
            "the source context"
        )

    fix_plan = assessment.fix_plan
    if fix_plan is not None:
        target_asset = fix_plan.get("target_asset")
        if target_asset != issue_data.get("asset_path"):
            return False, "fix_plan.target_asset must match the current issue asset_path"
        if assessment.recommended_action == "do_not_fix":
            if fix_plan.get("fix_type") != "no_change" or fix_plan.get("changes"):
                return False, "do_not_fix assessments may only submit an empty no_change plan"
        if fix_plan.get("fix_type") == "importer_setting":
            allowed = _ALLOWED_IMPORTER_CHANGES.get(rule_id, set())
            supplied = set(fix_plan.get("changes", {}))
            if not allowed or not supplied.issubset(allowed):
                unsupported = ", ".join(sorted(supplied - allowed)) or "none"
                return False, f"Unsupported importer changes for {rule_id}: {unsupported}"

    return True, None


def retarget_fix_plan(fix_plan: dict | None, target_asset: str) -> dict | None:
    """Clone a grouped representative fix plan for a peer asset safely."""
    if fix_plan is None:
        return None
    return {
        **fix_plan,
        "target_asset": target_asset,
        "changes": dict(fix_plan.get("changes", {})),
        "verification_steps": list(fix_plan.get("verification_steps", [])),
        "requires_approval": True,
    }


def _with_decision_boundary_note(
    decision: FixDecision,
    action: str,
    reason: str,
) -> str:
    """Add path-specific rationale when similar evidence leads to different actions."""
    if decision.rule_id != "TEX_READ_WRITE_ENABLED":
        return reason

    asset_path = decision.asset_path
    note = ""
    if (
        action == "manual_confirm_required"
        and asset_path.startswith("ReferenceImagesBase/")
    ):
        note = (
            "Decision boundary: ReferenceImagesBase assets look like baseline "
            "source images, but without direct code evidence they still require "
            "human review before changing Read/Write."
        )
    elif (
        action == "do_not_fix"
        and asset_path.startswith("ReferenceImages/")
    ):
        note = (
            "Decision boundary: ReferenceImages/ platform-specific comparison "
            "snapshot paths are treated as expected test data; keep Read/Write "
            "unchanged unless a human confirms the asset is not used by tests."
        )

    if not note or note in reason:
        return reason
    return f"{reason} {note}"


def reconcile_agent_assessment_with_decision(
    decision: FixDecision,
    assessment: AgentAssessment,
    min_auto_fix_confidence: float = 0.7,
) -> FixDecision:
    """Merge an AgentAssessment into a deterministic FixDecision safely.

    The deterministic pipeline remains the safety floor:
    - A deterministic do_not_fix cannot be upgraded by the agent.
    - Low-confidence auto-fix recommendations are downgraded to manual review.
    """
    if decision.action == "do_not_fix" and assessment.recommended_action != "do_not_fix":
        return FixDecision(
            issue_id=decision.issue_id,
            rule_id=decision.rule_id,
            asset_path=decision.asset_path,
            severity=decision.severity,
            action=decision.action,
            risk_level=decision.risk_level,
            reason=(
                "[Agent recommendation blocked by deterministic guardrail] "
                f"Deterministic decision remains do_not_fix: {decision.reason} "
                f"Agent suggested {assessment.recommended_action}: {assessment.summary}"
            ),
            suggestion=decision.suggestion,
        )

    if (
        assessment.recommended_action == "auto_fix_candidate"
        and assessment.confidence < min_auto_fix_confidence
    ):
        return FixDecision(
            issue_id=decision.issue_id,
            rule_id=decision.rule_id,
            asset_path=decision.asset_path,
            severity=decision.severity,
            action="manual_confirm_required",
            risk_level="medium",
            reason=(
                "[Agent auto-fix downgraded due to low confidence] "
                f"confidence={assessment.confidence:.2f}. {assessment.summary}"
            ),
            suggestion=decision.suggestion,
        )

    return FixDecision(
        issue_id=decision.issue_id,
        rule_id=decision.rule_id,
        asset_path=decision.asset_path,
        severity=decision.severity,
        action=assessment.recommended_action,
        risk_level=assessment.risk_level,
        reason=_with_decision_boundary_note(
            decision, assessment.recommended_action, assessment.summary
        ),
        suggestion=decision.suggestion,
    )
