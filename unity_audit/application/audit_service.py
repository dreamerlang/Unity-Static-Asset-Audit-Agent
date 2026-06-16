"""AuditService - Encapsulates the full deterministic audit pipeline.

Extracted from CLI so it can be reused by the Harness, Agent mode, or tests.
"""

import time

from unity_audit.application.models import AuditRequest, AuditResult
from unity_audit.evidence import build_evidence_for_issues
from unity_audit.extractors.audio import extract_audio_info
from unity_audit.extractors.prefab_scene import extract_prefab_scene_info
from unity_audit.extractors.shader_material import (
    MaterialInfo,
    ShaderInfo,
    extract_material_info,
    extract_shader_info,
)
from unity_audit.extractors.texture import extract_texture_info
from unity_audit.fix_planner import adjust_severity_for_platform, plan_fixes
from unity_audit.meta_parser import MetaInfo, parse_meta
from unity_audit.rules.audio_rules import (
    rule_aud_long_audio_decompress_on_load,
    rule_aud_stereo_sfx,
)
from unity_audit.rules.engine import RuleEngine
from unity_audit.rules.prefab_rules import (
    rule_prefab_missing_script,
    rule_ui_too_many_graphic_raycasters,
)
from unity_audit.rules.shader_material_rules import (
    rule_mat_empty_shader,
    rule_mat_missing_texture,
    rule_shader_excessive_variants,
    rule_shader_geometry_tessellation,
)
from unity_audit.rules.texture_rules import (
    rule_tex_npot_detected,
    rule_tex_read_write_enabled,
    rule_tex_ui_max_size_too_large,
    rule_tex_ui_mipmap_enabled,
)
from unity_audit.scanner import AssetInfo, scan_project


def _build_default_engine(config: dict | None = None) -> RuleEngine:
    """Build a RuleEngine with all standard rules registered.

    Args:
        config: Optional config dict with per-rule settings (enabled, thresholds).

    Returns:
        Configured RuleEngine.
    """
    engine = RuleEngine()
    rules_config = config.get("rules", {}) if config else {}

    # Texture rules
    if rules_config.get("TEX_UI_MIPMAP_ENABLED", {}).get("enabled", True):
        engine.register("TEX_UI_MIPMAP_ENABLED", "Texture", rule_tex_ui_mipmap_enabled)
    if rules_config.get("TEX_READ_WRITE_ENABLED", {}).get("enabled", True):
        engine.register("TEX_READ_WRITE_ENABLED", "Texture", rule_tex_read_write_enabled)
    if rules_config.get("TEX_UI_MAX_SIZE_TOO_LARGE", {}).get("enabled", True):
        engine.register("TEX_UI_MAX_SIZE_TOO_LARGE", "Texture", rule_tex_ui_max_size_too_large)
    if rules_config.get("TEX_NPOT_DETECTED", {}).get("enabled", True):
        engine.register("TEX_NPOT_DETECTED", "Texture", rule_tex_npot_detected)

    # Audio rules
    if rules_config.get("AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD", {}).get("enabled", True):
        engine.register("AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD", "Audio", rule_aud_long_audio_decompress_on_load)
    if rules_config.get("AUD_STEREO_SFX", {}).get("enabled", True):
        engine.register("AUD_STEREO_SFX", "Audio", rule_aud_stereo_sfx)

    # Prefab/Scene rules
    if rules_config.get("PREFAB_MISSING_SCRIPT", {}).get("enabled", True):
        engine.register("PREFAB_MISSING_SCRIPT", "Prefab", rule_prefab_missing_script)
        engine.register("PREFAB_MISSING_SCRIPT", "Scene", rule_prefab_missing_script)
    if rules_config.get("UI_TOO_MANY_GRAPHIC_RAYCASTERS", {}).get("enabled", True):
        engine.register("UI_TOO_MANY_GRAPHIC_RAYCASTERS", "Prefab", rule_ui_too_many_graphic_raycasters)
        engine.register("UI_TOO_MANY_GRAPHIC_RAYCASTERS", "Scene", rule_ui_too_many_graphic_raycasters)

    # Shader rules
    if rules_config.get("SHADER_EXCESSIVE_VARIANTS", {}).get("enabled", True):
        engine.register("SHADER_EXCESSIVE_VARIANTS", "Shader", rule_shader_excessive_variants)
    if rules_config.get("SHADER_HAS_GEOMETRY_TESSELLATION", {}).get("enabled", True):
        engine.register("SHADER_HAS_GEOMETRY_TESSELLATION", "Shader", rule_shader_geometry_tessellation)

    # Material rules
    if rules_config.get("MAT_MISSING_TEXTURE", {}).get("enabled", True):
        engine.register("MAT_MISSING_TEXTURE", "Material", rule_mat_missing_texture)
    if rules_config.get("MAT_EMPTY_SHADER", {}).get("enabled", True):
        engine.register("MAT_EMPTY_SHADER", "Material", rule_mat_empty_shader)

    return engine


