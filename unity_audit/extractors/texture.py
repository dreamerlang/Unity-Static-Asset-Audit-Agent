"""Texture Extractor - Extract texture properties from image files and .meta."""

import os
import struct
from dataclasses import dataclass

from unity_audit.meta_parser import MetaInfo


@dataclass
class TextureInfo:
    """Extracted texture information for rule evaluation."""
    asset_path: str
    width: int | None = None
    height: int | None = None
    is_npot: bool = False
    has_alpha: bool = False
    texture_type: str | None = None
    mipmap_enabled: bool | None = None
    read_write_enabled: bool | None = None
    max_texture_size: int | None = None
    parse_error: str | None = None


def _get_image_dimensions_png(filepath: str) -> tuple[int | None, int | None, bool | None]:
    """Read PNG dimensions and alpha presence from IHDR chunk."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
            if header[:8] != b"\x89PNG\r\n\x1a\n":
                return None, None, None
            # Read IHDR chunk
            f.read(4)  # chunk length
            chunk_type = f.read(4)
            if chunk_type != b"IHDR":
                return None, None, None
            width = struct.unpack(">I", f.read(4))[0]
            height = struct.unpack(">I", f.read(4))[0]
            f.read(1)[0]
            color_type = f.read(1)[0]
            # Color type 2 = RGB, 6 = RGBA
            has_alpha = color_type in (4, 6)
            return width, height, has_alpha
    except Exception:
        return None, None, None


def _get_image_dimensions_jpeg(filepath: str) -> tuple[int | None, int | None, bool | None]:
    """Read JPEG dimensions. JPEGs never have alpha."""
    try:
        with open(filepath, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None, None, None
            while True:
                marker = f.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    break
                # Skip padding FF bytes
                if marker[1] == 0xFF:
                    continue
                # SOS (Start of Scan) - no more metadata after this
                if marker[1] in (0xD8, 0xD9):
                    continue
                if marker[1] == 0xDA:
                    break
                seg_len = struct.unpack(">H", f.read(2))[0]
                if marker[1] in (0xC0, 0xC2) and seg_len >= 7:
                    height = struct.unpack(">H", f.read(2))[0]
                    width = struct.unpack(">H", f.read(2))[0]
                    return width, height, False
                else:
                    f.seek(seg_len - 2, 1)
    except Exception:
        pass
    return None, None, None


def _get_image_dimensions_pillow(filepath: str) -> tuple[int | None, int | None, bool | None]:
    """Read image dimensions using Pillow (fallback)."""
    try:
        from PIL import Image
        with Image.open(filepath) as img:
            has_alpha = img.mode in ("RGBA", "LA", "PA", "P") and "transparency" in img.info or img.mode in ("RGBA", "LA")
            return img.width, img.height, bool(has_alpha)
    except Exception:
        return None, None, None


def _is_power_of_two(n: int) -> bool:
    """Check if a number is a power of two."""
    return n > 0 and (n & (n - 1)) == 0


def extract_texture_info(asset_path: str, absolute_path: str, meta: MetaInfo) -> TextureInfo:
    """Extract texture information from an image file and its .meta.

    Args:
        asset_path: Relative path from Assets/ root.
        absolute_path: Absolute filesystem path to the image file.
        meta: Parsed MetaInfo from the corresponding .meta file.

    Returns:
        TextureInfo with extracted properties.
    """
    info = TextureInfo(asset_path=asset_path)

    ext = os.path.splitext(absolute_path)[1].lower()

    # Try native parsers first, then Pillow fallback
    width, height, has_alpha = None, None, None

    if ext == ".png":
        width, height, has_alpha = _get_image_dimensions_png(absolute_path)
    elif ext in (".jpg", ".jpeg"):
        width, height, has_alpha = _get_image_dimensions_jpeg(absolute_path)

    # Fallback to Pillow if native parsing failed
    if width is None or height is None:
        width, height, has_alpha_pil = _get_image_dimensions_pillow(absolute_path)
        if has_alpha is None:
            has_alpha = has_alpha_pil

    info.width = width
    info.height = height
    info.has_alpha = bool(has_alpha) if has_alpha is not None else False

    # NPOT check
    if width is not None and height is not None:
        info.is_npot = not (_is_power_of_two(width) and _is_power_of_two(height))

    # Copy meta fields
    info.texture_type = meta.texture_type
    info.mipmap_enabled = meta.mipmap_enabled
    info.read_write_enabled = meta.read_write_enabled
    info.max_texture_size = meta.max_texture_size

    if meta.parse_error:
        info.parse_error = meta.parse_error

    return info
