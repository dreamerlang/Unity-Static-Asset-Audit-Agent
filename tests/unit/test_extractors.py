"""Unit tests for Extractors (Section 13.3)."""
import os
import struct

import pytest

from unity_audit.extractors.audio import (
    extract_audio_info,
)
from unity_audit.extractors.prefab_scene import (
    extract_prefab_scene_info,
)
from unity_audit.extractors.texture import (
    _is_power_of_two,
    extract_texture_info,
)
from unity_audit.meta_parser import MetaInfo

# ── Helpers to create minimal valid test files ──────────────────────────

def _make_png(width=4, height=4, has_alpha=True):
    """Create a minimal valid PNG in memory."""
    import zlib

    def chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    # IHDR
    color_type = 6 if has_alpha else 2  # RGBA or RGB
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    ihdr = chunk(b"IHDR", ihdr_data)
    # IDAT with minimal compressed data
    raw_data = b"\x00" + b"\xff\x00\x00\xff" * width * height
    compressor = zlib.compressobj()
    compressed = compressor.compress(raw_data) + compressor.flush()
    idat = chunk(b"IDAT", compressed)
    # IEND
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def _make_wav(duration=1.0, channels=2, sample_rate=44100):
    """Create a minimal valid WAV in memory."""
    data_size = int(duration * sample_rate * channels * 2)
    data = b"\x00" * data_size
    fmt_data = struct.pack("<HHIIHH", 1, channels, sample_rate,
                           sample_rate * channels * 2, channels * 2, 16)
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt_data)) + fmt_data
    data_chunk = b"data" + struct.pack("<I", data_size) + data
    riff_body = b"WAVE" + fmt_chunk + data_chunk
    return b"RIFF" + struct.pack("<I", 4 + len(riff_body)) + riff_body


# ── Texture tests ──────────────────────────────────────────────────────

class TestTexturePNG:
    """EXT-001: PNG extraction."""

    def test_png_dimensions(self, tmp_path):
        """Should correctly extract width and height from PNG."""
        png_path = tmp_path / "test.png"
        png_path.write_bytes(_make_png(64, 32, has_alpha=False))

        meta = MetaInfo()
        info = extract_texture_info("test.png", str(png_path), meta)
        assert info.width == 64
        assert info.height == 32

    def test_png_alpha_detection_rgba(self, tmp_path):
        """RGBA PNG should have has_alpha=True."""
        png_path = tmp_path / "rgba.png"
        png_path.write_bytes(_make_png(8, 8, has_alpha=True))

        meta = MetaInfo()
        info = extract_texture_info("rgba.png", str(png_path), meta)
        assert info.has_alpha is True

    def test_png_alpha_detection_rgb(self, tmp_path):
        """RGB PNG should have has_alpha=False."""
        png_path = tmp_path / "rgb.png"
        png_path.write_bytes(_make_png(8, 8, has_alpha=False))

        meta = MetaInfo()
        info = extract_texture_info("rgb.png", str(png_path), meta)
        assert info.has_alpha is False

    def test_png_copies_meta_fields(self, tmp_path):
        """Should copy texture_type, mipmap, read/write, max_size from meta."""
        png_path = tmp_path / "meta_test.png"
        png_path.write_bytes(_make_png(4, 4))

        meta = MetaInfo(
            texture_type="Sprite",
            mipmap_enabled=True,
            read_write_enabled=True,
            max_texture_size=2048,
        )
        info = extract_texture_info("meta_test.png", str(png_path), meta)
        assert info.texture_type == "Sprite"
        assert info.mipmap_enabled is True
        assert info.read_write_enabled is True
        assert info.max_texture_size == 2048


class TestTextureNPOT:
    """EXT-002: NPOT detection."""

    def test_pot_texture(self, tmp_path):
        """Power-of-two dimensions should have is_npot=False."""
        png_path = tmp_path / "pot.png"
        png_path.write_bytes(_make_png(64, 64))
        info = extract_texture_info("pot.png", str(png_path), MetaInfo())
        assert info.is_npot is False

    def test_npot_texture(self, tmp_path):
        """Non-power-of-two dimensions should have is_npot=True."""
        png_path = tmp_path / "npot.png"
        png_path.write_bytes(_make_png(3, 5))
        info = extract_texture_info("npot.png", str(png_path), MetaInfo())
        assert info.is_npot is True

    def test_mixed_pot_npot(self, tmp_path):
        """One POT + one NPOT dimension is still NPOT."""
        png_path = tmp_path / "mixed.png"
        png_path.write_bytes(_make_png(64, 30))
        info = extract_texture_info("mixed.png", str(png_path), MetaInfo())
        assert info.is_npot is True


class TestPowerOfTwo:
    """_is_power_of_two helper."""

    def test_powers_of_two(self):
        assert _is_power_of_two(1)
        assert _is_power_of_two(2)
        assert _is_power_of_two(4)
        assert _is_power_of_two(8)
        assert _is_power_of_two(64)
        assert _is_power_of_two(1024)
        assert _is_power_of_two(2048)

    def test_non_powers_of_two(self):
        assert not _is_power_of_two(0)
        assert not _is_power_of_two(3)
        assert not _is_power_of_two(5)
        assert not _is_power_of_two(100)
        assert not _is_power_of_two(1023)


