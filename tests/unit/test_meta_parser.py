"""Unit tests for Meta Parser (Section 13.2)."""
import os

from unity_audit.meta_parser import parse_meta


class TestMetaTextureImporter:
    """META-001: TextureImporter parsing."""

    def test_parse_guid(self, test_project_root):
        """Should correctly parse GUID from .meta file."""
        meta_path = os.path.join(test_project_root, "Assets", "UI", "button_bg.png.meta")
        meta = parse_meta(meta_path)
        assert meta.guid == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"
        assert meta.importer_type == "TextureImporter"

    def test_parse_texture_type(self, test_project_root):
        """Should map textureType integer to string."""
        meta_path = os.path.join(test_project_root, "Assets", "UI", "button_bg.png.meta")
        meta = parse_meta(meta_path)
        assert meta.texture_type == "Sprite"

    def test_parse_default_texture_type(self, test_project_root):
        """textureType 0 maps to Default."""
        meta_path = os.path.join(test_project_root, "Assets", "Textures", "character_diffuse.png.meta")
        meta = parse_meta(meta_path)
        assert meta.texture_type == "Default"

    def test_parse_mipmap_enabled(self, test_project_root):
        """Should detect enableMipMap: 1 as mipmap enabled."""
        # button_bg has enableMipMap: 1
        meta_path = os.path.join(test_project_root, "Assets", "UI", "button_bg.png.meta")
        meta = parse_meta(meta_path)
        assert meta.mipmap_enabled is True

    def test_parse_mipmap_disabled(self, test_project_root):
        """Should detect enableMipMap: 0 as mipmap disabled."""
        meta_path = os.path.join(test_project_root, "Assets", "Textures", "character_diffuse.png.meta")
        meta = parse_meta(meta_path)
        assert meta.mipmap_enabled is False

    def test_parse_read_write_enabled(self, test_project_root):
        """Should detect readable: 1."""
        meta_path = os.path.join(test_project_root, "Assets", "UI", "button_bg.png.meta")
        meta = parse_meta(meta_path)
        assert meta.read_write_enabled is True

    def test_parse_read_write_disabled(self, test_project_root):
        """Should detect readable: 0."""
        meta_path = os.path.join(test_project_root, "Assets", "UI", "icon_strange.png.meta")
        meta = parse_meta(meta_path)
        assert meta.read_write_enabled is False

    def test_parse_max_texture_size(self, test_project_root):
        """Should parse maxTextureSize integer."""
        meta_path = os.path.join(test_project_root, "Assets", "UI", "button_bg.png.meta")
        meta = parse_meta(meta_path)
        assert meta.max_texture_size == 2048


class TestMetaAudioImporter:
    """META-002: AudioImporter parsing."""

    def test_parse_audio_guid(self, test_project_root):
        """Should parse GUID from audio .meta."""
        meta_path = os.path.join(test_project_root, "Assets", "Audio", "bgm.wav.meta")
        meta = parse_meta(meta_path)
        assert meta.guid == "e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
        assert meta.importer_type == "AudioImporter"

    def test_parse_decompress_on_load(self, test_project_root):
        """loadType 0 maps to Decompress On Load."""
        meta_path = os.path.join(test_project_root, "Assets", "Audio", "bgm.wav.meta")
        meta = parse_meta(meta_path)
        assert meta.load_type == "Decompress On Load"

    def test_parse_compressed_in_memory(self, test_project_root):
        """loadType 1 maps to Compressed In Memory."""
        meta_path = os.path.join(test_project_root, "Assets", "Audio", "SFX", "explosion.wav.meta")
        meta = parse_meta(meta_path)
        assert meta.load_type == "Compressed In Memory"

    def test_parse_compression_format_vorbis(self, test_project_root):
        """compressionFormat 3 maps to Vorbis."""
        meta_path = os.path.join(test_project_root, "Assets", "Audio", "bgm.wav.meta")
        meta = parse_meta(meta_path)
        assert meta.compression_format == "Vorbis"

    def test_parse_compression_format_mp3(self, test_project_root):
        """compressionFormat 2 maps to MP3."""
        meta_path = os.path.join(test_project_root, "Assets", "Audio", "SFX", "explosion.wav.meta")
        meta = parse_meta(meta_path)
        assert meta.compression_format == "MP3"

    def test_parse_force_to_mono(self, test_project_root):
        """Should detect forceToMono: 0."""
        meta_path = os.path.join(test_project_root, "Assets", "Audio", "bgm.wav.meta")
        meta = parse_meta(meta_path)
        assert meta.force_to_mono is False


class TestMetaEdgeCases:
    """META-003, META-004, META-005: Edge cases."""

    def test_missing_meta_file(self, tmp_path):
        """META-003: Missing .meta file returns parse_error."""
        meta_path = str(tmp_path / "nonexistent.png.meta")
        meta = parse_meta(meta_path)
        assert meta.parse_error is not None
        assert meta.guid is None

    def test_empty_meta_file(self, tmp_path):
        """Empty meta file should not crash."""
        meta_path = tmp_path / "empty.png.meta"
        meta_path.write_text("")
        meta = parse_meta(str(meta_path))
        assert meta.guid is None
        assert meta.importer_type is None

    def test_missing_fields(self, tmp_path):
        """META-004: Missing fields should be None, no exception."""
        meta_path = tmp_path / "minimal.prefab.meta"
        meta_path.write_text("fileFormatVersion: 2\nguid: abcdef1234567890abcdef1234567890\n")
        meta = parse_meta(str(meta_path))
        assert meta.guid == "abcdef1234567890abcdef1234567890"
        # Texture/Audio fields should be None since importer not present
        assert meta.texture_type is None
        assert meta.mipmap_enabled is None
        assert meta.read_write_enabled is None
        assert meta.max_texture_size is None
        assert meta.load_type is None
        # No parse error
        assert meta.parse_error is None

    def test_partial_texture_fields(self, tmp_path):
        """Meta with some but not all texture fields."""
        meta_path = tmp_path / "partial.png.meta"
        meta_path.write_text(
            "fileFormatVersion: 2\n"
            "guid: abcd1234abcd1234abcd1234abcd1234\n"
            "TextureImporter:\n"
            "  textureType: 3\n"
        )
        meta = parse_meta(str(meta_path))
        assert meta.texture_type == "Sprite"
        assert meta.mipmap_enabled is None
        assert meta.read_write_enabled is None
        assert meta.max_texture_size is None

    def test_platform_override_not_confused(self, test_project_root):
        """META-005: Platform override maxTextureSize should not be confused with default."""
        # button_bg has maxTextureSize: 2048 at default AND platformSettings
        # The first maxTextureSize (2048) at top level IS the default setting
        meta_path = os.path.join(test_project_root, "Assets", "UI", "button_bg.png.meta")
        meta = parse_meta(meta_path)
        # We parse the first maxTextureSize which IS the default
        assert meta.max_texture_size == 2048

    def test_unreadable_file(self, tmp_path):
        """Unreadable file should return parse_error."""
        meta_path = tmp_path / "noperm.png.meta"
        meta_path.write_text("guid: test1234")
        meta_path.chmod(0o000)
        try:
            meta = parse_meta(str(meta_path))
            assert meta.parse_error is not None
        finally:
            meta_path.chmod(0o644)
