"""Evidence Builder - Search code for context that affects fix risk assessment.

For high-risk issues, searches the project's C# files for APIs that indicate
whether a fix is safe to apply automatically. Now distinguishes between:
  - direct: API usage + asset reference (GUID, path, or name) in same context
  - possible: API usage exists but no direct link to this specific asset
  - none: No relevant API or association found
"""

import os
import re
from dataclasses import dataclass, field

from unity_audit.rules.engine import Issue

# Pixel read/write APIs that indicate Read/Write is needed
PIXEL_RW_APIS = [
    "GetPixels",
    "SetPixels",
    "GetPixel",
    "SetPixel",
    "EncodeToPNG",
    "EncodeToJPG",
    r"Texture2D\.Apply",
    r"Texture2D\.ReadPixels",
]


@dataclass
class CodeEvidence:
    """A single piece of code evidence with association metadata."""
    file: str              # Relative path to .cs file
    line: int              # Line number
    content: str           # The matched line content (trimmed)
    api: str               # The API that was matched
    association_type: str  # direct, possible, none
    association_value: str # What created the association (asset name, GUID, etc.)
    confidence: float      # 0.0 to 1.0


@dataclass
class EvidenceResult:
    """Evidence gathered for a specific issue."""
    issue_id: str
    context_summary: str = ""
    risk_hint: str = ""
    need_manual_confirm: bool = True
    association_level: str = "none"  # direct, possible, none
    code_evidence: list[CodeEvidence] = field(default_factory=list)

    @property
    def code_search_result(self) -> list[dict]:
        """Backward-compatible accessor for existing consumers."""
        return [
            {
                "file": e.file,
                "line": e.line,
                "content": e.content,
                "api": e.api,
                "association_type": e.association_type,
                "association_value": e.association_value,
                "confidence": e.confidence,
            }
            for e in self.code_evidence
        ]


def _is_comment_line(line: str) -> bool:
    """Check if a line is purely a comment (no executable code).

    Returns True if the entire line is a comment.
    """
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("//"):
        return True
    if stripped.startswith("/*") and stripped.endswith("*/"):
        return True
    if stripped.startswith("*") and not stripped.startswith("*/"):
        return True  # Block comment continuation
    return False


def _strip_comments(line: str) -> str:
    """Remove single-line comments from a line, returning the code portion.

    Returns empty string if nothing but comment remains.
    """
    # Find // that is not inside a string
    in_string = False
    string_char = None
    for i, ch in enumerate(line):
        if ch in ('"', "'") and (i == 0 or line[i - 1] != "\\"):
            if not in_string:
                in_string = True
                string_char = ch
            elif ch == string_char:
                in_string = False
                string_char = None
        if ch == "/" and i + 1 < len(line) and line[i + 1] == "/" and not in_string:
            return line[:i].strip()
    return line.strip()


def _get_asset_search_terms(asset_name: str) -> list[str]:
    """Generate alternative search terms for an asset name.

    For snake_case names (e.g., "linked_texture"), also generates:
    - Underscore-stripped: "linkedtexture"
    - PascalCase: "LinkedTexture"
    - camelCase: "linkedTexture"

    This allows matching C# identifiers that may differ from the file name.
    """
    terms = [asset_name]
    # Underscore-stripped version
    no_underscore = asset_name.replace("_", "")
    if no_underscore != asset_name:
        terms.append(no_underscore)
        # PascalCase: capitalize each segment
        pascal = "".join(part.capitalize() for part in asset_name.split("_"))
        terms.append(pascal)
        # camelCase: same as pascal but first letter lowercase
        camel = pascal[0].lower() + pascal[1:] if pascal else pascal
        terms.append(camel)
    return terms


def _check_name_match(line_lower: str, search_terms: list[str]) -> bool:
    """Check if any search term appears in the line (case-insensitive)."""
    for term in search_terms:
        if term.lower() in line_lower:
            return True
    return False


