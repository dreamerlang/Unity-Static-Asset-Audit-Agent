"""Audio rules for the Rule Engine."""


from unity_audit.extractors.audio import AudioInfo
from unity_audit.meta_parser import MetaInfo
from unity_audit.rules.engine import Issue
from unity_audit.scanner import AssetInfo


def rule_aud_long_audio_decompress_on_load(asset: AssetInfo, meta: MetaInfo, extracted: object) -> Issue | None:
    """AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD: Long audio should not use Decompress On Load."""
    if not isinstance(extracted, AudioInfo):
        return None
    info: AudioInfo = extracted

    if info.duration_seconds is None or info.duration_seconds <= 10:
        return None
    if info.load_type != "Decompress On Load":
        return None

    return Issue(
        issue_id="",
        rule_id="AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD",
        severity="high",
        asset_path=asset.asset_path,
        title=f"Long audio ({info.duration_seconds:.1f}s) uses Decompress On Load",
        message=(
            f"Audio '{asset.asset_path}' is {info.duration_seconds:.1f} seconds long "
            f"and uses Decompress On Load. This can cause high memory usage. "
            f"Consider using Streaming or Compressed In Memory instead."
        ),
        evidence={
            "duration_seconds": info.duration_seconds,
            "load_type": info.load_type,
        },
        suggestion="长音频使用 Decompress On Load 可能导致内存占用过高，建议改为 Streaming 或 Compressed In Memory。",
        auto_fixable=False,
    )


def rule_aud_stereo_sfx(asset: AssetInfo, meta: MetaInfo, extracted: object) -> Issue | None:
    """AUD_STEREO_SFX: Short stereo SFX may benefit from Force To Mono."""
    if not isinstance(extracted, AudioInfo):
        return None
    info: AudioInfo = extracted

    # Check if path contains /SFX/
    normalized_path = asset.asset_path.replace("\\", "/").lower()
    if "/sfx/" not in normalized_path:
        return None
    if info.channels != 2:
        return None
    if info.force_to_mono:
        return None  # Already force to mono

    return Issue(
        issue_id="",
        rule_id="AUD_STEREO_SFX",
        severity="low",
        asset_path=asset.asset_path,
        title="Stereo SFX could be converted to Mono",
        message=(
            f"SFX audio '{asset.asset_path}' is stereo (2 channels) but Force To Mono "
            f"is not enabled. If stereo positioning is not needed, mono saves memory."
        ),
        evidence={
            "channels": info.channels,
            "force_to_mono": info.force_to_mono,
        },
        suggestion="如果该音效不需要立体声定位，可以考虑 Force To Mono。",
        auto_fixable=False,
    )
