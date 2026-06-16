"""Tool Registry - Read-only tools for the Agent Harness.

All tools MUST be:
- Structured parameters
- JSON-serializable results
- Marked as read-only, no side effects, retryable/non-retryable
- Path-validated (all paths within project root)
- No shell command execution
"""

import os
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class ToolResult:
    """Unified result from a tool execution."""
    ok: bool
    data: dict | None = None
    error_code: str | None = None
    message: str = ""
    retryable: bool = False
    tool_result_id: str = ""  # Unique ID for trace/evidence reference

    def to_dict(self) -> dict:
        result = {"ok": self.ok}
        if self.data is not None:
            result["data"] = self.data
        if self.error_code is not None:
            result["error_code"] = self.error_code
        if self.message:
            result["message"] = self.message
        if not self.ok:
            result["retryable"] = self.retryable
        if self.tool_result_id:
            result["tool_result_id"] = self.tool_result_id
        return result


@dataclass
class ToolDef:
    """Definition of a registered tool."""
    name: str
    description: str
    func: Callable
    parameters: dict  # JSON Schema for parameters
    is_readonly: bool = True
    has_side_effects: bool = False

    def to_openai_function(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Registry of read-only tools available to the Agent."""

    def __init__(self):
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool_def: ToolDef):
        """Register a tool definition."""
        self._tools[tool_def.name] = tool_def

    def get(self, name: str) -> ToolDef | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDef]:
        """List all registered tools."""
        return list(self._tools.values())

    def list_openai_functions(self) -> list[dict]:
        """List tools in OpenAI function-calling format."""
        return [t.to_openai_function() for t in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> ToolResult:
        """Execute a tool by name.

        Returns:
            ToolResult - always succeeds at the harness level; tool errors
            are returned as ToolResult with ok=False, not as exceptions.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                ok=False,
                error_code="UNKNOWN_TOOL",
                message=f"Tool '{name}' is not registered",
                retryable=False,
            )

        try:
            result = tool.func(**arguments)
            # If the function returns a dict, wrap it
            if isinstance(result, dict):
                return ToolResult(ok=True, data=result)
            elif isinstance(result, ToolResult):
                return result
            else:
                return ToolResult(ok=True, data={"result": str(result)})
        except TypeError as e:
            return ToolResult(
                ok=False,
                error_code="INVALID_ARGUMENTS",
                message=f"Invalid arguments for tool '{name}': {e}",
                retryable=False,
            )
        except Exception as e:
            return ToolResult(
                ok=False,
                error_code="TOOL_ERROR",
                message=f"Tool '{name}' failed: {e}",
                retryable=True,
            )


# ── Path sandbox utilities ──────────────────────────────────────────────

def validate_path_in_project(path: str, project_root: str) -> tuple[bool, str]:
    """Validate that a path is within the project root.

    Resolves symlinks and normalizes. Rejects:
    - Absolute paths outside project root
    - Relative paths with ../
    - Symlinks pointing outside project root

    Returns:
        Tuple of (is_valid, resolved_absolute_path_or_error_message).
    """
    # Reject paths with ..
    if ".." in os.path.normpath(path).split(os.sep):
        return False, f"Path traversal detected: {path}"

    project_root = os.path.realpath(os.path.abspath(project_root))

    if os.path.isabs(path):
        full_path = os.path.realpath(path)
    else:
        full_path = os.path.realpath(os.path.join(project_root, path))

    # Check containment
    if not full_path.startswith(project_root + os.sep) and full_path != project_root:
        return False, f"Path is outside project root: {path}"

    return True, full_path
