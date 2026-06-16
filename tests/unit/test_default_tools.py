"""Unit tests for default agent tools including read_code_context."""

import os
import tempfile

from unity_audit.application.models import AuditResult
from unity_audit.harness.default_tools import READ_CODE_CONTEXT_PARAMS, build_default_tools
from unity_audit.harness.tools import ToolRegistry
from unity_audit.rules.engine import Issue


def _make_minimal_audit_result(project_root="/fake/project"):
    """Create a minimal AuditResult for tool testing."""
    return AuditResult(
        project_root=project_root,
        platform="Android",
        issues=[
            Issue(
                issue_id="TEX_READ_WRITE_ENABLED_0",
                rule_id="TEX_READ_WRITE_ENABLED",
                severity="high",
                asset_path="Textures/test.png",
                title="Read/Write enabled",
                message="Texture has Read/Write enabled.",
                evidence={"read_write_enabled": True},
                suggestion="Consider disabling.",
            ),
        ],
    )


def _register_tools(audit_result):
    """Build and register all default tools in a registry."""
    registry = ToolRegistry()
    for tool_def in build_default_tools(audit_result):
        registry.register(tool_def)
    return registry


class TestReadCodeContext:
    """Tests for the read_code_context agent tool."""

    def test_read_code_context_returns_surrounding_lines(self):
        """Should return lines around the target line."""
        # Create a temp C# file
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.cs', delete=False, encoding='utf-8'
        ) as f:
            for i in range(1, 51):
                f.write(f"// Line {i}\n")
            f.write("texture.GetPixels();\n")  # Line 51
            for i in range(52, 101):
                f.write(f"// Line {i}\n")
            temp_path = f.name

        try:
            project_root = os.path.dirname(temp_path)
            file_name = os.path.basename(temp_path)

            audit_result = AuditResult(
                project_root=project_root,
                platform="Android",
                issues=[],
            )
            registry = _register_tools(audit_result)

            result = registry.execute("read_code_context", {
                "file_path": file_name,
                "line_number": 51,
                "context_lines": 10,
            })

            assert result.ok, f"Tool failed: {result.message}"
            data = result.data
            assert data["target_line"] == 51
            assert data["context_start"] <= 51 <= data["context_end"]
            assert len(data["code_lines"]) > 0

            # Find the target line
            target_lines = [cl for cl in data["code_lines"] if cl["is_target"]]
            assert len(target_lines) == 1
            assert "GetPixels" in target_lines[0]["content"]
        finally:
            os.unlink(temp_path)

    def test_read_code_context_rejects_path_traversal(self):
        """Should reject paths with .. (path traversal)."""
        audit_result = _make_minimal_audit_result()
        registry = _register_tools(audit_result)

        result = registry.execute("read_code_context", {
            "file_path": "../../etc/passwd",
            "line_number": 1,
        })

        assert not result.ok
        assert result.error_code == "PATH_ERROR"

    def test_read_code_context_rejects_non_cs_file(self):
        """Should reject non-.cs files."""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        ) as f:
            f.write("Hello world\n")
            temp_path = f.name

        try:
            project_root = os.path.dirname(temp_path)
            file_name = os.path.basename(temp_path)

            audit_result = _make_minimal_audit_result(project_root=project_root)
            registry = _register_tools(audit_result)

            result = registry.execute("read_code_context", {
                "file_path": file_name,
                "line_number": 1,
            })

            assert not result.ok
            assert result.error_code == "NOT_CS_FILE"
        finally:
            os.unlink(temp_path)

    def test_read_code_context_rejects_invalid_line(self):
        """Should reject line numbers beyond file length."""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.cs', delete=False, encoding='utf-8'
        ) as f:
            f.write("// Only one line\n")
            temp_path = f.name

        try:
            project_root = os.path.dirname(temp_path)
            file_name = os.path.basename(temp_path)

            audit_result = _make_minimal_audit_result(project_root=project_root)
            registry = _register_tools(audit_result)

            result = registry.execute("read_code_context", {
                "file_path": file_name,
                "line_number": 999,
            })

            assert not result.ok
            assert result.error_code == "INVALID_LINE"
        finally:
            os.unlink(temp_path)

    def test_read_code_context_file_not_found(self):
        """Should return error for nonexistent file."""
        audit_result = _make_minimal_audit_result()
        registry = _register_tools(audit_result)

        result = registry.execute("read_code_context", {
            "file_path": "Assets/Scripts/DoesNotExist.cs",
            "line_number": 1,
        })

        assert not result.ok
        assert result.error_code == "FILE_NOT_FOUND"

    def test_read_code_context_returns_method_boundaries(self):
        """Should detect enclosing method boundaries."""
        code = """using UnityEngine;

public class TestClass : MonoBehaviour
{
    void Start()
    {
        Debug.Log("Starting...");
        Texture2D tex = GetComponent<Texture2D>();
        tex.GetPixels();  // This is the target
        Debug.Log("Done.");
    }

    void Update()
    {
        // Another method
    }
}
"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.cs', delete=False, encoding='utf-8'
        ) as f:
            f.write(code)
            temp_path = f.name

        try:
            project_root = os.path.dirname(temp_path)
            file_name = os.path.basename(temp_path)

            audit_result = _make_minimal_audit_result(project_root=project_root)
            registry = _register_tools(audit_result)

            # Line 8 is "tex.GetPixels();" (0-indexed: 7)
            result = registry.execute("read_code_context", {
                "file_path": file_name,
                "line_number": 8,
                "context_lines": 15,
            })

            assert result.ok, f"Tool failed: {result.message}"
            data = result.data
            # Should detect method boundaries
            assert data["method_start"] is not None, "Should detect method start"
            assert data["method_end"] is not None, "Should detect method end"
            # Method Start should be around line 5
            assert 4 <= data["method_start"] <= 6, \
                f"Expected method_start around 5, got {data['method_start']}"
            # Method end should be around line 11
            assert 8 <= data["method_end"] <= 12, \
                f"Expected method_end around 11, got {data['method_end']}"
        finally:
            os.unlink(temp_path)

    def test_read_code_context_detects_comment_target(self):
        """Should identify when the target line is a comment."""
        code = """using UnityEngine;

