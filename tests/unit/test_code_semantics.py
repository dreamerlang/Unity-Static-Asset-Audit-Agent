"""Tests for lightweight C# semantic signal extraction."""

from unity_audit.harness.code_semantics import analyze_code_context


def test_detects_editor_guard_and_lifecycle_method():
    lines = [
        "public class TextureProcessor : MonoBehaviour\n",
        "{\n",
        "    void Start()\n",
        "    {\n",
        "#if UNITY_EDITOR\n",
        "        texture.GetPixels();\n",
        "#endif\n",
        "    }\n",
        "}\n",
    ]

    signals = analyze_code_context("Assets/Scripts/TextureProcessor.cs", lines, 6)

    assert signals.execution_scope == "editor_only"
    assert signals.enclosing_type == "TextureProcessor"
    assert signals.enclosing_method == "Start"
    assert signals.unity_lifecycle_method is True
    assert signals.hot_path is False
    assert signals.active_preprocessor_guards == ["UNITY_EDITOR"]
    assert "GetPixels" in signals.relevant_api_calls


def test_detects_runtime_hot_path():
    lines = [
        "public class RuntimePainter : MonoBehaviour\n",
        "{\n",
        "    void Update()\n",
        "    {\n",
        "        runtimeTexture.SetPixels(colors);\n",
        "    }\n",
        "}\n",
    ]

    signals = analyze_code_context("Assets/Scripts/RuntimePainter.cs", lines, 5)

    assert signals.execution_scope == "runtime"
    assert signals.enclosing_method == "Update"
    assert signals.hot_path is True
    assert "per_frame_or_render_hot_path" in signals.risk_modifiers
    assert "SetPixels" in signals.relevant_api_calls


def test_detects_test_only_path():
    signals = analyze_code_context(
        "Assets/Tests/TextureAuditTest.cs",
        ["public class TextureAuditTest {}\n"],
        1,
    )

    assert signals.execution_scope == "test_only"
