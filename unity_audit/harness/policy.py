"""Policy enforcement - Schema validation and guardrails for Agent Actions.

Ensures the agent cannot:
- Call unregistered tools
- Submit assessments that modify deterministic fields
- Reference non-existent tool results
- Submit structurally invalid actions
"""


# Allowed risk levels
VALID_RISK_LEVELS = {"low", "medium", "high"}

# Allowed actions
VALID_RECOMMENDED_ACTIONS = {
    "auto_fix_candidate",
    "manual_confirm_required",
    "do_not_fix",
}

# Allowed agent action types
VALID_AGENT_ACTIONS = {"call_tool", "finish"}


class PolicyViolation(Exception):
    """Raised when an agent action violates policy."""
    pass


def validate_call_tool_action(
    action_data: dict,
    registered_tools: set[str],
) -> tuple[bool, str | None]:
    """Validate a call_tool action.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if action_data.get("action") != "call_tool":
        return False, "Action type must be 'call_tool'"

    tool_name = action_data.get("tool_name")
    if not tool_name:
        return False, "Missing 'tool_name' field"

    if tool_name not in registered_tools:
        return False, f"Unknown tool: {tool_name}"

    arguments = action_data.get("arguments")
    if arguments is None:
        return False, "Missing 'arguments' field"
    if not isinstance(arguments, dict):
        return False, "Arguments must be a JSON object"

    reason = action_data.get("reason")
    if not reason:
        return False, "Missing 'reason' field"

    return True, None


def validate_finish_action(
    action_data: dict,
    existing_tool_result_ids: set[str],
    original_issue: dict | None = None,
) -> tuple[bool, str | None]:
    """Validate a finish action with an AgentAssessment.

    Checks:
    - Required fields present
    - risk_level is valid
    - recommended_action is valid
    - confidence is in [0, 1]
    - evidence_refs reference real tool results
    - Does not modify rule_id, severity, or asset_path

    Args:
        action_data: The parsed action from the model.
        existing_tool_result_ids: Set of valid tool_result_id strings from this run.
        original_issue: If provided, the original deterministic issue to compare against.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if action_data.get("action") != "finish":
        return False, "Action type must be 'finish'"

    assessment = action_data.get("assessment")
    if not assessment:
        return False, "Missing 'assessment' field"
    if not isinstance(assessment, dict):
        return False, "Assessment must be a JSON object"

    # Required fields
    if "issue_id" not in assessment:
        return False, "Assessment missing 'issue_id'"
    if "risk_level" not in assessment:
        return False, "Assessment missing 'risk_level'"
    if "recommended_action" not in assessment:
        return False, "Assessment missing 'recommended_action'"
    if "confidence" not in assessment:
        return False, "Assessment missing 'confidence'"
    if "summary" not in assessment:
        return False, "Assessment missing 'summary'"

    # Validate risk_level
    if assessment["risk_level"] not in VALID_RISK_LEVELS:
        return False, f"Invalid risk_level: {assessment['risk_level']}"

    # Validate recommended_action
    if assessment["recommended_action"] not in VALID_RECOMMENDED_ACTIONS:
        return False, f"Invalid recommended_action: {assessment['recommended_action']}"

    # Validate confidence
    confidence = assessment["confidence"]
    if not isinstance(confidence, (int, float)):
        return False, "Confidence must be a number"
    if confidence < 0 or confidence > 1:
        return False, f"Confidence must be in [0, 1], got {confidence}"

    # Validate evidence_refs
    evidence_refs = assessment.get("evidence_refs", [])
    if not isinstance(evidence_refs, list):
        return False, "evidence_refs must be an array"
    for ref in evidence_refs:
        if ref not in existing_tool_result_ids:
            return False, f"Evidence ref '{ref}' does not exist in this run"

    # Cannot set do_not_fix without evidence or a substantive summary.
    # Path classification and issue detail data count as implicit evidence —
    # the agent always calls get_issue_detail first, so it always has context.
    if assessment["recommended_action"] == "do_not_fix" and not evidence_refs:
        summary = assessment.get("summary", "")
        if len(summary) < 15:
            return False, (
                "Cannot recommend do_not_fix without evidence references "
                "and summary is too short (< 15 chars)"
            )

    # If original issue provided, check no modification of deterministic fields
    if original_issue:
        if "rule_id" in assessment:
            return False, "Assessment must not modify rule_id"
        if "severity" in assessment:
            return False, "Assessment must not modify severity"
        if "asset_path" in assessment:
            return False, "Assessment must not modify asset_path"

    return True, None


def validate_action(
    action_data: dict,
    registered_tools: set[str],
    existing_tool_result_ids: set[str],
    original_issue: dict | None = None,
) -> tuple[bool, str | None]:
    """Validate any agent action.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not isinstance(action_data, dict):
        return False, "Action must be a JSON object"

    action_type = action_data.get("action")
    if action_type not in VALID_AGENT_ACTIONS:
        return False, f"Invalid action type: {action_type}"

    if action_type == "call_tool":
        return validate_call_tool_action(action_data, registered_tools)
    elif action_type == "finish":
        return validate_finish_action(
            action_data, existing_tool_result_ids, original_issue
        )

    return False, "Unknown action type"
