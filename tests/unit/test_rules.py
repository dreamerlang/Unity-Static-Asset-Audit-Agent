"""Unit tests for Rule Engine and Rules (Section 13.4)."""
import os

from unity_audit.extractors.audio import AudioInfo
from unity_audit.extractors.prefab_scene import PrefabSceneInfo
from unity_audit.extractors.texture import TextureInfo
from unity_audit.meta_parser import MetaInfo
from unity_audit.rules.audio_rules import (
    rule_aud_long_audio_decompress_on_load,
    rule_aud_stereo_sfx,
)
from unity_audit.rules.engine import Issue, RuleEngine
from unity_audit.rules.prefab_rules import (
    rule_prefab_missing_script,
    rule_ui_too_many_graphic_raycasters,
)
from unity_audit.rules.texture_rules import (
    _is_ui_texture,
    rule_tex_npot_detected,
    rule_tex_read_write_enabled,
    rule_tex_ui_max_size_too_large,
    rule_tex_ui_mipmap_enabled,
)
from unity_audit.scanner import AssetInfo


def _make_asset(path="UI/test.png", atype="Texture"):
    return AssetInfo(
        asset_path=path,
        absolute_path=f"/fake/Assets/{path}",
        asset_type=atype,
        extension=".png",
        file_size=1024,
        meta_path=f"/fake/Assets/{path}.meta",
    )


# ── Rule Engine ────────────────────────────────────────────────────────

class TestRuleEngine:
    def test_evaluate_all_regression(self):
        """RULE-001: test_project produces exactly 10 issues."""
        # This test runs the full engine against test_project fixture data
        # We re-create the known state from the test_project baseline
        from unity_audit.extractors.audio import extract_audio_info
        from unity_audit.extractors.prefab_scene import extract_prefab_scene_info
        from unity_audit.extractors.texture import extract_texture_info
        from unity_audit.meta_parser import parse_meta
        from unity_audit.scanner import scan_project

        test_proj = os.path.join(os.path.dirname(__file__), "..", "..", "test_project")
        scan_result = scan_project(test_proj)

        meta_map = {}
        for asset in scan_result.assets:
            meta_map[asset.asset_path] = parse_meta(asset.meta_path)

        extracted_map = {}
        for asset in scan_result.assets:
            meta = meta_map.get(asset.asset_path, MetaInfo())
            try:
                if asset.asset_type == "Texture":
                    extracted_map[asset.asset_path] = extract_texture_info(
                        asset.asset_path, asset.absolute_path, meta
                    )
                elif asset.asset_type == "Audio":
                    extracted_map[asset.asset_path] = extract_audio_info(
                        asset.asset_path, asset.absolute_path, meta
                    )
                elif asset.asset_type in ("Prefab", "Scene"):
                    extracted_map[asset.asset_path] = extract_prefab_scene_info(
                        asset.asset_path, asset.absolute_path
                    )
            except Exception:
                pass

        engine = RuleEngine()
        engine.register("TEX_UI_MIPMAP_ENABLED", "Texture", rule_tex_ui_mipmap_enabled)
        engine.register("TEX_READ_WRITE_ENABLED", "Texture", rule_tex_read_write_enabled)
        engine.register("TEX_UI_MAX_SIZE_TOO_LARGE", "Texture", rule_tex_ui_max_size_too_large)
        engine.register("TEX_NPOT_DETECTED", "Texture", rule_tex_npot_detected)
        engine.register("AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD", "Audio", rule_aud_long_audio_decompress_on_load)
        engine.register("AUD_STEREO_SFX", "Audio", rule_aud_stereo_sfx)
        engine.register("PREFAB_MISSING_SCRIPT", "Prefab", rule_prefab_missing_script)
        engine.register("PREFAB_MISSING_SCRIPT", "Scene", rule_prefab_missing_script)
        engine.register("UI_TOO_MANY_GRAPHIC_RAYCASTERS", "Prefab", rule_ui_too_many_graphic_raycasters)
        engine.register("UI_TOO_MANY_GRAPHIC_RAYCASTERS", "Scene", rule_ui_too_many_graphic_raycasters)

        issues = engine.evaluate_all(scan_result.assets, meta_map, extracted_map)
        assert len(issues) == 10, f"Expected 10 issues, got {len(issues)}"

    def test_rule_exception_handling(self):
        """RULE-007: Rule exception produces RULE_ERR issue, other rules continue."""
        engine = RuleEngine()

        def _bad_rule(asset, meta, extracted):
            raise RuntimeError("Intentional test error")

        def _good_rule(asset, meta, extracted):
            if isinstance(extracted, TextureInfo) and extracted.read_write_enabled:
                return Issue(
                    issue_id="",
                    rule_id="TEX_READ_WRITE_ENABLED",
                    severity="high",
                    asset_path=asset.asset_path,
                    title="Test",
                    message="Test",
                )
            return None

        engine.register("BAD_RULE", "Texture", _bad_rule)
        engine.register("TEX_READ_WRITE_ENABLED", "Texture", _good_rule)

        asset = _make_asset()
        meta = MetaInfo(read_write_enabled=True)
        tex_info = TextureInfo(asset_path="UI/test.png", read_write_enabled=True)

        issues = engine.evaluate(asset, meta, tex_info)
        assert len(issues) == 2
        rule_err = [i for i in issues if i.rule_id == "BAD_RULE"]
        good_issue = [i for i in issues if i.rule_id == "TEX_READ_WRITE_ENABLED"]
        assert len(rule_err) == 1
        assert len(good_issue) == 1
        assert "RULE_ERR" in rule_err[0].issue_id


