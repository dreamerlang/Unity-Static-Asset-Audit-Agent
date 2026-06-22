"""Lightweight semantic signals for C# code evidence.

This module does not attempt to fully parse C#. It extracts conservative,
structured hints that help the Agent interpret source context without asking
the model to infer every fact from raw text.
"""

import re
from dataclasses import asdict, dataclass, field


UNITY_LIFECYCLE_METHODS = {
    "Awake", "Start", "OnEnable", "OnDisable", "OnDestroy",
    "Update", "LateUpdate", "FixedUpdate", "OnGUI", "OnRenderImage",
}
HOT_PATH_METHODS = {"Update", "LateUpdate", "FixedUpdate", "OnGUI", "OnRenderImage"}
RELEVANT_APIS = (
    "GetPixels", "SetPixels", "GetPixel", "SetPixel", "ReadPixels",
    "EncodeToPNG", "EncodeToJPG", "Apply", "Resources.Load",
    "Addressables.LoadAssetAsync", "AssetDatabase.LoadAssetAtPath",
)


@dataclass
class CodeSemanticSignals:
    """Structured interpretation of a code evidence location."""

    execution_scope: str = "runtime"  # runtime, editor_only, test_only, development_only
    enclosing_type: str | None = None
    enclosing_method: str | None = None
    unity_lifecycle_method: bool = False
    hot_path: bool = False
    active_preprocessor_guards: list[str] = field(default_factory=list)
    relevant_api_calls: list[str] = field(default_factory=list)
    risk_modifiers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


_TYPE_RE = re.compile(r"\b(?:class|struct|interface)\s+([A-Za-z_]\w*)")
_METHOD_RE = re.compile(
    r"^(?:(?:public|private|protected|internal|static|virtual|override|async|sealed|new)\s+)*"
    r"(?:[A-Za-z_]\w*(?:\s*<[^>]+>)?(?:\[\])?[?.]?)\s+"
    r"([A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|$)"
)
_CONTROL_KEYWORDS = {"if", "for", "foreach", "while", "switch", "catch", "using", "lock"}


def _active_preprocessor_guards(lines: list[str], target_index: int) -> list[str]:
    """Return active #if/#elif conditions at the target line."""
    stack: list[str] = []
    directive_re = re.compile(r"^\s*#\s*(if|elif|else|endif)\b\s*(.*)$")
    for line in lines[:target_index + 1]:
        match = directive_re.match(line)
        if not match:
            continue
        directive, condition = match.group(1), match.group(2).strip()
        if directive == "if":
            stack.append(condition)
        elif directive == "elif" and stack:
            stack[-1] = condition
        elif directive == "else" and stack:
            stack[-1] = f"else({stack[-1]})"
        elif directive == "endif" and stack:
            stack.pop()
    return stack


def _find_enclosing_symbol(lines: list[str], target_index: int) -> tuple[str | None, str | None]:
    """Find the nearest enclosing type and method declarations."""
    enclosing_type = None
    enclosing_method = None
    for index in range(target_index, -1, -1):
        stripped = lines[index].strip()
        if enclosing_method is None:
            method_match = _METHOD_RE.match(stripped)
            if method_match and method_match.group(1) not in _CONTROL_KEYWORDS:
                enclosing_method = method_match.group(1)
        if enclosing_type is None:
            type_match = _TYPE_RE.search(stripped)
            if type_match:
                enclosing_type = type_match.group(1)
        if enclosing_type and enclosing_method:
            break
    return enclosing_type, enclosing_method


def analyze_code_context(
    file_path: str,
    lines: list[str],
    target_line: int,
) -> CodeSemanticSignals:
    """Extract conservative semantic signals around a 1-indexed target line."""
    target_index = max(0, min(target_line - 1, len(lines) - 1)) if lines else 0
    normalized_path = file_path.replace("\\", "/").lower()
    guards = _active_preprocessor_guards(lines, target_index) if lines else []
    enclosing_type, enclosing_method = _find_enclosing_symbol(lines, target_index) if lines else (None, None)

    if "/editor/" in f"/{normalized_path}" or normalized_path.endswith("editor.cs"):
        execution_scope = "editor_only"
    elif any(part in normalized_path for part in ("/tests/", "/test/", "test.cs")):
        execution_scope = "test_only"
    else:
        execution_scope = "runtime"

    guard_text = " ".join(guards).upper()
    if "UNITY_EDITOR" in guard_text:
        execution_scope = "editor_only"
    elif "DEVELOPMENT_BUILD" in guard_text or "UNITY_ASSERTIONS" in guard_text:
        execution_scope = "development_only"

    context_start = max(0, target_index - 20)
    context_end = min(len(lines), target_index + 21)
    context = "\n".join(lines[context_start:context_end])
    api_calls = [api for api in RELEVANT_APIS if api in context]

    lifecycle = enclosing_method in UNITY_LIFECYCLE_METHODS
    hot_path = enclosing_method in HOT_PATH_METHODS
    modifiers = []
    if execution_scope in {"editor_only", "test_only", "development_only"}:
        modifiers.append(f"non_runtime_scope:{execution_scope}")
    if hot_path:
        modifiers.append("per_frame_or_render_hot_path")
    if lifecycle and not hot_path:
        modifiers.append("unity_lifecycle_one_shot_or_event")

    return CodeSemanticSignals(
        execution_scope=execution_scope,
        enclosing_type=enclosing_type,
        enclosing_method=enclosing_method,
        unity_lifecycle_method=lifecycle,
        hot_path=hot_path,
        active_preprocessor_guards=guards,
        relevant_api_calls=api_calls,
        risk_modifiers=modifiers,
    )
