"""Project Scanner - Scan Unity project Assets directory for resource files."""

import os
from dataclasses import dataclass, field

# Supported asset extensions mapped to asset types
EXTENSION_MAP = {
    # Textures
    ".png": "Texture",
    ".jpg": "Texture",
    ".jpeg": "Texture",
    ".tga": "Texture",
    ".psd": "Texture",
    # Audio
    ".wav": "Audio",
    ".mp3": "Audio",
    ".ogg": "Audio",
    # Prefab
    ".prefab": "Prefab",
    # Scene
    ".unity": "Scene",
    # Shader
    ".shader": "Shader",
    ".shadergraph": "Shader",
    ".hlsl": "Shader",
    ".cginc": "Shader",
    # Material
    ".mat": "Material",
    # Animation
    ".anim": "AnimationClip",
    ".controller": "AnimatorController",
}

# Directories to skip during scan
SKIP_DIRS = {
    "Library",
    "Temp",
    "Logs",
    "Obj",
    "ProjectSettings",
    "Packages",
}


@dataclass
class AssetInfo:
    """Basic information about a scanned asset."""
    asset_path: str          # Relative path from Assets/ root
    absolute_path: str       # Absolute filesystem path
    asset_type: str          # Texture, Audio, Prefab, Scene
    extension: str           # File extension
    file_size: int           # File size in bytes
    meta_path: str           # Path to corresponding .meta file


@dataclass
class ScanResult:
    """Result of a project scan."""
    project_root: str
    assets: list[AssetInfo] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scan_errors: list[str] = field(default_factory=list)


def _get_asset_type(filename: str) -> str | None:
    """Determine asset type from filename extension."""
    _, ext = os.path.splitext(filename)
    return EXTENSION_MAP.get(ext.lower())


def _should_skip_dir(dirname: str) -> bool:
    """Check if directory should be skipped."""
    return dirname in SKIP_DIRS or dirname.startswith(".")


def scan_project(
    project_root: str,
    assets_dir: str = "Assets",
    path_filter: set[str] | None = None,
) -> ScanResult:
    """Scan a Unity project's Assets directory and return discovered assets.

    Args:
        project_root: Absolute path to the Unity project root.
        assets_dir: Relative path to the assets directory (default: "Assets").
        path_filter: Optional set of relative asset paths to include.
            If provided, only assets matching these paths are scanned.
            Used for incremental/CI scans.

    Returns:
        ScanResult containing the list of discovered assets and any warnings.
    """
    result = ScanResult(project_root=os.path.abspath(project_root))
    assets_root = os.path.join(result.project_root, assets_dir)

    if not os.path.isdir(assets_root):
        result.scan_errors.append(f"Assets directory not found: {assets_root}")
        return result

    for dirpath, dirnames, filenames in os.walk(assets_root):
        # Filter out directories to skip
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]

        for filename in filenames:
            full_path = os.path.join(dirpath, filename)

            # Skip .meta files themselves - they are handled alongside their source
            if filename.endswith(".meta"):
                continue

            asset_type = _get_asset_type(filename)
            if asset_type is None:
                continue

            try:
                file_size = os.path.getsize(full_path)
            except OSError as e:
                result.warnings.append(f"Cannot read file size for {full_path}: {e}")
                file_size = 0

            # Compute relative path from Assets/
            rel_path = os.path.relpath(full_path, assets_root)

            # Apply path filter for incremental scans
            if path_filter is not None and rel_path not in path_filter:
                continue

            asset = AssetInfo(
                asset_path=rel_path,
                absolute_path=full_path,
                asset_type=asset_type,
                extension=os.path.splitext(filename)[1].lower(),
                file_size=file_size,
                meta_path=full_path + ".meta",
            )
            result.assets.append(asset)

    return result
