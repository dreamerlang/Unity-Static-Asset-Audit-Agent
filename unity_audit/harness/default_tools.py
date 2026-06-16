"""Default Agent Tools - Read-only tools for the Agent Harness.

These tools provide the agent with access to scan data, extracted properties,
evidence, and code search results. All are read-only with no side effects.

Each tool is a closure that captures the AuditResult for data lookup.
"""

import os

from unity_audit.application.models import AuditResult
from unity_audit.harness.tools import ToolDef, ToolResult, validate_path_in_project

# ── JSON Schemas for tool parameters ────────────────────────────────────

GET_ISSUE_DETAIL_PARAMS = {
    "type": "object",
    "properties": {
        "issue_id": {
            "type": "string",
            "description": "The issue ID to get details for (e.g., TEX_READ_WRITE_ENABLED_0_Textures_char_png)",
        },
    },
    "required": ["issue_id"],
}

GET_ASSET_INFO_PARAMS = {
    "type": "object",
    "properties": {
        "asset_path": {
            "type": "string",
            "description": "The asset path relative to the project (e.g., Textures/char_diffuse.png)",
        },
    },
    "required": ["asset_path"],
}

SEARCH_ASSET_CODE_PARAMS = {
    "type": "object",
    "properties": {
        "asset_path": {
            "type": "string",
            "description": "The asset path to search code references for",
        },
        "search_type": {
            "type": "string",
            "enum": ["api_usage", "all"],
            "description": "Type of search: 'api_usage' for pixel/audio API calls, 'all' for any reference",
            "default": "all",
        },
    },
    "required": ["asset_path"],
}

READ_CODE_CONTEXT_PARAMS = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "Relative path to the C# file within the project (e.g., 'Assets/Scripts/TextureUtils.cs'). Must be from code evidence matches.",
        },
        "line_number": {
            "type": "integer",
            "description": "The line number to center the context on (from code evidence match). The target line will be highlighted.",
        },
        "context_lines": {
            "type": "integer",
            "description": "Number of lines to show before and after the target line (default 20, max 50).",
            "default": 20,
        },
    },
    "required": ["file_path", "line_number"],
}

TRACE_PREFAB_REFERENCES_PARAMS = {
    "type": "object",
    "properties": {
        "prefab_path": {
            "type": "string",
            "description": "Path to the .prefab or .unity file relative to the project (e.g., 'Prefabs/UI_MainPanel.prefab').",
        },
    },
    "required": ["prefab_path"],
}

LIST_PROJECT_ASSETS_PARAMS = {
    "type": "object",
    "properties": {
        "asset_type": {
            "type": "string",
            "enum": ["Texture", "Audio", "Prefab", "Scene"],
            "description": "Filter by asset type. Omit for all types.",
        },
    },
}

SUBMIT_ASSESSMENT_PARAMS = {
    "type": "object",
    "properties": {
        "issue_id": {
            "type": "string",
            "description": "The issue ID being assessed",
        },
        "risk_level": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": "Risk level of taking the recommended action",
        },
        "recommended_action": {
            "type": "string",
            "enum": ["auto_fix_candidate", "manual_confirm_required", "do_not_fix"],
            "description": "Recommended fix action",
        },
        "confidence": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "Confidence in this assessment (0.0-1.0)",
        },
        "summary": {
            "type": "string",
            "description": "Analysis summary explaining the assessment",
        },
        "needs_human_review": {
            "type": "boolean",
            "description": "Whether a human should review this assessment",
        },
        "evidence_refs": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of tool_result_ids used as evidence",
        },
    },
    "required": ["issue_id", "risk_level", "recommended_action", "confidence", "summary"],
}


# ── Tool factory ─────────────────────────────────────────────────────────