def _search_code_for_api_with_association(
    project_root: str,
    api_pattern: str,
    asset_name: str,
    asset_path: str,
    guid: str | None,
) -> list[CodeEvidence]:
    """Search C# files for an API pattern and determine association level.

    For each API hit, checks whether the same file also references the target
    asset by name, GUID, or path to establish the association level.

    Args:
        project_root: Absolute path to the Unity project root.
        api_pattern: Regex pattern for the API to search.
        asset_name: The asset filename without extension (e.g., "linked_texture").
        asset_path: The full asset path relative to Assets/ (e.g., "Textures/linked_texture.png").
        guid: Optional GUID of the asset.

    Returns:
        List of CodeEvidence with association metadata.
    """
    results: list[CodeEvidence] = []
    assets_cs = os.path.join(project_root, "Assets")

    if not os.path.isdir(assets_cs):
        return results

    try:
        compiled = re.compile(api_pattern)
    except re.error:
        return results

    # Collect all .cs files first
    cs_files = []
    for dirpath, dirnames, filenames in os.walk(assets_cs):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            if filename.endswith(".cs"):
                cs_files.append(os.path.join(dirpath, filename))

    # Generate flexible search terms for asset name
    search_terms = _get_asset_search_terms(asset_name)

    for filepath in cs_files:
        try:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue

        full_content = "".join(lines)
        full_lower = full_content.lower()
        rel_path = os.path.relpath(filepath, project_root)

        # Determine file-level association signals
        file_has_asset_name = _check_name_match(full_lower, search_terms)
        file_has_guid = guid and guid.lower() in full_lower
        file_has_path = asset_path.lower() in full_lower

        for i, line in enumerate(lines, start=1):
            # Skip pure comment lines
            if _is_comment_line(line):
                continue

            # Strip comments from the line before matching
            code_line = _strip_comments(line)
            if not code_line:
                continue

            match = compiled.search(code_line)
            if not match:
                continue

            # Determine association type for this hit
            line_lower = code_line.lower()

            # Check for direct association on this line
            name_on_line = _check_name_match(line_lower, search_terms)
            guid_on_line = guid and guid.lower() in line_lower
            path_on_line = asset_path.lower() in line_lower

            # Direct: asset reference is on the same line as the API
            if name_on_line or guid_on_line or path_on_line:
                assoc_type = "direct"
                assoc_value = _build_assoc_value(
                    asset_name, guid, asset_path,
                    name_on_line, guid_on_line, path_on_line,
                )
                confidence = 0.95
            # Possible: asset reference exists somewhere in the file
            elif file_has_asset_name or file_has_guid or file_has_path:
                assoc_type = "possible"
                assoc_value = _build_assoc_value(
                    asset_name, guid, asset_path,
                    file_has_asset_name, file_has_guid, file_has_path,
                )
                confidence = 0.5
            # None: API exists but no asset reference found
            else:
                assoc_type = "none"
                assoc_value = f"API {api_pattern} found, no asset reference for {asset_name}"
                confidence = 0.1

            results.append(CodeEvidence(
                file=rel_path,
                line=i,
                content=line.strip()[:200],
                api=api_pattern,
                association_type=assoc_type,
                association_value=assoc_value,
                confidence=confidence,
            ))

    return results


def _build_assoc_value(
    asset_name: str,
    guid: str | None,
    asset_path: str,
    name_match: bool,
    guid_match: bool,
    path_match: bool,
) -> str:
    """Build a human-readable association value string."""
    parts = []
    if name_match:
        parts.append(f"name={asset_name}")
    if guid_match and guid:
        parts.append(f"GUID={guid}")
    if path_match:
        parts.append(f"path={asset_path}")
    return ", ".join(parts) if parts else "unknown"


def _deduplicate_evidence(evidence_list: list[CodeEvidence]) -> list[CodeEvidence]:
    """Deduplicate evidence by (file, line, api). Keep the highest confidence."""
    seen: dict[tuple[str, int, str], CodeEvidence] = {}
    for e in evidence_list:
        key = (e.file, e.line, e.api)
        if key in seen:
            if e.confidence > seen[key].confidence:
                seen[key] = e
        else:
            seen[key] = e
    return list(seen.values())


def _determine_association_level(evidence_list: list[CodeEvidence]) -> str:
    """Determine the overall association level from a list of evidence.

    - direct: at least one evidence item has direct association
    - possible: at least one has possible (but none direct)
    - none: only none associations or empty list
    """
    if not evidence_list:
        return "none"
    types = {e.association_type for e in evidence_list}
    if "direct" in types:
        return "direct"
    if "possible" in types:
        return "possible"
    return "none"


