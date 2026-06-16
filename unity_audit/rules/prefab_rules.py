"""Prefab/Scene rules for the Rule Engine."""


from unity_audit.extractors.prefab_scene import PrefabSceneInfo
from unity_audit.meta_parser import MetaInfo
from unity_audit.rules.engine import Issue
from unity_audit.scanner import AssetInfo


def rule_prefab_missing_script(asset: AssetInfo, meta: MetaInfo, extracted: object) -> Issue | None:
    """PREFAB_MISSING_SCRIPT: Check for Missing Script references."""
    if not isinstance(extracted, PrefabSceneInfo):
        return None
    info: PrefabSceneInfo = extracted

    if info.missing_script_count <= 0:
        return None

    return Issue(
        issue_id="",
        rule_id="PREFAB_MISSING_SCRIPT",
        severity="critical",
        asset_path=asset.asset_path,
        title=f"Missing Script detected ({info.missing_script_count} occurrence(s))",
        message=(
            f"Prefab/Scene '{asset.asset_path}' contains {info.missing_script_count} "
            f"missing script reference(s). This can cause runtime errors or missing functionality."
        ),
        evidence={
            "missing_script_count": info.missing_script_count,
        },
        suggestion="需要人工修复 Missing Script，否则可能导致运行时逻辑缺失或报错。",
        auto_fixable=False,
    )


def rule_ui_too_many_graphic_raycasters(asset: AssetInfo, meta: MetaInfo, extracted: object) -> Issue | None:
    """UI_TOO_MANY_GRAPHIC_RAYCASTERS: Check for excessive GraphicRaycaster components."""
    if not isinstance(extracted, PrefabSceneInfo):
        return None
    info: PrefabSceneInfo = extracted

    if info.graphic_raycaster_count <= 1:
        return None

    return Issue(
        issue_id="",
        rule_id="UI_TOO_MANY_GRAPHIC_RAYCASTERS",
        severity="medium",
        asset_path=asset.asset_path,
        title=f"Multiple GraphicRaycasters detected ({info.graphic_raycaster_count})",
        message=(
            f"Prefab/Scene '{asset.asset_path}' contains {info.graphic_raycaster_count} "
            f"GraphicRaycaster components. Multiple raycasters in the same UI hierarchy "
            f"can increase UI raycast overhead."
        ),
        evidence={
            "graphic_raycaster_count": info.graphic_raycaster_count,
        },
        suggestion="同一 UI 层级中过多 GraphicRaycaster 可能增加 UI 射线检测开销，建议检查是否可以合并或移除。",
        auto_fixable=False,
    )
