"""Audio Extractor - Extract audio properties from audio files and .meta."""

import os
import struct
from dataclasses import dataclass

from unity_audit.meta_parser import MetaInfo


@dataclass
class AudioInfo:
    """Extracted audio information for rule evaluation."""
    asset_path: str
    duration_seconds: float | None = None
    channels: int | None = None
    sample_rate: int | None = None
    load_type: str | None = None
    compression_format: str | None = None
    force_to_mono: bool | None = None
    parse_error: str | None = None


def _parse_wav_header(filepath: str) -> tuple[float | None, int | None, int | None]:
    """Parse WAV header to extract duration, channels, and sample rate."""
    try:
        with open(filepath, "rb") as f:
            if f.read(4) != b"RIFF":
                return None, None, None
            f.read(4)  # file size - 8
            if f.read(4) != b"WAVE":
                return None, None, None

            channels = None
            sample_rate = None
            data_size = None

            while True:
                chunk_id = f.read(4)
                if len(chunk_id) < 4:
                    break
                chunk_size = struct.unpack("<I", f.read(4))[0]

                if chunk_id == b"fmt ":
                    fmt_data = f.read(chunk_size)
                    if len(fmt_data) >= 8:
                        channels = struct.unpack("<H", fmt_data[2:4])[0]
                        sample_rate = struct.unpack("<I", fmt_data[4:8])[0]
                elif chunk_id == b"data":
                    data_size = chunk_size
                    break
                else:
                    f.seek(chunk_size, 1)

            if channels and sample_rate and data_size:
                # Sample size assumption: 16-bit = 2 bytes per sample
                bytes_per_sample = 2
                duration = data_size / (sample_rate * channels * bytes_per_sample)
                return duration, channels, sample_rate
    except Exception:
        pass
    return None, None, None


def _parse_mp3_duration(filepath: str) -> float | None:
    """Estimate MP3 duration from file size using common bitrate.

    This is a rough estimate. For production use, consider mutagen or ffprobe.
    """
    try:
        # Common MP3 frame header: 11-bit sync word 0x7FF
        with open(filepath, "rb") as f:
            # Try to find the first valid MP3 frame header
            data = f.read(8192)
            for i in range(len(data) - 4):
                if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
                    # Found a frame sync
                    header = struct.unpack(">I", data[i:i + 4])[0]
                    version_idx = (header >> 19) & 3
                    bitrate_idx = (header >> 12) & 0xF
                    freq_idx = (header >> 10) & 3

                    # MPEG version bitrate tables (kbps)
                    if version_idx == 3:  # MPEG 1
                        bitrates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
                    elif version_idx == 2:  # MPEG 2
                        bitrates = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
                    else:
                        continue

                    if bitrate_idx > 0 and bitrate_idx < 15 and freq_idx < 3:
                        bitrate = bitrates[bitrate_idx] * 1000  # bps
                        file_size = os.path.getsize(filepath)
                        return (file_size * 8) / bitrate  # seconds

            # Fallback: estimate from file size assuming 128kbps
            file_size = os.path.getsize(filepath)
            return (file_size * 8) / 128000.0
    except Exception:
        pass
    return None


def extract_audio_info(asset_path: str, absolute_path: str, meta: MetaInfo) -> AudioInfo:
    """Extract audio information from an audio file and its .meta.

    Args:
        asset_path: Relative path from Assets/ root.
        absolute_path: Absolute filesystem path to the audio file.
        meta: Parsed MetaInfo from the corresponding .meta file.

    Returns:
        AudioInfo with extracted properties.
    """
    info = AudioInfo(asset_path=asset_path)

    ext = os.path.splitext(absolute_path)[1].lower()

    if ext == ".wav":
        info.duration_seconds, info.channels, info.sample_rate = _parse_wav_header(absolute_path)
    elif ext == ".mp3":
        info.duration_seconds = _parse_mp3_duration(absolute_path)

    # Copy meta fields
    info.load_type = meta.load_type
    info.compression_format = meta.compression_format
    info.force_to_mono = meta.force_to_mono

    if meta.parse_error:
        info.parse_error = meta.parse_error

    return info
