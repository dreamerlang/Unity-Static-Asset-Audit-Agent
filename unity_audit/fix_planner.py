"""Fix Planner - Generate fix decisions based on issues and evidence.

Determines whether each issue is:
- auto_fix_candidate: Safe to fix automatically
- manual_confirm_required: Needs human review
- do_not_fix: Fixing would likely cause problems

Decision table for Read/Write:
  direct evidence   -> do_not_fix (risk: high)
  possible evidence -> manual_confirm_required (risk: medium)
  none evidence     -> manual_confirm_required (risk: medium)

do_not_fix is ONLY allowed when there is direct evidence that the asset
is actively being used with pixel read/write APIs.
"""

from dataclasses import dataclass

from unity_audit.evidence import EvidenceResult
from unity_audit.rules.engine import Issue

# ═══════════════════════════════════════════════════════════════════════════════
# Platform-differentiated severity
# ═══════════════════════════════════════════════════════════════════════════════

# Severity rank for comparison
_SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_RANK_TO_SEVERITY = {v: k for k, v in _SEVERITY_RANK.items()}

# Platform-specific severity adjustments
# Each entry: (rule_id_substring, platform_match, rank_delta)
_PLATFORM_SEVERITY_RULES = [
    # NPOT textures: worse on platforms with strict POT requirements
    ("TEX_NPOT_DETECTED", "iOS", +1),
    ("TEX_NPOT_DETECTED", "tvOS", +1),
    ("TEX_NPOT_DETECTED", "WebGL", +1),
    # Read/Write: less critical on PC/Console (more memory available)
    ("TEX_READ_WRITE_ENABLED", "Standalone", -1),
    # Mipmaps: more important on mobile (memory constrained)
    ("TEX_UI_MIPMAP_ENABLED", "iOS", +1),
    ("TEX_UI_MIPMAP_ENABLED", "Android", +1),
    # Long audio: more critical on mobile
    ("AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD", "iOS", +1),
    ("AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD", "Android", +1),
    # Shader variants: critical on mobile (limited shader memory)
    ("SHADER_EXCESSIVE_VARIANTS", "iOS", +1),
    ("SHADER_EXCESSIVE_VARIANTS", "Android", +1),
    ("SHADER_EXCESSIVE_VARIANTS", "WebGL", +1),
]


def adjust_severity_for_platform(severity: str, rule_id: str, platform: str) -> str:
    """Adjust issue severity based on target platform.

    Mobile platforms (iOS, Android) and WebGL have stricter constraints
    for NPOT textures, memory, and shader variants. PC/Console platforms
    may be more lenient.

    Args:
        severity: Original severity (low/medium/high/critical).
        rule_id: The rule that produced the issue.
        platform: Target platform name (e.g., "Android", "iOS").

    Returns:
        Adjusted severity string, clamped to [low, critical].
    """
    rank = _SEVERITY_RANK.get(severity, 1)
    platform_lower = platform.lower()

    for rule_substring, plat_match, delta in _PLATFORM_SEVERITY_RULES:
        if rule_substring in rule_id and plat_match.lower() in platform_lower:
            rank += delta

    # Clamp
    rank = max(0, min(3, rank))
    return _RANK_TO_SEVERITY[rank]


@dataclass
class FixDecision:
    """Fix decision for a single issue."""
    issue_id: str
    rule_id: str
    asset_path: str
    severity: str
    action: str           # auto_fix_candidate, manual_confirm_required, do_not_fix
    risk_level: str       # low, medium, high
    reason: str = ""
    suggestion: str = ""


def plan_fixes(
    issues: list[Issue],
    evidence_map: dict[str, EvidenceResult],
) -> list[FixDecision]:
    """Generate fix decisions for all issues.

    Args:
        issues: List of all issues.
        evidence_map: Evidence map from Evidence Builder.

    Returns:
        List of FixDecision objects.
    """
    decisions = []
    for issue in issues:
        evidence = evidence_map.get(issue.issue_id)
        decision = _decide_for_issue(issue, evidence)
        decisions.append(decision)
    return decisions


