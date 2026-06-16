"""Shader and Material rules for the Rule Engine."""

from unity_audit.extractors.shader_material import MaterialInfo, ShaderInfo
from unity_audit.meta_parser import MetaInfo
from unity_audit.rules.engine import Issue
from unity_audit.scanner import AssetInfo

# ── Shader Rules ──────────────────────────────────────────────────────────


def rule_shader_excessive_variants(
    asset: AssetInfo, meta: MetaInfo, extracted: object,
) -> Issue | None:
    """SHADER_EXCESSIVE_VARIANTS: Shader has too many multi_compile variants.

    Each boolean keyword doubles the variant count. 64+ variants can cause
    significant build time increases and runtime shader memory overhead.
    """
    if not isinstance(extracted, ShaderInfo):
        return None
    info: ShaderInfo = extracted

    if info.variant_count < 64:
        return None

    severity = "high" if info.variant_count >= 256 else "medium"

    return Issue(
        issue_id="",
        rule_id="SHADER_EXCESSIVE_VARIANTS",
        severity=severity,
        asset_path=asset.asset_path,
        title=(
            f"Shader has ~{info.variant_count} variants "
            f"({len(info.variant_keywords)} keywords)"
        ),
        message=(
            f"Shader '{asset.asset_path}' generates approximately "
            f"{info.variant_count} variants from "
            f"{len(info.variant_keywords)} multi_compile/shadder_feature "
            f"keywords: {', '.join(info.variant_keywords[:10])}"
            f"{'...' if len(info.variant_keywords) > 10 else ''}. "
            f"High variant counts increase build time and shader memory."
        ),
        evidence={
            "variant_count": info.variant_count,
            "keyword_count": len(info.variant_keywords),
            "keywords": info.variant_keywords[:20],
            "pass_count": info.pass_count,
        },
        suggestion="考虑减少 multi_compile 关键字，或将不常用的变体改为 shader_feature 以按需编译。",
        auto_fixable=False,
    )


def rule_shader_geometry_tessellation(
    asset: AssetInfo, meta: MetaInfo, extracted: object,
) -> Issue | None:
    """SHADER_HAS_GEOMETRY_TESSELLATION: Flag when shader uses geometry/tessellation.

    Geometry and tessellation shaders are not supported on all platforms
    (e.g., geometry shaders are unavailable on Metal/Mac, tessellation on
    some mobile GPUs). This rule flags them for manual review.
    """
    if not isinstance(extracted, ShaderInfo):
        return None
    info: ShaderInfo = extracted

    if not (info.has_geometry or info.has_tessellation):
        return None

    features = []
    if info.has_geometry:
        features.append("geometry")
    if info.has_tessellation:
        features.append("tessellation")

    return Issue(
        issue_id="",
        rule_id="SHADER_HAS_GEOMETRY_TESSELLATION",
        severity="medium",
        asset_path=asset.asset_path,
        title=f"Shader uses {'/'.join(features)} shader stage(s)",
        message=(
            f"Shader '{asset.asset_path}' uses {' and '.join(features)} "
            f"shader stages, which may not be supported on all target "
            f"platforms (e.g., geometry shaders on Metal)."
        ),
        evidence={
            "has_geometry": info.has_geometry,
            "has_tessellation": info.has_tessellation,
        },
        suggestion="确认目标平台支持该着色器阶段，或为不支持的平台提供 fallback。",
        auto_fixable=False,
    )


# ── Material Rules ────────────────────────────────────────────────────────


def rule_mat_missing_texture(
    asset: AssetInfo, meta: MetaInfo, extracted: object,
) -> Issue | None:
    """MAT_MISSING_TEXTURE: Material references a missing/null texture GUID."""
    if not isinstance(extracted, MaterialInfo):
        return None
    info: MaterialInfo = extracted

    if not info.missing_textures:
        return None

    tex_list = ", ".join(info.missing_textures[:5])
    if len(info.missing_textures) > 5:
        tex_list += f" ... and {len(info.missing_textures) - 5} more"

    return Issue(
        issue_id="",
        rule_id="MAT_MISSING_TEXTURE",
        severity="high",
        asset_path=asset.asset_path,
        title=(
            f"Material has {len(info.missing_textures)} missing texture "
            f"reference(s)"
        ),
        message=(
            f"Material '{asset.asset_path}' references "
            f"{len(info.missing_textures)} texture(s) with null GUIDs: "
            f"{tex_list}. These textures will appear as missing (pink) "
            f"at runtime."
        ),
        evidence={
            "missing_texture_count": len(info.missing_textures),
            "missing_textures": info.missing_textures,
            "shader_name": info.shader_name,
        },
        suggestion=(
            "检查材质引用的贴图是否存在，如果不需要请从材质中移除该贴图引用。"
        ),
        auto_fixable=False,
    )


def rule_mat_empty_shader(
    asset: AssetInfo, meta: MetaInfo, extracted: object,
) -> Issue | None:
    """MAT_EMPTY_SHADER: Material has unknown or missing shader reference."""
    if not isinstance(extracted, MaterialInfo):
        return None
    info: MaterialInfo = extracted

    if info.shader_name and info.shader_name != "unknown":
        return None

    return Issue(
        issue_id="",
        rule_id="MAT_EMPTY_SHADER",
        severity="high",
        asset_path=asset.asset_path,
        title="Material has no valid shader reference",
        message=(
            f"Material '{asset.asset_path}' does not reference a "
            f"valid shader. The material will render as the default "
            f"error shader (pink) at runtime."
        ),
        evidence={"shader_name": info.shader_name},
        suggestion="为材质指定一个有效的 Shader，或删除不再使用的材质文件。",
        auto_fixable=False,
    )