def build_evidence_for_issues(
    project_root: str,
    issues: list[Issue],
    meta_guid_map: dict[str, str | None],
) -> dict[str, EvidenceResult]:
    """Build evidence for issues that need context.

    For TEX_READ_WRITE_ENABLED issues, searches for pixel read/write API usage
    and determines if the asset is directly referenced by the code that uses
    those APIs.

    Args:
        project_root: Absolute path to Unity project root.
        issues: List of all issues found.
        meta_guid_map: Mapping of asset_path -> GUID.

    Returns:
        Dict of issue_id -> EvidenceResult.
    """
    evidence_map: dict[str, EvidenceResult] = {}

    for issue in issues:
        evidence = EvidenceResult(issue_id=issue.issue_id)

        if issue.rule_id == "TEX_READ_WRITE_ENABLED":
            asset_name = os.path.splitext(os.path.basename(issue.asset_path))[0]
            guid = meta_guid_map.get(issue.asset_path)

            all_evidence: list[CodeEvidence] = []
            for api in PIXEL_RW_APIS:
                hits = _search_code_for_api_with_association(
                    project_root, api, asset_name, issue.asset_path, guid
                )
                all_evidence.extend(hits)

            # Deduplicate
            all_evidence = _deduplicate_evidence(all_evidence)
            evidence.code_evidence = all_evidence
            evidence.association_level = _determine_association_level(all_evidence)

            if evidence.association_level == "direct":
                files_involved = list(set(e.file for e in all_evidence if e.association_type == "direct"))
                evidence.context_summary = (
                    f"Found {len(all_evidence)} pixel read/write API usage(s) "
                    f"with direct asset references in {len(files_involved)} file(s)."
                )
                evidence.risk_hint = (
                    "代码直接引用此资源并使用像素读写 API，关闭 Read/Write 可能导致运行时错误"
                )
                evidence.need_manual_confirm = True
            elif evidence.association_level == "possible":
                files_involved = list(set(e.file for e in all_evidence))
                evidence.context_summary = (
                    f"Found {len(all_evidence)} pixel read/write API usage(s) "
                    f"in {len(files_involved)} file(s), but no direct reference to this asset."
                )
                evidence.risk_hint = (
                    "存在像素读写 API 但未确认与此资源直接关联，需人工判断"
                )
                evidence.need_manual_confirm = True
            else:
                evidence.context_summary = (
                    "No pixel read/write API usage found in codebase, "
                    "or no association with this asset."
                )
                evidence.risk_hint = "未发现像素读写 API 与此资源有明确关联"
                evidence.need_manual_confirm = True

        elif issue.rule_id == "TEX_UI_MIPMAP_ENABLED":
            path_lower = issue.asset_path.lower()
            special_paths = ["worldspaceui", "billboard", "minimap"]
            found_special = [p for p in special_paths if p in path_lower]

            if found_special:
                evidence.context_summary = (
                    f"UI texture path contains special keywords: {found_special}. "
                    f"These may legitimately need mipmaps."
                )
                evidence.risk_hint = "特殊 UI 路径，关闭 Mipmap 前需要确认使用场景"
                evidence.need_manual_confirm = True
                evidence.association_level = "possible"
            else:
                evidence.context_summary = (
                    "Standard UI texture, mipmap typically not needed."
                )
                evidence.risk_hint = "普通 UI 贴图，关闭 Mipmap 风险很低"
                evidence.need_manual_confirm = False
                evidence.association_level = "none"

        elif issue.rule_id == "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD":
            evidence.context_summary = (
                "Long audio with Decompress On Load detected. "
                "Switching load type requires understanding of playback patterns."
            )
            evidence.risk_hint = (
                "修改加载方式需了解音频播放场景，如频繁重播则 Compressed In Memory 更合适"
            )
            evidence.need_manual_confirm = True
            evidence.association_level = "none"

        elif issue.rule_id == "PREFAB_MISSING_SCRIPT":
            evidence.context_summary = (
                f"Missing script reference in {issue.asset_path}. "
                f"Cannot determine the missing script type automatically."
            )
            evidence.risk_hint = (
                "Missing Script 无法自动修复，需人工定位缺失的脚本并恢复引用"
            )
            evidence.need_manual_confirm = True
            evidence.association_level = "none"

        else:
            evidence.context_summary = "No additional context available."
            evidence.need_manual_confirm = True
            evidence.association_level = "none"

        evidence_map[issue.issue_id] = evidence

    return evidence_map