def _decide_for_issue(issue: Issue, evidence: EvidenceResult | None) -> FixDecision:
    """Generate a fix decision for a single issue based on its rule and evidence."""
    decision = FixDecision(
        issue_id=issue.issue_id,
        rule_id=issue.rule_id,
        asset_path=issue.asset_path,
        severity=issue.severity,
        action="manual_confirm_required",  # Default: safe conservative
        risk_level="medium",
        suggestion=issue.suggestion,
    )

    if issue.rule_id == "TEX_UI_MIPMAP_ENABLED":
        path_lower = issue.asset_path.lower()
        special_paths = ["worldspaceui", "billboard", "minimap"]

        if any(p in path_lower for p in special_paths):
            decision.action = "manual_confirm_required"
            decision.risk_level = "medium"
            decision.reason = (
                "资源路径包含特殊 UI 关键字，关闭 Mipmap 前需要确认使用场景"
            )
        else:
            decision.action = "auto_fix_candidate"
            decision.risk_level = "low"
            decision.reason = (
                "普通 UI 贴图，关闭 Mipmap 通常安全，内存节省明确"
            )

    elif issue.rule_id == "TEX_READ_WRITE_ENABLED":
        if evidence is not None:
            level = evidence.association_level
            if level == "direct":
                decision.action = "do_not_fix"
                decision.risk_level = "high"
                direct_count = sum(
                    1 for e in evidence.code_evidence
                    if e.association_type == "direct"
                )
                decision.reason = (
                    f"代码中 {direct_count} 处像素读写 API 调用直接关联此资源，"
                    f"关闭 Read/Write 可能导致运行时错误"
                )
            elif level == "possible":
                decision.action = "manual_confirm_required"
                decision.risk_level = "medium"
                decision.reason = (
                    "项目存在像素读写 API 但未确认与此资源直接关联，需人工判断"
                )
            else:  # none
                decision.action = "manual_confirm_required"
                decision.risk_level = "medium"
                decision.reason = (
                    "未发现像素读写 API 与此资源相关，谨慎建议人工确认后关闭"
                )
        else:
            decision.action = "manual_confirm_required"
            decision.risk_level = "medium"
            decision.reason = (
                "无法获取证据，需人工确认运行时需求"
            )

    elif issue.rule_id == "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD":
        decision.action = "manual_confirm_required"
        decision.risk_level = "medium"
        decision.reason = (
            "长音频加载方式修改需了解播放场景：频繁重播建议 Compressed In Memory，"
            "背景音乐建议 Streaming"
        )

    elif issue.rule_id == "PREFAB_MISSING_SCRIPT":
        decision.action = "manual_confirm_required"
        decision.risk_level = "high"
        decision.reason = (
            "Missing Script 无法自动修复，需人工定位缺失的脚本组件"
        )

    elif issue.rule_id == "AUD_STEREO_SFX":
        decision.action = "manual_confirm_required"
        decision.risk_level = "low"
        decision.reason = (
            "是否需要立体声取决于设计需求，建议与音频设计师确认"
        )

    elif issue.rule_id == "TEX_UI_MAX_SIZE_TOO_LARGE":
        decision.action = "auto_fix_candidate"
        decision.risk_level = "low"
        decision.reason = (
            "降低 UI 贴图 Max Size 通常安全，建议根据实际需求调整"
        )

    elif issue.rule_id == "UI_TOO_MANY_GRAPHIC_RAYCASTERS":
        decision.action = "manual_confirm_required"
        decision.risk_level = "medium"
        decision.reason = (
            "多个 GraphicRaycaster 可能由不同 UI Canvas 层级需要，需人工判断"
        )

    elif issue.rule_id == "TEX_NPOT_DETECTED":
        decision.action = "manual_confirm_required"
        decision.risk_level = "low"
        decision.reason = (
            "NPOT 贴图在不同平台上表现不同，需结合目标平台判断"
        )

    return decision