# ── Texture Rules ──────────────────────────────────────────────────────

class TestTexUIMipmapEnabled:
    """RULE-002: UI texture with mipmap enabled."""

    def test_ui_texture_with_mipmap_triggers(self):
        asset = _make_asset("UI/button.png")
        meta = MetaInfo(mipmap_enabled=True, texture_type="Sprite")
        tex = TextureInfo(asset_path="UI/button.png", mipmap_enabled=True, texture_type="Sprite")
        issue = rule_tex_ui_mipmap_enabled(asset, meta, tex)
        assert issue is not None
        assert issue.rule_id == "TEX_UI_MIPMAP_ENABLED"
        assert issue.severity == "medium"

    def test_ui_texture_without_mipmap_no_issue(self):
        asset = _make_asset("UI/button.png")
        meta = MetaInfo(mipmap_enabled=False, texture_type="Sprite")
        tex = TextureInfo(asset_path="UI/button.png", mipmap_enabled=False, texture_type="Sprite")
        issue = rule_tex_ui_mipmap_enabled(asset, meta, tex)
        assert issue is None

    def test_non_ui_texture_with_mipmap_no_issue(self):
        asset = _make_asset("Textures/wall.png")
        meta = MetaInfo(mipmap_enabled=True, texture_type="Default")
        tex = TextureInfo(asset_path="Textures/wall.png", mipmap_enabled=True, texture_type="Default")
        issue = rule_tex_ui_mipmap_enabled(asset, meta, tex)
        assert issue is None

    def test_sprite_type_is_ui(self):
        """Sprite texture type should be considered UI."""
        asset = _make_asset("Characters/hero.png")
        MetaInfo(texture_type="Sprite")
        tex = TextureInfo(asset_path="Characters/hero.png", texture_type="Sprite")
        assert _is_ui_texture(asset, tex) is True


class TestTexReadWriteEnabled:
    """RULE-003: Read/Write enabled."""

    def test_read_write_enabled_triggers(self):
        asset = _make_asset("Textures/char.png")
        meta = MetaInfo(read_write_enabled=True)
        tex = TextureInfo(asset_path="Textures/char.png", read_write_enabled=True)
        issue = rule_tex_read_write_enabled(asset, meta, tex)
        assert issue is not None
        assert issue.rule_id == "TEX_READ_WRITE_ENABLED"
        assert issue.severity == "high"

    def test_read_write_disabled_no_issue(self):
        asset = _make_asset("Textures/char.png")
        meta = MetaInfo(read_write_enabled=False)
        tex = TextureInfo(asset_path="Textures/char.png", read_write_enabled=False)
        issue = rule_tex_read_write_enabled(asset, meta, tex)
        assert issue is None

    def test_read_write_none_no_issue(self):
        """If meta doesn't parse read/write field, no issue."""
        asset = _make_asset("Textures/char.png")
        meta = MetaInfo(read_write_enabled=None)
        tex = TextureInfo(asset_path="Textures/char.png", read_write_enabled=None)
        issue = rule_tex_read_write_enabled(asset, meta, tex)
        assert issue is None


