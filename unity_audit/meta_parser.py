"""Meta Parser - Parse Unity .meta files to extract GUID and importer settings."""

import os
import re
from dataclasses import dataclass, field


@dataclass
class MetaInfo:
    """Parsed .meta file information."""
    guid: str | None = None
    importer_type: str | None = None
    raw_meta: str = ""
    parse_error: str | None = None

    # Common texture importer fields
    texture_type: str | None = None
    mipmap_enabled: bool | None = None
    read_write_enabled: bool | None = None
    max_texture_size: int | None = None

    # Common audio importer fields
    load_type: str | None = None       # Decompress On Load / Streaming / Compressed In Memory
    compression_format: str | None = None
    force_to_mono: bool | None = None

    # Extra fields parsed from meta
    extra: dict = field(default_factory=dict)


def parse_meta(meta_path: str) -> MetaInfo:
    """Parse a Unity .meta file and extract key information.

    Parsing failures are non-fatal: warnings are recorded but do not throw.
    """
    meta = MetaInfo()

    if not os.path.isfile(meta_path):
        meta.parse_error = f"Meta file not found: {meta_path}"
        return meta

    try:
        with open(meta_path, encoding="utf-8", errors="replace") as f:
            meta.raw_meta = f.read()
    except OSError as e:
        meta.parse_error = f"Cannot read meta file: {e}"
        return meta

    raw = meta.raw_meta

    # Extract GUID (format: guid: <32 hex chars>)
    guid_match = re.search(r"guid:\s*([a-fA-F0-9]{32})", raw)
    if guid_match:
        meta.guid = guid_match.group(1)

    # Determine importer type from the file itself
    # TextureImporter, AudioImporter, etc.
    importer_match = re.search(r"(\w+Importer)", raw)
    if importer_match:
        meta.importer_type = importer_match.group(1)

    # --- Texture importer fields ---
    _parse_texture_importer_fields(meta, raw)

    # --- Audio importer fields ---
    _parse_audio_importer_fields(meta, raw)

    return meta


def _parse_texture_importer_fields(meta: MetaInfo, raw: str):
    """Parse texture-specific importer fields from meta content."""
    # textureType: 0=Default, 1=Normal Map, 2=GUI, 3=Sprite, 4=Cursor, 5=Reflection, 6=Cookie, 7=Lightmap, 8=Shadowmask
    tt_match = re.search(r"textureType:\s*(\d+)", raw)
    if tt_match:
        tt_map = {0: "Default", 1: "NormalMap", 2: "Editor GUI", 3: "Sprite",
                  4: "Cursor", 5: "Reflection", 6: "Cookie", 7: "Lightmap", 8: "Shadowmask"}
        meta.texture_type = tt_map.get(int(tt_match.group(1)), f"Unknown({tt_match.group(1)})")

    # mipmap enabled: Unity uses "enableMipMap" (newer) or "mipmapEnabled" (older)
    mm_match = re.search(r"(?:enableMipMap|mipmapEnabled):\s*(\d)", raw)
    if mm_match:
        meta.mipmap_enabled = mm_match.group(1) == "1"

    # read/write enabled: Unity uses "readable" (newer) or "isReadable" (older)
    rw_match = re.search(r"(?:readable|isReadable):\s*(\d)", raw)
    if rw_match:
        meta.read_write_enabled = rw_match.group(1) == "1"

    # maxTextureSize: <int>
    mts_match = re.search(r"maxTextureSize:\s*(\d+)", raw)
    if mts_match:
        meta.max_texture_size = int(mts_match.group(1))


def _parse_audio_importer_fields(meta: MetaInfo, raw: str):
    """Parse audio-specific importer fields from meta content."""
    # loadType: 0=Decompress On Load, 1=Compressed In Memory, 2=Streaming
    lt_match = re.search(r"loadType:\s*(\d+)", raw)
    if lt_match:
        lt_map = {0: "Decompress On Load", 1: "Compressed In Memory", 2: "Streaming"}
        meta.load_type = lt_map.get(int(lt_match.group(1)), f"Unknown({lt_match.group(1)})")

    # compressionFormat: 0=PCM, 1=ADPCM, 2=MP3, 3=Vorbis
    cf_match = re.search(r"compressionFormat:\s*(\d+)", raw)
    if cf_match:
        cf_map = {0: "PCM", 1: "ADPCM", 2: "MP3", 3: "Vorbis"}
        meta.compression_format = cf_map.get(int(cf_match.group(1)), f"Unknown({cf_match.group(1)})")

    # forceToMono: 0 or 1
    fm_match = re.search(r"forceToMono:\s*(\d)", raw)
    if fm_match:
        meta.force_to_mono = fm_match.group(1) == "1"