def _extract_asset(
    asset: AssetInfo,
    meta_map: dict[str, MetaInfo],
) -> object | None:
    """Run the appropriate extractor for an asset type."""
    meta = meta_map.get(asset.asset_path)
    if meta is None:
        meta = MetaInfo(parse_error="Meta not loaded")

    try:
        if asset.asset_type == "Texture":
            return extract_texture_info(asset.asset_path, asset.absolute_path, meta)
        elif asset.asset_type == "Audio":
            return extract_audio_info(asset.asset_path, asset.absolute_path, meta)
        elif asset.asset_type in ("Prefab", "Scene"):
            return extract_prefab_scene_info(asset.asset_path, asset.absolute_path)
        elif asset.asset_type == "Shader":
            return _extract_shader(asset)
        elif asset.asset_type == "Material":
            return _extract_material(asset)
    except Exception:
        pass
    return None


def _extract_shader(asset: AssetInfo) -> ShaderInfo | None:
    """Extract shader info from a .shader file."""
    try:
        with open(asset.absolute_path, encoding="utf-8", errors="replace") as f:
            source = f.read()
        return extract_shader_info(asset.asset_path, source)
    except (OSError, UnicodeDecodeError):
        return ShaderInfo(
            asset_path=asset.asset_path,
            variant_keywords=[],
            variant_count=0,
            pass_count=0,
            parse_error="Could not read shader file",
        )


def _extract_material(asset: AssetInfo) -> MaterialInfo | None:
    """Extract material info from a .mat file."""
    try:
        with open(asset.absolute_path, encoding="utf-8", errors="replace") as f:
            source = f.read()
        return extract_material_info(asset.asset_path, source)
    except (OSError, UnicodeDecodeError):
        return MaterialInfo(
            asset_path=asset.asset_path,
            parse_error="Could not read material file",
        )


class AuditService:
    """Encapsulates the deterministic asset audit pipeline.

    Usage:
        service = AuditService()
        result = service.run_scan(AuditRequest(
            project_root="/path/to/unity/project",
            platform="Android",
        ))
    """

    def run_scan(self, request: AuditRequest) -> AuditResult:
        """Execute the full deterministic audit pipeline.

        Args:
            request: AuditRequest with project_root, platform, and optional config.

        Returns:
            AuditResult containing all scan outputs.

        Raises:
            FileNotFoundError: If the project root or Assets/ dir doesn't exist.
        """
        result = AuditResult(
            project_root=request.project_root,
            platform=request.platform,
            started_at=time.time(),
        )

        # Phase 1: Scan
        scan_result = scan_project(
            request.project_root,
            path_filter=request.path_filter,
        )
        result.assets = scan_result.assets
        result.warnings.extend(scan_result.warnings)
        result.errors.extend(scan_result.scan_errors)

        if result.errors:
            result.finished_at = time.time()
            return result

        if not result.assets:
            result.finished_at = time.time()
            return result

        # Phase 2: Parse .meta files
        meta_map: dict[str, MetaInfo] = {}
        for asset in result.assets:
            meta = parse_meta(asset.meta_path)
            meta_map[asset.asset_path] = meta
            if meta.parse_error:
                result.warnings.append(f"Meta parse warning for {asset.asset_path}: {meta.parse_error}")
        result.meta_map = meta_map

        # Phase 3: Extract asset info
        extracted_map: dict[str, object] = {}
        for asset in result.assets:
            extracted = _extract_asset(asset, meta_map)
            if extracted is not None:
                extracted_map[asset.asset_path] = extracted
            else:
                result.warnings.append(f"Extraction failed for {asset.asset_path}")
        result.extracted_map = extracted_map

        # Phase 4: Run rules
        config = request.config
        engine = _build_default_engine(config)
        result.issues = engine.evaluate_all(result.assets, meta_map, extracted_map)

        # Phase 4.5: Adjust severity for target platform
        platform = request.platform
        for issue in result.issues:
            original = issue.severity
            adjusted = adjust_severity_for_platform(original, issue.rule_id, platform)
            if adjusted != original:
                issue.severity = adjusted

        # Phase 5: Build evidence
        meta_guid_map = {path: meta.guid for path, meta in meta_map.items()}
        result.evidence_map = build_evidence_for_issues(
            request.project_root, result.issues, meta_guid_map
        )

        # Phase 6: Fix planning
        result.fix_decisions = plan_fixes(result.issues, result.evidence_map)

        result.finished_at = time.time()
        return result
