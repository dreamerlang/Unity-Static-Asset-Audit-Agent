"""AuditAgent - Wraps a ModelClient with context management.

The AuditAgent is responsible for:
- Building the system prompt with current context
- Formatting tool definitions for the model
- Calling the model client
- Returning structured actions
"""

import json

from unity_audit.agents.model_client import ModelClient
from unity_audit.agents.prompts import (
    build_system_prompt,
    build_tool_descriptions,
)
from unity_audit.harness.state import ToolCallRecord
from unity_audit.harness.tools import ToolDef


class AuditAgent:
    """Single-issue audit agent that wraps a model client.

    The agent does NOT hold state between issues - each issue is processed
    independently by the HarnessRunner.
    """

    def __init__(self, model_client: ModelClient):
        self._model_client = model_client

    @property
    def model_name(self) -> str:
        return self._model_client.model_name

    def get_action(
        self,
        issue_data: dict,
        tool_defs: list[ToolDef],
        tool_results: list[ToolCallRecord],
        step: int = 0,
        max_steps: int = 12,
        rule_id: str | None = None,
    ) -> dict:
        """Get the next action from the model.

        Args:
            issue_data: Current issue dict with all deterministic fields.
            tool_defs: Available tool definitions.
            tool_results: Tool results from this run so far.
            step: Current step number.
            max_steps: Maximum steps allowed.
            rule_id: Optional rule ID for specialized prompt routing.

        Returns:
            Parsed action dict (call_tool or finish).

        Raises:
            ModelError: If the model fails to produce a valid response.
        """
        # Build context
        issue_context = json.dumps(issue_data, ensure_ascii=False, indent=2)
        tool_descriptions = build_tool_descriptions(tool_defs)

        tool_results_context = "No tools called yet."
        if tool_results:
            lines = ["Previous tool results:"]
            for tr in tool_results:
                lines.append(f"- [{tr.tool_result_id}] {tr.tool_name}: "
                           f"{json.dumps(tr.result, ensure_ascii=False)[:300]}")
            tool_results_context = "\n".join(lines)

        # Use rule_id from issue_data if not explicitly provided
        effective_rule_id = rule_id or issue_data.get("rule_id")

        system_prompt = build_system_prompt(
            issue_context=issue_context,
            tool_descriptions=tool_descriptions,
            tool_results_context=tool_results_context,
            step=step,
            max_steps=max_steps,
            rule_id=effective_rule_id,
        )

        # Format tools for model
        tools = [t.to_openai_function() for t in tool_defs]

        # Call model
        return self._model_client.get_action(
            system_prompt=system_prompt,
            tools=tools,
        )
