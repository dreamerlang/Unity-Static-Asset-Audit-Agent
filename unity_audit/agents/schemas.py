"""Pydantic schemas for Agent Actions and Assessments.

These schemas enforce the output protocol:
- call_tool: {action, tool_name, arguments, reason}
- finish: {action, assessment: {issue_id, risk_level, recommended_action,
   confidence, summary, evidence_refs, needs_human_review}}
"""



# JSON Schemas (dict form) for use without Pydantic dependency

CALL_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "const": "call_tool"},
        "tool_name": {
            "type": "string",
            "description": "Name of the tool to call",
        },
        "arguments": {
            "type": "object",
            "description": "Arguments for the tool",
        },
        "reason": {
            "type": "string",
            "description": "Why this tool call is needed",
        },
    },
    "required": ["action", "tool_name", "arguments", "reason"],
    "additionalProperties": False,
}

FINISH_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "const": "finish"},
        "assessment": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string"},
                "risk_level": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                },
                "recommended_action": {
                    "type": "string",
                    "enum": ["auto_fix_candidate", "manual_confirm_required", "do_not_fix"],
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "summary": {"type": "string"},
                "evidence_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "needs_human_review": {"type": "boolean"},
                "usage_context": {
                    "type": "string",
                    "enum": [
                        "ui", "world_space_ui", "character", "environment",
                        "editor_only", "test_only", "third_party", "runtime_generated",
                        "audio_sfx", "audio_music", "scene", "unknown",
                    ],
                },
                "evidence_strength": {
                    "type": "string",
                    "enum": ["direct", "possible", "none"],
                },
                "fix_plan": {
                    "type": ["object", "null"],
                    "properties": {
                        "fix_type": {
                            "type": "string",
                            "enum": [
                                "importer_setting", "editor_script",
                                "manual_action", "no_change",
                            ],
                        },
                        "target_asset": {"type": "string"},
                        "changes": {"type": "object"},
                        "verification_steps": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "requires_approval": {"type": "boolean"},
                    },
                    "required": [
                        "fix_type", "target_asset", "changes",
                        "verification_steps", "requires_approval",
                    ],
                },
            },
            "required": [
                "issue_id", "risk_level", "recommended_action",
                "confidence", "summary",
            ],
            "additionalProperties": False,
        },
    },
    "required": ["action", "assessment"],
    "additionalProperties": False,
}

AGENT_ACTION_SCHEMA = {
    "type": "object",
    "oneOf": [CALL_TOOL_SCHEMA, FINISH_SCHEMA],
}


# Helper to build structured output format
def get_structured_output_schema() -> dict:
    """Get the structured output schema for the agent."""
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["call_tool", "finish"],
            },
            "tool_name": {"type": "string"},
            "arguments": {"type": "object"},
            "reason": {"type": "string"},
            "assessment": {
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string"},
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "recommended_action": {
                        "type": "string",
                        "enum": ["auto_fix_candidate", "manual_confirm_required", "do_not_fix"],
                    },
                    "confidence": {"type": "number"},
                    "summary": {"type": "string"},
                    "evidence_refs": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "needs_human_review": {"type": "boolean"},
                    "usage_context": {"type": "string"},
                    "evidence_strength": {"type": "string"},
                    "fix_plan": {"type": ["object", "null"]},
                },
                "required": [
                    "issue_id", "risk_level", "recommended_action",
                    "confidence", "summary",
                ],
            },
        },
        "required": ["action"],
    }