public class TestClass : MonoBehaviour
{
    void Start()
    {
        // texture.GetPixels(); - commented out
        var x = 1;
    }
}
"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.cs', delete=False, encoding='utf-8'
        ) as f:
            f.write(code)
            temp_path = f.name

        try:
            project_root = os.path.dirname(temp_path)
            file_name = os.path.basename(temp_path)

            audit_result = _make_minimal_audit_result(project_root=project_root)
            registry = _register_tools(audit_result)

            # Line 7 is the comment
            result = registry.execute("read_code_context", {
                "file_path": file_name,
                "line_number": 7,
            })

            assert result.ok, f"Tool failed: {result.message}"
            data = result.data
            assert data["target_is_comment"] is True, \
                "Should detect target line is a comment"
        finally:
            os.unlink(temp_path)

    def test_read_code_context_detects_preprocessor_directives(self):
        """Should detect #if/#endif around the target line."""
        code = """using UnityEngine;

public class TestClass : MonoBehaviour
{
#if UNITY_EDITOR
    void EditorOnlyMethod()
    {
        Texture2D tex = new Texture2D(256, 256);
        tex.GetPixels();  // Target line
        tex.Apply();
    }
#endif
}
"""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.cs', delete=False, encoding='utf-8'
        ) as f:
            f.write(code)
            temp_path = f.name

        try:
            project_root = os.path.dirname(temp_path)
            file_name = os.path.basename(temp_path)

            audit_result = _make_minimal_audit_result(project_root=project_root)
            registry = _register_tools(audit_result)

            # Line 9 is tex.GetPixels();
            result = registry.execute("read_code_context", {
                "file_path": file_name,
                "line_number": 9,
                "context_lines": 15,
            })

            assert result.ok, f"Tool failed: {result.message}"
            data = result.data
            pp = data.get("preprocessor_directives", [])
            directives = [d["directive"] for d in pp]
            assert "if" in directives, \
                f"Should detect #if UNITY_EDITOR directive, got: {directives}"
            assert "endif" in directives, \
                f"Should detect #endif directive, got: {directives}"
        finally:
            os.unlink(temp_path)

    def test_read_code_context_with_real_test_project_file(self):
        """Should successfully read context from the test project's TextureUtils.cs."""
        test_project = os.path.join(
            os.path.dirname(__file__), "..", "..", "test_project"
        )
        test_project = os.path.abspath(test_project)

        audit_result = _make_minimal_audit_result(project_root=test_project)
        registry = _register_tools(audit_result)

        result = registry.execute("read_code_context", {
            "file_path": "Assets/Scripts/TextureUtils.cs",
            "line_number": 10,  # Color[] pixels = sourceTexture.GetPixels();
            "context_lines": 20,
        })

        assert result.ok, f"Tool failed: {result.message}"
        data = result.data
        assert data["target_line"] == 10
        assert data["total_lines"] == 22  # The actual file has 22 lines (with trailing newline)
        assert len(data["code_lines"]) > 0

        # The target line should contain GetPixels
        target_lines = [cl for cl in data["code_lines"] if cl["is_target"]]
        assert len(target_lines) == 1
        assert "GetPixels" in target_lines[0]["content"]

    def test_read_code_context_clamps_context_lines(self):
        """Should clamp context_lines between 5 and 50."""
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.cs', delete=False, encoding='utf-8'
        ) as f:
            for i in range(1, 101):
                f.write(f"// Line {i}\n")
            temp_path = f.name

        try:
            project_root = os.path.dirname(temp_path)
            file_name = os.path.basename(temp_path)

            audit_result = _make_minimal_audit_result(project_root=project_root)
            registry = _register_tools(audit_result)

            # Test with context_lines=1 (should clamp to 5)
            result = registry.execute("read_code_context", {
                "file_path": file_name,
                "line_number": 50,
                "context_lines": 1,
            })
            assert result.ok
            # Should have at least 5 lines before + target + 5 lines after = 11
            assert len(result.data["code_lines"]) >= 11

            # Test with context_lines=100 (should clamp to 50)
            result = registry.execute("read_code_context", {
                "file_path": file_name,
                "line_number": 50,
                "context_lines": 100,
            })
            assert result.ok
            assert len(result.data["code_lines"]) <= 101  # Entire file
        finally:
            os.unlink(temp_path)


