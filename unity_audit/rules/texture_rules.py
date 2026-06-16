"""Texture rules for the Rule Engine."""


from unity_audit.extractors.texture import TextureInfo
from unity_audit.meta_parser import MetaInfo
from unity_audit.rules.engine import Issue
from unity_audit.scanner import AssetInfo


def _is_ui_texture(asset: AssetInfo, info: TextureInfo) -> bool:
    """Check if a texture is a UI texture based on path or texture type."""
    # Path-based check
    normalized_path = asset.asset_path.replace("\\", "/").lower()
    if "/ui/" in normalized_path:
        return True
    # Texture type check: Sprite or Editor GUI
    if info.texture_type in ("Sprite", "Editor GUI"):
        return True
    return False


def rule_tex_ui_mipmap_enabled(asset: AssetInfo, meta: MetaInfo, extracted: object) -> Issue | None:
    """TEX_UI_MIPMAP_ENABLED: UI textures should not have mipmap enabled."""
    if not isinstance(extracted, TextureInfo):
        return None
    info: TextureInfo = extracted

    if not _is_ui_texture(asset, info):
        return None
    if not info.mipmap_enabled:
        return None

    return Issue(
        issue_id="",  # Filled by engine
        rule_id="TEX_UI_MIPMAP_ENABLED",
        severity="medium",
        asset_path=asset.asset_path,
        title="UI Texture has Mipmap enabled",
        message=(
            f"UI texture '{asset.asset_path}' has mipmap enabled. "
            f"Normal UI textures typically do not need mipmaps."
        ),
        evidence={
            "texture_type": info.texture_type,
            "mipmap_enabled": info.mipmap_enabled,
        },
        suggestion="普通 UI 贴图通常不需要 Mipmap，建议关闭。",
        auto_fixable=True,
    )


def rule_tex_read_write_enabled(asset: AssetInfo, meta: MetaInfo, extracted: object) -> Issue | None:
    """TEX_READ_WRITE_ENABLED: Textures with Read/Write enabled may waste memory."""
    if not isinstance(extracted, TextureInfo):
        return None
    info: TextureInfo = extracted

    if not info.read_write_enabled:
        return None

    return Issue(
        issue_id="",
        rule_id="TEX_READ_WRITE_ENABLED",
        severity="high",
        asset_path=asset.asset_path,
        title="Texture has Read/Write enabled",
        message=(
            f"Texture '{asset.asset_path}' has Read/Write enabled. "
            f"This doubles memory usage as Unity keeps a CPU-accessible copy."
        ),
        evidence={
            "read_write_enabled": info.read_write_enabled,
        },
        suggestion="如果运行时没有像素读写需求，建议关闭 Read/Write。",
        auto_fixable=False,
    )


def rule_tex_ui_max_size_too_large(asset: AssetInfo, meta: MetaInfo, extracted: object) -> Issue | None:
    """TEX_UI_MAX_SIZE_TOO_LARGE: UI textures should have reasonable max size."""
    if not isinstance(extracted, TextureInfo):
        return None
    info: TextureInfo = extracted

    if not _is_ui_texture(asset, info):
        return None
    if info.max_texture_size is None or info.max_texture_size <= 1024:
        return None

    return Issue(
        issue_id="",
        rule_id="TEX_UI_MAX_SIZE_TOO_LARGE",
        severity="medium",
        asset_path=asset.asset_path,
        title=f"UI Texture Max Size too large: {info.max_texture_size}",
        message=(
            f"UI texture '{asset.asset_path}' has max texture size set to {info.max_texture_size}. "
            f"For mobile platforms, UI textures should be limited to 1024 or lower."
        ),
        evidence={
            "max_texture_size": info.max_texture_size,
            "texture_type": info.texture_type,
        },
        suggestion="移动端 UI 贴图建议限制到合理尺寸，例如 1024 或更低。",
        auto_fixable=True,
    )


def rule_tex_npot_detected(asset: AssetInfo, meta: MetaInfo, extracted: object) -> Issue | None:
    """TEX_NPOT_DETECTED: Non-power-of-two texture dimensions."""
    if not isinstance(extracted, TextureInfo):
        return None
    info: TextureInfo = extracted

    if not info.is_npot:
        return None
    if info.width is None or info.height is None:
        return None

    return Issue(
        issue_id="",
        rule_id="TEX_NPOT_DETECTED",
        severity="low",
        asset_path=asset.asset_path,
        title=f"Non-power-of-two texture: {info.width}x{info.height}",
        message=(
            f"Texture '{asset.asset_path}' has non-power-of-two dimensions "
            f"({info.width}x{info.height}). This may cause issues on some platforms."
        ),
        evidence={
            "width": info.width,
            "height": info.height,
        },
        suggestion="需要结合项目规范判断是否调整为 2 的幂尺寸。",
        auto_fixable=False,
    )