class TestTexUIMaxSize:
    """RULE-004: UI max size too large."""

    def test_ui_max_size_large_triggers(self):
        asset = _make_asset("UI/panel.png")
        meta = MetaInfo(max_texture_size=2048)
        tex = TextureInfo(asset_path="UI/panel.png", texture_type="Sprite", max_texture_size=2048)
        issue = rule_tex_ui_max_size_too_large(asset, meta, tex)
        assert issue is not None
        assert issue.rule_id == "TEX_UI_MAX_SIZE_TOO_LARGE"

    def test_ui_max_size_ok_no_issue(self):
        asset = _make_asset("UI/icon.png")
        meta = MetaInfo(max_texture_size=512)
        tex = TextureInfo(asset_path="UI/icon.png", texture_type="Sprite", max_texture_size=512)
        issue = rule_tex_ui_max_size_too_large(asset, meta, tex)
        assert issue is None

    def test_ui_max_size_none_no_issue(self):
        asset = _make_asset("UI/icon.png")
        meta = MetaInfo(max_texture_size=None)
        tex = TextureInfo(asset_path="UI/icon.png", texture_type="Sprite", max_texture_size=None)
        issue = rule_tex_ui_max_size_too_large(asset, meta, tex)
        assert issue is None


class TestTexNPOT:
    """TEX_NPOT_DETECTED rule."""

    def test_npot_triggers(self):
        asset = _make_asset("UI/icon.png")
        meta = MetaInfo()
        tex = TextureInfo(asset_path="UI/icon.png", width=3, height=5, is_npot=True)
        issue = rule_tex_npot_detected(asset, meta, tex)
        assert issue is not None
        assert issue.rule_id == "TEX_NPOT_DETECTED"
        assert issue.severity == "low"

    def test_pot_no_issue(self):
        asset = _make_asset("UI/icon.png")
        meta = MetaInfo()
        tex = TextureInfo(asset_path="UI/icon.png", width=64, height=64, is_npot=False)
        issue = rule_tex_npot_detected(asset, meta, tex)
        assert issue is None

    def test_null_dimensions_no_issue(self):
        asset = _make_asset("UI/icon.png")
        meta = MetaInfo()
        tex = TextureInfo(asset_path="UI/icon.png", width=None, height=None, is_npot=True)
        issue = rule_tex_npot_detected(asset, meta, tex)
        assert issue is None


# ── Audio Rules ────────────────────────────────────────────────────────

class TestAudioLongDecompress:
    """RULE-005: Long audio with Decompress On Load."""

    def test_long_audio_decompress_triggers(self):
        asset = _make_asset("Audio/bgm.wav", "Audio")
        meta = MetaInfo(load_type="Decompress On Load")
        audio = AudioInfo(asset_path="Audio/bgm.wav", duration_seconds=30.0, load_type="Decompress On Load")
        issue = rule_aud_long_audio_decompress_on_load(asset, meta, audio)
        assert issue is not None
        assert issue.rule_id == "AUD_LONG_AUDIO_DECOMPRESS_ON_LOAD"
        assert issue.severity == "high"

    def test_short_audio_no_issue(self):
        asset = _make_asset("Audio/sfx.wav", "Audio")
        meta = MetaInfo(load_type="Decompress On Load")
        audio = AudioInfo(asset_path="Audio/sfx.wav", duration_seconds=2.0, load_type="Decompress On Load")
        issue = rule_aud_long_audio_decompress_on_load(asset, meta, audio)
        assert issue is None

    def test_long_audio_streaming_no_issue(self):
        asset = _make_asset("Audio/bgm.wav", "Audio")
        meta = MetaInfo(load_type="Streaming")
        audio = AudioInfo(asset_path="Audio/bgm.wav", duration_seconds=30.0, load_type="Streaming")
        issue = rule_aud_long_audio_decompress_on_load(asset, meta, audio)
        assert issue is None

    def test_null_duration_no_issue(self):
        asset = _make_asset("Audio/bgm.wav", "Audio")
        meta = MetaInfo(load_type="Decompress On Load")
        audio = AudioInfo(asset_path="Audio/bgm.wav", duration_seconds=None, load_type="Decompress On Load")
        issue = rule_aud_long_audio_decompress_on_load(asset, meta, audio)
        assert issue is None