class TestPathClassification:
    """Tests for the _classify_asset_path helper integrated into tools."""

    def test_reference_images_classified_as_reference(self):
        """ReferenceImages/ paths should be classified as reference_images."""
        audit_result = AuditResult(
            project_root="/fake/project",
            platform="Android",
            issues=[
                Issue(
                    issue_id="TEX_NPOT_DETECTED_0",
                    rule_id="TEX_NPOT_DETECTED",
                    severity="low",
                    asset_path="ReferenceImages/Linear/Vulkan/test.png",
                    title="NPOT",
                    message="NPOT detected.",
                    evidence={"width": 123, "height": 456},
                    suggestion="Check.",
                ),
            ],
        )
        registry = _register_tools(audit_result)
        result = registry.execute("get_issue_detail", {
            "issue_id": "TEX_NPOT_DETECTED_0",
        })
        assert result.ok
        pc = result.data["path_classification"]
        assert pc["primary_category"] == "reference_images"
        assert pc["auto_fix_safe"] is True
        assert "intentional" in pc["implication"]

    def test_ui_path_classified_as_ui(self):
        """UI/ paths should be classified as ui."""
        audit_result = AuditResult(
            project_root="/fake/project",
            platform="Android",
            issues=[
                Issue(
                    issue_id="TEX_READ_WRITE_ENABLED_0",
                    rule_id="TEX_READ_WRITE_ENABLED",
                    severity="high",
                    asset_path="UI/button_bg.png",
                    title="Read/Write",
                    message="Read/Write enabled.",
                    evidence={"read_write_enabled": True},
                    suggestion="Consider disabling.",
                ),
            ],
        )
        registry = _register_tools(audit_result)
        result = registry.execute("get_issue_detail", {
            "issue_id": "TEX_READ_WRITE_ENABLED_0",
        })
        assert result.ok
        pc = result.data["path_classification"]
        assert pc["primary_category"] == "ui"
        assert "mipmaps" in pc["implication"].lower()

    def test_editor_path_classified_as_editor_only(self):
        """Editor/ paths should be classified as editor_only."""
        audit_result = AuditResult(
            project_root="/fake/project",
            platform="iOS",
            issues=[
                Issue(
                    issue_id="ISSUE_0",
                    rule_id="TEX_NPOT_DETECTED",
                    severity="low",
                    asset_path="Editor/SomeTool/icon.png",
                    title="NPOT",
                    message="NPOT detected.",
                    evidence={"width": 123, "height": 456},
                    suggestion="Check.",
                ),
            ],
        )
        registry = _register_tools(audit_result)
        result = registry.execute("get_issue_detail", {"issue_id": "ISSUE_0"})
        assert result.ok
        pc = result.data["path_classification"]
        assert pc["primary_category"] == "editor_only"
        assert pc["auto_fix_safe"] is True
        assert "platform" in pc["implication"].lower()

    def test_unknown_path_gets_default(self):
        """Unrecognized paths should get 'unknown' category."""
        audit_result = AuditResult(
            project_root="/fake/project",
            platform="Android",
            issues=[
                Issue(
                    issue_id="ISSUE_0",
                    rule_id="TEX_NPOT_DETECTED",
                    severity="low",
                    asset_path="MiscStuff/random.png",
                    title="NPOT",
                    message="NPOT detected.",
                    evidence={"width": 123, "height": 456},
                    suggestion="Check.",
                ),
            ],
        )
        registry = _register_tools(audit_result)
        result = registry.execute("get_issue_detail", {"issue_id": "ISSUE_0"})
        assert result.ok
        pc = result.data["path_classification"]
        assert pc["primary_category"] == "unknown"
        assert pc["auto_fix_safe"] is False

    def test_scenes_ui_takes_priority_over_scenes(self):
        """Scenes/UI/ should match ui (more specific) before scenes."""
        audit_result = AuditResult(
            project_root="/fake/project",
            platform="Android",
            issues=[
                Issue(
                    issue_id="ISSUE_0",
                    rule_id="TEX_READ_WRITE_ENABLED",
                    severity="high",
                    asset_path="Scenes/UI/MainMenu/bg.png",
                    title="RW",
                    message="RW enabled.",
                    evidence={"read_write_enabled": True},
                    suggestion="Check.",
                ),
            ],
        )
        registry = _register_tools(audit_result)
        result = registry.execute("get_issue_detail", {"issue_id": "ISSUE_0"})
        assert result.ok
        pc = result.data["path_classification"]
        assert pc["primary_category"] == "ui", \
            f"Expected 'ui' for Scenes/UI/ path, got '{pc['primary_category']}'"

    def test_third_party_path(self):
        """ThirdParty/ paths should be classified appropriately."""
        audit_result = AuditResult(
            project_root="/fake/project",
            platform="Android",
            issues=[
                Issue(
                    issue_id="ISSUE_0",
                    rule_id="TEX_READ_WRITE_ENABLED",
                    severity="high",
                    asset_path="ThirdParty/DOTween/icon.png",
                    title="RW",
                    message="RW enabled.",
                    evidence={"read_write_enabled": True},
                    suggestion="Check.",
                ),
            ],
        )
        registry = _register_tools(audit_result)
        result = registry.execute("get_issue_detail", {"issue_id": "ISSUE_0"})
        assert result.ok
        pc = result.data["path_classification"]
        assert pc["primary_category"] == "third_party"
        assert "overwritten" in pc["implication"].lower()

    def test_path_classification_in_get_asset_info(self):
        """get_asset_info should also include path_classification."""
        from unity_audit.scanner import AssetInfo

        audit_result = AuditResult(
            project_root="/fake/project",
            platform="Android",
            issues=[],
        )
        audit_result.assets = [
            AssetInfo(
                asset_path="UI/button_bg.png",
                asset_type="Texture",
                extension=".png",
                file_size=1024,
                meta_path="UI/button_bg.png.meta",
                absolute_path="/fake/project/Assets/UI/button_bg.png",
            ),
        ]
        registry = _register_tools(audit_result)
        result = registry.execute("get_asset_info", {"asset_path": "UI/button_bg.png"})
        assert result.ok
        pc = result.data["path_classification"]
        assert pc["primary_category"] == "ui"