def build_default_tools(audit_result: AuditResult) -> list[ToolDef]:
    """Build the default set of read-only agent tools.

    Each tool is a closure capturing the audit_result for data lookup.
    All tools are read-only, no side effects.

    Args:
        audit_result: The completed deterministic AuditResult with all scan data.

    Returns:
        List of ToolDef ready for registration in a ToolRegistry.
    """
    project_root = audit_result.project_root

    # Pre-compute lookup maps for efficient access
    _issues_by_id = {i.issue_id: i for i in audit_result.issues}
    _issues_by_asset = {}
    for i in audit_result.issues:
        _issues_by_asset.setdefault(i.asset_path, []).append(i)
    _decisions_by_id = {d.issue_id: d for d in audit_result.fix_decisions}

    # Reverse GUID → asset_path map for prefab reference resolution
    _guid_to_asset: dict[str, str] = {}
    for asset_path, meta in audit_result.meta_map.items():
        if meta.guid:
            _guid_to_asset[meta.guid.lower()] = asset_path

    # ── Path classification helper ─────────────────────────────────────

    # Patterns for classifying asset paths by usage context
    _PATH_CLASSIFIERS = [
        # (regex pattern, category, implication, auto_fix_safe)
        (r'(^|/)ReferenceImages/', 'reference_images',
         'Test/screenshot reference image — issues are usually intentional, safe to exclude from fixes', True),
        (r'(^|/)Snapshots/', 'reference_images',
         'Test/screenshot snapshot — issues are usually intentional, safe to exclude from fixes', True),
        (r'(^|/)TestAssets/', 'test_assets',
         'Test asset — issues may be intentional for test coverage', True),
        (r'(^|/)Editor/', 'editor_only',
         'Editor-only asset — platform rules (Android/iOS) do not apply', True),
        (r'(^|/)Editor Only/', 'editor_only',
         'Editor-only asset — platform rules do not apply', True),
        (r'(^|/)ThirdParty/', 'third_party',
         'Third-party/vendor asset — changes may be overwritten on update, caution needed', False),
        (r'(^|/)Plugins/', 'third_party',
         'Plugin asset — changes may be overwritten on update, caution needed', False),
        (r'(^|/)Vendor/', 'third_party',
         'Vendor asset — changes may be overwritten on update, caution needed', False),
        (r'(^|/)UI/', 'ui',
         'UI texture — mipmaps should be off, max size should match screen usage', False),
        (r'(^|/)Scenes/UI/', 'ui',
         'UI scene texture — directly visible on screen, size matters', False),
        (r'(^|/)Characters/', 'character',
         'Character asset — mipmaps likely needed for 3D rendering at varying distances', False),
        (r'(^|/)Models/', 'character',
         'Model asset — mipmaps likely needed for 3D rendering', False),
        (r'(^|/)Environment/', 'environment',
         'Environment asset — mipmaps needed, platform compression important', False),
        (r'(^|/)Audio/SFX/', 'sfx_audio',
         'Sound effect — should be mono, streaming or compressed-in-memory for long clips', False),
        (r'(^|/)Audio/Music/', 'music_audio',
         'Music track — streaming recommended for long clips, stereo acceptable', False),
        (r'(^|/)Audio/', 'audio',
         'General audio — evaluate based on clip length and purpose', False),
        (r'(^|/)Textures/', 'texture',
         'General texture — evaluate based on usage context', False),
        (r'(^|/)Scenes/', 'scene',
         'Scene asset — evaluate based on scene purpose', False),
        (r'(^|/)Prefabs/', 'prefab',
         'Prefab asset — evaluate based on what it contains', False),
        (r'(^|/)Resources/', 'resource',
         'Resources folder — always loaded, optimize aggressively', False),
    ]

    def _classify_asset_path(asset_path: str) -> dict:
        """Classify an asset path into usage categories based on directory naming.

        Returns structured classification data that the LLM can use to adjust
        risk assessment based on project conventions.
        """
        import re
        path_normalized = asset_path.replace('\\', '/')

        classifications = []
        for pattern, category, implication, auto_fix_safe in _PATH_CLASSIFIERS:
            if re.search(pattern, path_normalized):
                classifications.append({
                    "category": category,
                    "implication": implication,
                    "auto_fix_safe": auto_fix_safe,
                })
                break  # First match wins (most specific patterns first)

        if not classifications:
            classifications.append({
                "category": "unknown",
                "implication": "No special directory convention detected — evaluate by asset type and rules",
                "auto_fix_safe": False,
            })

        return {
            "primary_category": classifications[0]["category"],
            "implication": classifications[0]["implication"],
            "auto_fix_safe": classifications[0]["auto_fix_safe"],
            "all_matches": classifications,
        }

    # ── get_issue_detail ────────────────────────────────────────────────

    def _get_issue_detail(issue_id: str) -> ToolResult:
        """Get detailed information about a specific issue."""
        issue = _issues_by_id.get(issue_id)
        if issue is None:
            return ToolResult(
                ok=False,
                error_code="ISSUE_NOT_FOUND",
                message=f"Issue '{issue_id}' not found. Available: {list(_issues_by_id.keys())[:10]}",
            )

        # Gather asset meta
        meta = audit_result.meta_map.get(issue.asset_path)
        meta_data = None
        if meta is not None:
            meta_data = {
                "guid": meta.guid,
                "importer_type": meta.importer_type,
                "parse_error": meta.parse_error,
                "texture_type": meta.texture_type,
                "mipmap_enabled": meta.mipmap_enabled,
                "read_write_enabled": meta.read_write_enabled,
                "max_texture_size": meta.max_texture_size,
                "load_type": meta.load_type,
                "compression_format": meta.compression_format,
                "force_to_mono": meta.force_to_mono,
                "extra": meta.extra,
            }

        # Gather extracted properties
        extracted = audit_result.extracted_map.get(issue.asset_path)
        extracted_data = None
        if extracted is not None:
            if hasattr(extracted, '__dict__'):
                extracted_data = {
                    k: v for k, v in extracted.__dict__.items()
                    if not k.startswith('_')
                }
            elif isinstance(extracted, dict):
                extracted_data = extracted
            else:
                extracted_data = {"value": str(extracted)}

        # Gather evidence
        evidence = audit_result.evidence_map.get(issue_id)
        evidence_data = None
        if evidence is not None:
            evidence_data = {
                "context_summary": evidence.context_summary,
                "risk_hint": evidence.risk_hint,
                "need_manual_confirm": evidence.need_manual_confirm,
                "code_search_result": evidence.code_search_result[:10] if evidence.code_search_result else [],
                "code_search_result_count": len(evidence.code_search_result) if evidence.code_search_result else 0,
            }

        # Gather fix decision
        decision = _decisions_by_id.get(issue_id)
        decision_data = None
        if decision is not None:
            decision_data = {
                "action": decision.action,
                "risk_level": decision.risk_level,
                "reason": decision.reason,
                "suggestion": decision.suggestion,
            }

        return ToolResult(ok=True, data={
            "issue_id": issue.issue_id,
            "rule_id": issue.rule_id,
            "severity": issue.severity,
            "asset_path": issue.asset_path,
            "title": issue.title,
            "message": issue.message,
            "suggestion": issue.suggestion,
            "auto_fixable": issue.auto_fixable,
            "evidence_from_rule": issue.evidence,
            "path_classification": _classify_asset_path(issue.asset_path),
            "meta": meta_data,
            "extracted_properties": extracted_data,
            "code_evidence": evidence_data,
            "deterministic_fix_decision": decision_data,
            "related_issues_on_same_asset": [
                {"issue_id": i.issue_id, "rule_id": i.rule_id, "severity": i.severity}
                for i in _issues_by_asset.get(issue.asset_path, [])
                if i.issue_id != issue_id
            ],
        })

    # ── get_asset_info ──────────────────────────────────────────────────

    def _get_asset_info(asset_path: str) -> ToolResult:
        """Get detailed info about an asset (meta + extracted properties)."""
        # Find the asset
        asset = None
        for a in audit_result.assets:
            if a.asset_path == asset_path:
                asset = a
                break

        if asset is None:
            # Try fuzzy match
            candidates = [a.asset_path for a in audit_result.assets
                          if asset_path.lower() in a.asset_path.lower()]
            return ToolResult(
                ok=False,
                error_code="ASSET_NOT_FOUND",
                message=f"Asset '{asset_path}' not found. Similar: {candidates[:5]}",
            )

        # Gather meta
        meta = audit_result.meta_map.get(asset_path)
        meta_data = None
        if meta is not None:
            meta_data = {
                "guid": meta.guid,
                "importer_type": meta.importer_type,
                "parse_error": meta.parse_error,
                "texture_type": meta.texture_type,
                "mipmap_enabled": meta.mipmap_enabled,
                "read_write_enabled": meta.read_write_enabled,
                "max_texture_size": meta.max_texture_size,
                "load_type": meta.load_type,
                "compression_format": meta.compression_format,
                "force_to_mono": meta.force_to_mono,
                "extra": meta.extra,
            }

        # Gather extracted properties
        extracted = audit_result.extracted_map.get(asset_path)
        extracted_data = None
        if extracted is not None:
            if hasattr(extracted, '__dict__'):
                extracted_data = {
                    k: v for k, v in extracted.__dict__.items()
                    if not k.startswith('_')
                }
            elif isinstance(extracted, dict):
                extracted_data = extracted
            else:
                extracted_data = {"value": str(extracted)}

        # Gather issues on this asset
        asset_issues = _issues_by_asset.get(asset_path, [])
        issues_data = [
            {
                "issue_id": i.issue_id,
                "rule_id": i.rule_id,
                "severity": i.severity,
                "title": i.title,
            }
            for i in asset_issues
        ]

        return ToolResult(ok=True, data={
            "asset_path": asset.asset_path,
            "asset_type": asset.asset_type,
            "extension": asset.extension,
            "file_size": asset.file_size,
            "file_size_mb": round(asset.file_size / (1024 * 1024), 3) if asset.file_size else 0,
            "meta_path": asset.meta_path,
            "path_classification": _classify_asset_path(asset_path),
            "meta": meta_data,
            "extracted_properties": extracted_data,
            "issues_on_asset": issues_data,
            "issue_count": len(issues_data),
        })

    # ── search_asset_code ───────────────────────────────────────────────

    def _search_asset_code(asset_path: str, search_type: str = "all") -> ToolResult:
        """Search C# code for references to an asset."""
        # Collect evidence from all issues related to this asset
        asset_issues = _issues_by_asset.get(asset_path, [])
        all_matches = []
        seen_keys = set()

        for issue in asset_issues:
            evidence = audit_result.evidence_map.get(issue.issue_id)
            if evidence and evidence.code_search_result:
                for match in evidence.code_search_result:
                    key = (match.get("file", ""), match.get("line", 0), match.get("api", ""))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        item = {
                            "file": match.get("file", ""),
                            "line": match.get("line", 0),
                            "match": match.get("match", ""),
                            "api": match.get("api", ""),
                            "association_level": match.get("association_level", "none"),
                            "context": match.get("context", ""),
                        }
                        if search_type == "api_usage" and item["api"] == "none":
                            continue
                        all_matches.append(item)

        # Also check for direct GUID references in .meta files
        asset_meta = audit_result.meta_map.get(asset_path)
        guid = asset_meta.guid if asset_meta else None

        return ToolResult(ok=True, data={
            "asset_path": asset_path,
            "guid": guid,
            "search_type": search_type,
            "match_count": len(all_matches),
            "matches": all_matches[:20],  # Limit to 20
            "association_summary": {
                "direct": sum(1 for m in all_matches if m.get("association_level") == "direct"),
                "possible": sum(1 for m in all_matches if m.get("association_level") == "possible"),
                "none": sum(1 for m in all_matches if m.get("association_level") == "none"),
            },
        })

    # ── read_code_context ───────────────────────────────────────────────

    def _detect_method_boundaries(lines: list[str], target_line: int) -> tuple[int | None, int | None]:
        """Detect the enclosing method/class boundaries around target_line.

        Scans backward for a method/class signature pattern and forward
        for the matching closing brace. Returns (start_line, end_line)
        as 1-indexed line numbers, or None if boundaries cannot be detected.
        """
        # Common C# method/class signature patterns

        # Search backward for method/class start
        method_start = None
        brace_depth = 0
        for i in range(target_line - 1, -1, -1):
            line = lines[i].strip()
            # Track brace depth (counting opens as +1, closes as -1)
            brace_depth += line.count('}') - line.count('{')
            if brace_depth < 0:
                # Found the opening of our scope
                method_start = i + 1
                break

        # Search forward for matching close
        method_end = None
        brace_depth = 0
        for i in range(target_line - 1, len(lines)):
            line = lines[i].strip()
            # Remove string/comment content before counting
            brace_depth += line.count('{') - line.count('}')
            if brace_depth == 0 and i >= target_line - 1:
                method_end = i + 1
                break

        return method_start, method_end

    def _read_code_context(file_path: str, line_number: int,
                           context_lines: int = 20) -> ToolResult:
        """Read actual C# source code context around a specific line.

        Unlike search_asset_code which returns single-line matches with metadata,
        this tool reads the real source file and returns surrounding code so the
        LLM can understand:
        - Is the API usage in a test file or editor-only code?
        - Is it behind a conditional (#if UNITY_EDITOR)?
        - What method/class surrounds the API call?
        - Is the asset actually used or just referenced in a comment?

        Path validation: the file_path must be within the project root.
        Only .cs files are supported.
        """
        import re

        # Validate path
        valid, resolved = validate_path_in_project(file_path, project_root)
        if not valid:
            return ToolResult(
                ok=False,
                error_code="PATH_ERROR",
                message=resolved,
                retryable=False,
            )

        # Only allow C# files
        if not resolved.endswith('.cs'):
            return ToolResult(
                ok=False,
                error_code="NOT_CS_FILE",
                message=f"Only .cs files are supported: {file_path}",
                retryable=False,
            )

        # Check file exists
        if not os.path.exists(resolved):
            return ToolResult(
                ok=False,
                error_code="FILE_NOT_FOUND",
                message=f"File not found: {file_path} (resolved: {resolved})",
                retryable=False,
            )

        # Read the file
        try:
            with open(resolved, encoding='utf-8', errors='replace') as f:
                all_lines = f.readlines()
        except OSError as e:
            return ToolResult(
                ok=False,
                error_code="READ_ERROR",
                message=f"Cannot read file {file_path}: {e}",
                retryable=False,
            )

        total_lines = len(all_lines)

        # Validate line_number
        if line_number < 1 or line_number > total_lines:
            return ToolResult(
                ok=False,
                error_code="INVALID_LINE",
                message=f"Line number {line_number} is out of range "
                        f"(file has {total_lines} lines): {file_path}",
                retryable=False,
            )

        # Clamp context_lines
        context_lines = min(max(context_lines, 5), 50)

        # Compute display range
        start = max(0, line_number - 1 - context_lines)
        end = min(total_lines, line_number + context_lines)

        # Detect method boundaries
        method_start, method_end = _detect_method_boundaries(all_lines, line_number)

        # Check for preprocessor directives around target
        pp_directives = []
        pp_pattern = re.compile(r'^\s*#\s*(if|elif|else|endif|region|endregion|define|undef|pragma)\b')
        for i in range(start, min(end + 5, total_lines)):
            m = pp_pattern.match(all_lines[i])
            if m:
                pp_directives.append({
                    "line": i + 1,
                    "directive": m.group(1),
                    "content": all_lines[i].strip(),
                })

        # Check for comment-only: if the target line is a comment
        target_content = all_lines[line_number - 1].strip()
        is_comment = target_content.startswith('//') or target_content.startswith('/*')

        # Build line-by-line output
        code_lines = []
        for i in range(start, end):
            code_lines.append({
                "line_num": i + 1,
                "is_target": (i + 1) == line_number,
                "content": all_lines[i].rstrip('\n\r'),
            })

        return ToolResult(ok=True, data={
            "file_path": file_path,
            "resolved_path": resolved,
            "target_line": line_number,
            "target_is_comment": is_comment,
            "context_start": start + 1,
            "context_end": end,
            "total_lines": total_lines,
            "method_start": method_start,
            "method_end": method_end,
            "preprocessor_directives": pp_directives,
            "code_lines": code_lines,
        })

    # ── trace_prefab_references ───────────────────────────────────────

    def _trace_prefab_references(prefab_path: str) -> ToolResult:
        """Trace asset references within a prefab or scene.

        Parses the .prefab/.unity YAML to extract the chain:
        GameObject → Component → Material/Sprite → Texture

        This reveals which textures are actually used by which prefabs,
        helping assess Read/Write and other risks with real usage context.
        """
        from unity_audit.harness.prefab_tools import (
            resolve_references,
            trace_prefab_references,
        )

        # Asset paths are relative to Assets/. Try both with and without prefix.
        search_paths = [prefab_path]
        if not prefab_path.startswith('Assets/') and not prefab_path.startswith('Assets\\'):
            search_paths.append('Assets/' + prefab_path)

        resolved = None
        path_error = None
        for sp in search_paths:
            valid, rp = validate_path_in_project(sp, project_root)
            if not valid:
                path_error = rp  # Save the error message
                continue
            if os.path.exists(rp):
                resolved = rp
                break

        if resolved is None:
            if path_error:
                return ToolResult(
                    ok=False, error_code="PATH_ERROR", message=path_error,
                    retryable=False,
                )
            return ToolResult(
                ok=False, error_code="FILE_NOT_FOUND",
                message=f"Prefab/scene not found: {prefab_path} (tried: {search_paths})",
                retryable=False,
            )

        # Only accept .prefab and .unity files
        if not (resolved.endswith('.prefab') or resolved.endswith('.unity')):
            return ToolResult(
                ok=False, error_code="NOT_PREFAB_OR_SCENE",
                message=f"Only .prefab and .unity files are supported: {prefab_path}",
                retryable=False,
            )

        if not os.path.exists(resolved):
            return ToolResult(
                ok=False, error_code="FILE_NOT_FOUND",
                message=f"File not found: {prefab_path}",
                retryable=False,
            )

        # Trace references
        trace_result = trace_prefab_references(resolved, prefab_path)

        if trace_result.parse_error:
            return ToolResult(
                ok=False, error_code="PARSE_ERROR",
                message=trace_result.parse_error, retryable=False,
            )

        # Resolve GUIDs to asset paths
        resolved_refs = resolve_references(trace_result, _guid_to_asset)

        # Build material/texture/sprite summary
        from collections import Counter
        ref_types = Counter(r["reference_type"] for r in resolved_refs)
        resolved_count = sum(1 for r in resolved_refs if r["resolved"])
        unresolved_count = sum(1 for r in resolved_refs if not r["resolved"])

        # Group by target asset
        by_asset: dict[str, list[dict]] = {}
        for r in resolved_refs:
            by_asset.setdefault(r["target_asset"], []).append(r)

        asset_summary = []
        for target, refs in sorted(by_asset.items(), key=lambda x: -len(x[1])):
            sources = list(set(r["source_object"] for r in refs))
            asset_summary.append({
                "target_asset": target,
                "reference_count": len(refs),
                "reference_types": list(set(r["reference_type"] for r in refs)),
                "used_by_objects": sources[:10],  # Limit
                "resolved": refs[0]["resolved"],
            })

        return ToolResult(ok=True, data={
            "prefab_path": prefab_path,
            "prefab_type": trace_result.prefab_type,
            "game_object_count": trace_result.game_object_count,
            "component_summary": trace_result.component_summary,
            "total_references": len(resolved_refs),
            "resolved_references": resolved_count,
            "unresolved_references": unresolved_count,
            "reference_type_summary": dict(ref_types),
            "referenced_assets": asset_summary,
            # Include raw references for deep inspection
            "raw_references": [
                {
                    "source_object": r.source_object,
                    "source_component": r.source_component,
                    "reference_type": r.reference_type,
                    "target_guid": r.target_guid,
                }
                for r in trace_result.references[:30]  # Limit
            ],
        })

    # ── list_project_assets ─────────────────────────────────────────────

    def _list_project_assets(asset_type: str | None = None) -> ToolResult:
        """List all scanned assets with summary stats."""
        assets = audit_result.assets
        if asset_type:
            assets = [a for a in assets if a.asset_type == asset_type]

        asset_list = []
        for a in assets:
            asset_issues = _issues_by_asset.get(a.asset_path, [])
            critical_high = sum(
                1 for i in asset_issues if i.severity in ("critical", "high")
            )
            asset_list.append({
                "asset_path": a.asset_path,
                "asset_type": a.asset_type,
                "extension": a.extension,
                "file_size_mb": round(a.file_size / (1024 * 1024), 3) if a.file_size else 0,
                "issue_count": len(asset_issues),
                "critical_high_count": critical_high,
            })

        # Summary by type
        from collections import Counter
        type_counts = Counter(a.asset_type for a in assets)
        severity_counts = Counter(i.severity for i in audit_result.issues)

        return ToolResult(ok=True, data={
            "total_assets": len(assets),
            "filtered_by_type": asset_type,
            "assets": asset_list,
            "summary_by_type": dict(type_counts),
            "issue_severity_summary": dict(severity_counts),
            "total_issues": len(audit_result.issues),
            "project_root": project_root,
        })

    # ── submit_assessment ───────────────────────────────────────────────

    # Shared state so the harness can retrieve the assessment
    _submitted_assessment = {}

    def _submit_assessment(
        issue_id: str,
        risk_level: str,
        recommended_action: str,
        confidence: float,
        summary: str,
        needs_human_review: bool = False,
        evidence_refs: list[str] | None = None,
    ) -> ToolResult:
        """Submit the final assessment for an issue. Call this when done analyzing."""
        if evidence_refs is None:
            evidence_refs = []
        _submitted_assessment["assessment"] = {
            "issue_id": issue_id,
            "risk_level": risk_level,
            "recommended_action": recommended_action,
            "confidence": confidence,
            "summary": summary,
            "needs_human_review": needs_human_review,
            "evidence_refs": evidence_refs,
        }
        return ToolResult(
            ok=True,
            data={
                "submitted": True,
                "issue_id": issue_id,
                "message": "Assessment submitted successfully. The harness will now process the next issue.",
            },
        )

    # ── Build ToolDef list ──────────────────────────────────────────────

    return [
        ToolDef(
            name="get_issue_detail",
            description=(
                "Get detailed information about a specific audit issue, including "
                "extracted asset properties, parsed .meta fields, code evidence with "
                "association levels, and the deterministic fix decision. "
                "Use this first when analyzing an issue to understand its full context."
            ),
            func=_get_issue_detail,
            parameters=GET_ISSUE_DETAIL_PARAMS,
            is_readonly=True,
        ),
        ToolDef(
            name="get_asset_info",
            description=(
                "Get detailed parsed and extracted information about a specific asset, "
                "including .meta file fields (GUID, importer settings), extracted "
                "properties (dimensions, format, duration, etc.), and all issues found "
                "on this asset. Use this to understand an asset's configuration."
            ),
            func=_get_asset_info,
            parameters=GET_ASSET_INFO_PARAMS,
            is_readonly=True,
        ),
        ToolDef(
            name="search_asset_code",
            description=(
                "Search the project's C# code for references to a specific asset. "
                "Returns code locations with association levels (direct/possible/none) "
                "indicating whether the code directly references the asset and uses "
                "relevant Unity APIs (e.g., Texture2D.GetPixels, AudioSource). "
                "Use this to assess Read/Write or Decompress-on-Load risks."
            ),
            func=_search_asset_code,
            parameters=SEARCH_ASSET_CODE_PARAMS,
            is_readonly=True,
        ),
        ToolDef(
            name="read_code_context",
            description=(
                "Read actual C# source code around a specific line from a code evidence match. "
                "Shows surrounding code context (±context_lines around the target line) so you can "
                "determine: Is the API call in test/editor-only code? Is it behind #if UNITY_EDITOR? "
                "What method/class surrounds it? Is this asset name just in a comment? "
                "Use this AFTER search_asset_code to deep-read promising code matches before "
                "making your final risk assessment."
            ),
            func=_read_code_context,
            parameters=READ_CODE_CONTEXT_PARAMS,
            is_readonly=True,
        ),
        ToolDef(
            name="trace_prefab_references",
            description=(
                "Parse a .prefab or .unity file to trace the GameObject → Component → "
                "Material/Sprite → Texture reference chain. Returns which GameObjects "
                "reference which assets by GUID, cross-referenced with the project's asset "
                "database. Use this to determine if a texture with Read/Write enabled is "
                "actually used by a prefab that calls GetPixels — or if it's just a static "
                "decoration. Also reveals missing scripts and component structure."
            ),
            func=_trace_prefab_references,
            parameters=TRACE_PREFAB_REFERENCES_PARAMS,
            is_readonly=True,
        ),
        ToolDef(
            name="list_project_assets",
            description=(
                "List all scanned assets in the project with summary statistics. "
                "Optionally filter by asset type (Texture, Audio, Prefab, Scene). "
                "Returns per-asset issue counts, severity distribution, and overall "
                "project stats. Use this to get an overview of the project state."
            ),
            func=_list_project_assets,
            parameters=LIST_PROJECT_ASSETS_PARAMS,
            is_readonly=True,
        ),
        ToolDef(
            name="submit_assessment",
            description=(
                "Submit your final assessment for the current issue. "
                "You MUST call this tool when you have finished analyzing the issue. "
                "Provide risk_level (low/medium/high), recommended_action "
                "(auto_fix_candidate/manual_confirm_required/do_not_fix), "
                "confidence (0.0-1.0), and a brief summary explaining your reasoning. "
                "Call this tool as soon as you have enough information — do not keep "
                "calling other tools after you have made your assessment."
            ),
            func=_submit_assessment,
            parameters=SUBMIT_ASSESSMENT_PARAMS,
            is_readonly=True,
        ),
    ]