class TestAudioStereoSFX:
    """AUD_STEREO_SFX rule."""

    def test_stereo_sfx_triggers(self):
        asset = _make_asset("Audio/SFX/boom.wav", "Audio")
        meta = MetaInfo(force_to_mono=False)
        audio = AudioInfo(asset_path="Audio/SFX/boom.wav", channels=2, force_to_mono=False)
        issue = rule_aud_stereo_sfx(asset, meta, audio)
        assert issue is not None
        assert issue.rule_id == "AUD_STEREO_SFX"
        assert issue.severity == "low"

    def test_mono_sfx_no_issue(self):
        asset = _make_asset("Audio/SFX/boom.wav", "Audio")
        meta = MetaInfo(force_to_mono=False)
        audio = AudioInfo(asset_path="Audio/SFX/boom.wav", channels=1, force_to_mono=False)
        issue = rule_aud_stereo_sfx(asset, meta, audio)
        assert issue is None

    def test_stereo_already_mono_no_issue(self):
        asset = _make_asset("Audio/SFX/boom.wav", "Audio")
        meta = MetaInfo(force_to_mono=True)
        audio = AudioInfo(asset_path="Audio/SFX/boom.wav", channels=2, force_to_mono=True)
        issue = rule_aud_stereo_sfx(asset, meta, audio)
        assert issue is None

    def test_not_sfx_path_no_issue(self):
        asset = _make_asset("Audio/Music/song.wav", "Audio")
        meta = MetaInfo(force_to_mono=False)
        audio = AudioInfo(asset_path="Audio/Music/song.wav", channels=2, force_to_mono=False)
        issue = rule_aud_stereo_sfx(asset, meta, audio)
        assert issue is None


# ── Prefab/Scene Rules ─────────────────────────────────────────────────

class TestMissingScript:
    """RULE-006: Missing Script detection."""

    def test_missing_script_triggers(self):
        asset = _make_asset("Prefabs/Broken.prefab", "Prefab")
        meta = MetaInfo()
        ps = PrefabSceneInfo(asset_path="Prefabs/Broken.prefab", missing_script_count=2)
        issue = rule_prefab_missing_script(asset, meta, ps)
        assert issue is not None
        assert issue.rule_id == "PREFAB_MISSING_SCRIPT"
        assert issue.severity == "critical"

    def test_no_missing_script(self):
        asset = _make_asset("Prefabs/Good.prefab", "Prefab")
        meta = MetaInfo()
        ps = PrefabSceneInfo(asset_path="Prefabs/Good.prefab", missing_script_count=0)
        issue = rule_prefab_missing_script(asset, meta, ps)
        assert issue is None


class TestGraphicRaycaster:
    """UI_TOO_MANY_GRAPHIC_RAYCASTERS rule."""

    def test_multiple_raycasters_triggers(self):
        asset = _make_asset("Prefabs/UI.prefab", "Prefab")
        meta = MetaInfo()
        ps = PrefabSceneInfo(asset_path="Prefabs/UI.prefab", graphic_raycaster_count=4)
        issue = rule_ui_too_many_graphic_raycasters(asset, meta, ps)
        assert issue is not None
        assert issue.rule_id == "UI_TOO_MANY_GRAPHIC_RAYCASTERS"
        assert issue.severity == "medium"

    def test_single_raycaster_no_issue(self):
        asset = _make_asset("Prefabs/UI.prefab", "Prefab")
        meta = MetaInfo()
        ps = PrefabSceneInfo(asset_path="Prefabs/UI.prefab", graphic_raycaster_count=1)
        issue = rule_ui_too_many_graphic_raycasters(asset, meta, ps)
        assert issue is None

    def test_zero_raycasters_no_issue(self):
        asset = _make_asset("Prefabs/UI.prefab", "Prefab")
        meta = MetaInfo()
        ps = PrefabSceneInfo(asset_path="Prefabs/UI.prefab", graphic_raycaster_count=0)
        issue = rule_ui_too_many_graphic_raycasters(asset, meta, ps)
        assert issue is None