class TestTracePrefabReferences:
    """Tests for the trace_prefab_references agent tool."""

    def _make_result_for_project(self):
        """Create an AuditResult with the test project for prefab tracing."""
        from unity_audit.meta_parser import MetaInfo
        from unity_audit.scanner import AssetInfo

        test_project = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "test_project"
        ))

        result = AuditResult(
            project_root=test_project,
            platform="Android",
            issues=[],
            assets=[
                AssetInfo(
                    asset_path="Prefabs/UI_MainPanel.prefab",
                    asset_type="Prefab",
                    extension=".prefab",
                    file_size=1024,
                    meta_path="Prefabs/UI_MainPanel.prefab.meta",
                    absolute_path=os.path.join(
                        test_project, "Assets", "Prefabs", "UI_MainPanel.prefab"
                    ),
                ),
                AssetInfo(
                    asset_path="Scenes/MainScene.unity",
                    asset_type="Scene",
                    extension=".unity",
                    file_size=2048,
                    meta_path="Scenes/MainScene.unity.meta",
                    absolute_path=os.path.join(
                        test_project, "Assets", "Scenes", "MainScene.unity"
                    ),
                ),
                AssetInfo(
                    asset_path="UI/button_bg.png",
                    asset_type="Texture",
                    extension=".png",
                    file_size=512,
                    meta_path="UI/button_bg.png.meta",
                    absolute_path=os.path.join(
                        test_project, "Assets", "UI", "button_bg.png"
                    ),
                ),
            ],
        )
        # Add meta with GUIDs to simulate GUID→asset resolution
        result.meta_map = {
            "Prefabs/UI_MainPanel.prefab": MetaInfo(
                guid="abcdef1234567890abcdef1234567890",
                importer_type="NativeFormatImporter",
            ),
            "Scenes/MainScene.unity": MetaInfo(
                guid="fedcba0987654321fedcba0987654321",
                importer_type="NativeFormatImporter",
            ),
            "UI/button_bg.png": MetaInfo(
                guid="aabbccddeeff00112233445566778899",
                importer_type="TextureImporter",
            ),
        }

        return result

    def test_trace_ui_prefab(self):
        """Should parse UI_MainPanel.prefab and extract game objects and components."""
        audit_result = self._make_result_for_project()
        registry = _register_tools(audit_result)

        result = registry.execute("trace_prefab_references", {
            "prefab_path": "Prefabs/UI_MainPanel.prefab",
        })

        assert result.ok, f"Tool failed: {result.message}"
        data = result.data
        assert data["prefab_type"] == "prefab"
        assert data["game_object_count"] == 2  # MainPanel, Canvas
        # Should detect components
        comps = data["component_summary"]
        assert "MonoBehaviour" in comps
        assert "Canvas" in comps or "RectTransform" in comps

    def test_trace_scene(self):
        """Should parse MainScene.unity."""
        audit_result = self._make_result_for_project()
        registry = _register_tools(audit_result)

        result = registry.execute("trace_prefab_references", {
            "prefab_path": "Scenes/MainScene.unity",
        })

        assert result.ok, f"Tool failed: {result.message}"
        data = result.data
        assert data["prefab_type"] == "scene"
        assert data["game_object_count"] == 1  # MainCamera

    def test_trace_resolves_guid_to_asset(self):
        """Should resolve GUID references to asset paths using guid→asset map."""
        audit_result = self._make_result_for_project()
        # The prefab has a guid: aabbccddeeff00112233445566778899
        # This should resolve to UI/button_bg.png
        registry = _register_tools(audit_result)

        result = registry.execute("trace_prefab_references", {
            "prefab_path": "Prefabs/UI_MainPanel.prefab",
        })

        assert result.ok, f"Tool failed: {result.message}"
        data = result.data
        # Check that at least one reference resolves to UI/button_bg.png
        assets = [a["target_asset"] for a in data["referenced_assets"]]
        # The prefab has a script with guid aabbcc... which maps to button_bg.png
        assert any("button_bg.png" in str(a) for a in assets), \
            f"Expected button_bg.png in referenced assets, got: {assets}"

    def test_trace_rejects_non_prefab(self):
        """Should reject .cs files."""
        audit_result = self._make_result_for_project()
        registry = _register_tools(audit_result)

        result = registry.execute("trace_prefab_references", {
            "prefab_path": "Assets/Scripts/TextureUtils.cs",
        })

        assert not result.ok
        assert result.error_code == "NOT_PREFAB_OR_SCENE"

    def test_trace_file_not_found(self):
        """Should handle nonexistent prefab."""
        audit_result = self._make_result_for_project()
        registry = _register_tools(audit_result)

        result = registry.execute("trace_prefab_references", {
            "prefab_path": "Prefabs/DoesNotExist.prefab",
        })

        assert not result.ok
        assert result.error_code == "FILE_NOT_FOUND"

    def test_trace_path_traversal(self):
        """Should reject path traversal."""
        audit_result = self._make_result_for_project()
        registry = _register_tools(audit_result)

        result = registry.execute("trace_prefab_references", {
            "prefab_path": "../../etc/passwd.prefab",
        })

        assert not result.ok
        assert result.error_code == "PATH_ERROR"


class TestReadCodeContextParams:
    """Verify the JSON Schema for read_code_context parameters."""

    def test_params_schema_has_required_fields(self):
        schema = READ_CODE_CONTEXT_PARAMS
        assert "file_path" in schema["required"]
        assert "line_number" in schema["required"]
        assert "context_lines" not in schema["required"]  # Optional with default

    def test_params_schema_types(self):
        schema = READ_CODE_CONTEXT_PARAMS
        props = schema["properties"]
        assert props["file_path"]["type"] == "string"
        assert props["line_number"]["type"] == "integer"
        assert props["context_lines"]["type"] == "integer"
        assert props["context_lines"].get("default") == 20
