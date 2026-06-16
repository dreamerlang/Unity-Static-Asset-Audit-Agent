"""Unit tests for Harness Tools (Section 13.7)."""
import os

from unity_audit.harness.tools import (
    ToolDef,
    ToolRegistry,
    ToolResult,
    validate_path_in_project,
)


class TestToolRegistry:
    """Basic tool registry tests."""

    def test_register_and_get_tool(self):
        registry = ToolRegistry()

        def my_tool(asset_path: str) -> dict:
            return {"path": asset_path}

        tool_def = ToolDef(
            name="my_tool",
            description="A test tool",
            func=my_tool,
            parameters={
                "type": "object",
                "properties": {
                    "asset_path": {"type": "string"},
                },
                "required": ["asset_path"],
            },
        )
        registry.register(tool_def)

        assert registry.get("my_tool") is tool_def
        assert len(registry.list_tools()) == 1

    def test_execute_tool_success(self):
        registry = ToolRegistry()

        def hello(name: str) -> dict:
            return {"greeting": f"Hello {name}"}

        registry.register(ToolDef(
            name="hello",
            description="Say hello",
            func=hello,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ))

        result = registry.execute("hello", {"name": "World"})
        assert result.ok is True
        assert result.data == {"greeting": "Hello World"}

    def test_execute_unknown_tool(self):
        """TOOL-006: Unknown tool returns UNKNOWN_TOOL error."""
        registry = ToolRegistry()
        result = registry.execute("nonexistent", {})
        assert result.ok is False
        assert result.error_code == "UNKNOWN_TOOL"
        assert result.retryable is False

    def test_execute_tool_exception(self):
        """TOOL-007: Tool exception -> ToolError, harness doesn't crash."""
        registry = ToolRegistry()

        def broken_tool(**kwargs):
            raise RuntimeError("Something went wrong")

        registry.register(ToolDef(
            name="broken",
            description="Always fails",
            func=broken_tool,
            parameters={"type": "object", "properties": {}},
        ))

        result = registry.execute("broken", {})
        assert result.ok is False
        assert result.error_code == "TOOL_ERROR"
        assert result.retryable is True

    def test_execute_invalid_arguments(self):
        registry = ToolRegistry()

        def requires_name(name: str) -> dict:
            return {"name": name}

        registry.register(ToolDef(
            name="needs_name",
            description="Requires name",
            func=requires_name,
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        ))

        result = registry.execute("needs_name", {})  # Missing required 'name'
        assert result.ok is False
        assert result.error_code == "INVALID_ARGUMENTS"

    def test_list_openai_functions(self):
        registry = ToolRegistry()

        def my_tool(x: int) -> dict:
            return {"x": x}

        registry.register(ToolDef(
            name="my_tool",
            description="Test",
            func=my_tool,
            parameters={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        ))

        functions = registry.list_openai_functions()
        assert len(functions) == 1
        assert functions[0]["type"] == "function"
        assert functions[0]["function"]["name"] == "my_tool"


class TestPathValidation:
    """Path sandbox validation tests."""

    def test_valid_path_in_project(self, tmp_path):
        """TOOL-001: Normal path inside project."""
        proj = str(tmp_path)
        (tmp_path / "Assets").mkdir()
        (tmp_path / "Assets" / "test.png").write_text("")
        valid, resolved = validate_path_in_project("Assets/test.png", proj)
        assert valid is True
        assert resolved.endswith("test.png")

    def test_path_with_dot_dot_rejected(self, tmp_path):
        """TOOL-003: ../ is rejected."""
        valid, msg = validate_path_in_project("../etc/passwd", str(tmp_path))
        assert valid is False
        assert "traversal" in msg.lower() or "outside" in msg.lower()

    def test_absolute_path_outside_project(self, tmp_path):
        """TOOL-004: Absolute path outside project is rejected."""
        valid, msg = validate_path_in_project("/etc/passwd", str(tmp_path))
        assert valid is False

    def test_symlink_outside_project(self, tmp_path):
        """TOOL-005: Symlink outside project is rejected."""
        proj = str(tmp_path / "project")
        os.makedirs(os.path.join(proj, "Assets"))
        # Target file truly OUTSIDE the project
        outside_dir = str(tmp_path / "outside_area")
        os.makedirs(outside_dir)
        outside = os.path.join(outside_dir, "secret.txt")
        with open(outside, "w") as f:
            f.write("secret")
        link = os.path.join(proj, "Assets", "link.txt")
        os.symlink(outside, link)
        valid, msg = validate_path_in_project("Assets/link.txt", proj)
        assert valid is False, f"Expected invalid, got valid. msg={msg}"

    def test_valid_absolute_path_in_project(self, tmp_path):
        """Absolute path within project is valid."""
        proj = str(tmp_path)
        (tmp_path / "Assets").mkdir()
        f = tmp_path / "Assets" / "valid.png"
        f.write_text("")
        valid, resolved = validate_path_in_project(str(f), proj)
        assert valid is True

    def test_tool_result_to_dict(self):
        result = ToolResult(ok=True, data={"key": "value"}, tool_result_id="tr_001")
        d = result.to_dict()
        assert d["ok"] is True
        assert d["data"] == {"key": "value"}
        assert d["tool_result_id"] == "tr_001"

    def test_tool_error_to_dict(self):
        result = ToolResult(
            ok=False,
            error_code="INVALID_PATH",
            message="Path outside",
            retryable=False,
        )
        d = result.to_dict()
        assert d["ok"] is False
        assert d["error_code"] == "INVALID_PATH"
        assert d["retryable"] is False