# ── Audio tests ────────────────────────────────────────────────────────

class TestAudioWAV:
    """EXT-003: WAV extraction."""

    def test_wav_duration(self, tmp_path):
        """Should correctly calculate WAV duration."""
        wav_path = tmp_path / "test.wav"
        wav_path.write_bytes(_make_wav(duration=2.0, channels=1, sample_rate=44100))
        info = extract_audio_info("test.wav", str(wav_path), MetaInfo())
        assert info.duration_seconds == pytest.approx(2.0, rel=0.01)

    def test_wav_channels(self, tmp_path):
        """Should extract channel count."""
        wav_path = tmp_path / "stereo.wav"
        wav_path.write_bytes(_make_wav(duration=1.0, channels=2, sample_rate=44100))
        info = extract_audio_info("stereo.wav", str(wav_path), MetaInfo())
        assert info.channels == 2

    def test_wav_sample_rate(self, tmp_path):
        """Should extract sample rate."""
        wav_path = tmp_path / "hires.wav"
        wav_path.write_bytes(_make_wav(duration=1.0, channels=1, sample_rate=48000))
        info = extract_audio_info("hires.wav", str(wav_path), MetaInfo())
        assert info.sample_rate == 48000

    def test_wav_meta_fields(self, tmp_path):
        """Should copy load_type, compression_format, force_to_mono from meta."""
        wav_path = tmp_path / "meta.wav"
        wav_path.write_bytes(_make_wav())
        meta = MetaInfo(
            load_type="Decompress On Load",
            compression_format="PCM",
            force_to_mono=True,
        )
        info = extract_audio_info("meta.wav", str(wav_path), meta)
        assert info.load_type == "Decompress On Load"
        assert info.compression_format == "PCM"
        assert info.force_to_mono is True


class TestExtractorCorruption:
    """EXT-004: Corrupted files."""

    def test_corrupted_png(self, tmp_path):
        """Non-PNG data should not crash, returns no dimensions."""
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not a png file")
        info = extract_texture_info("bad.png", str(bad), MetaInfo())
        assert info.width is None
        assert info.height is None

    def test_empty_png(self, tmp_path):
        """Empty file should not crash."""
        empty = tmp_path / "empty.png"
        empty.write_bytes(b"")
        info = extract_texture_info("empty.png", str(empty), MetaInfo())
        assert info.width is None

    def test_corrupted_wav(self, tmp_path):
        """Non-WAV data should not crash."""
        bad = tmp_path / "bad.wav"
        bad.write_bytes(b"not a wav file")
        info = extract_audio_info("bad.wav", str(bad), MetaInfo())
        assert info.duration_seconds is None


# ── Prefab/Scene tests ─────────────────────────────────────────────────

class TestPrefabScene:
    """EXT-005: Prefab/Scene extraction."""

    def test_prefab_missing_script_count(self, test_project_root):
        """Should count m_Script: {fileID: 0} occurrences."""
        path = os.path.join(test_project_root, "Assets", "Prefabs", "UI_MainPanel.prefab")
        info = extract_prefab_scene_info("Prefabs/UI_MainPanel.prefab", path)
        # prefab has one {fileID: 0} and one guid: , type: 3 → 2 total
        assert info.missing_script_count == 2

    def test_scene_missing_script_count(self, test_project_root):
        """Should count missing scripts in scene."""
        path = os.path.join(test_project_root, "Assets", "Scenes", "MainScene.unity")
        info = extract_prefab_scene_info("Scenes/MainScene.unity", path)
        assert info.missing_script_count == 1

    def test_graphic_raycaster_count(self, test_project_root):
        """Should count GraphicRaycaster references."""
        path = os.path.join(test_project_root, "Assets", "Prefabs", "UI_MainPanel.prefab")
        info = extract_prefab_scene_info("Prefabs/UI_MainPanel.prefab", path)
        assert info.graphic_raycaster_count == 4

    def test_canvas_count(self, test_project_root):
        """Should count Canvas references."""
        path = os.path.join(test_project_root, "Assets", "Prefabs", "UI_MainPanel.prefab")
        info = extract_prefab_scene_info("Prefabs/UI_MainPanel.prefab", path)
        assert info.canvas_count >= 1

    def test_unreadable_file(self, tmp_path):
        """Unreadable file should return parse_error."""
        bad_path = tmp_path / "nonexistent.prefab"
        info = extract_prefab_scene_info("test.prefab", str(bad_path))
        assert info.parse_error is not None

    def test_empty_prefab(self, tmp_path):
        """Empty file should not crash, all counts zero."""
        empty = tmp_path / "empty.prefab"
        empty.write_text("")
        info = extract_prefab_scene_info("empty.prefab", str(empty))
        assert info.missing_script_count == 0
        assert info.graphic_raycaster_count == 0
        assert info.canvas_count == 0
