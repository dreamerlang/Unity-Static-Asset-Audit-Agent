"""Prefab/Scene Extractor - Text-level scan for common issue patterns.

Does NOT do full YAML AST parsing. Instead uses text/regex scanning to count
known problematic patterns like Missing Script, GraphicRaycaster, Canvas, etc.
"""

import re
from dataclasses import dataclass


@dataclass
class PrefabSceneInfo:
    """Extracted prefab/scene information for rule evaluation."""
    asset_path: str
    missing_script_count: int = 0
    graphic_raycaster_count: int = 0
    canvas_count: int = 0
    particle_system_count: int = 0
    parse_error: str | None = None


def extract_prefab_scene_info(asset_path: str, absolute_path: str) -> PrefabSceneInfo:
    """Extract prefab/scene info via text scanning.

    Args:
        asset_path: Relative path from Assets/ root.
        absolute_path: Absolute filesystem path to the .prefab or .unity file.

    Returns:
        PrefabSceneInfo with extracted counts.
    """
    info = PrefabSceneInfo(asset_path=asset_path)

    try:
        with open(absolute_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        info.parse_error = f"Cannot read file: {e}"
        return info

    # Missing Script: Unity serializes missing scripts with specific patterns
    # In YAML text format: {fileID: 0} or script: {fileID: 0} with a missing guid
    # The classic missing script marker is: m_Script: {fileID: 0}
    info.missing_script_count = len(re.findall(
        r"m_Script:\s*\{fileID:\s*0\}",
        content
    ))

    # Also check for the stripped GUID pattern: script: {fileID: 11500000, guid: , type: 3}
    # In some Unity versions, missing is indicated by an empty guid
    info.missing_script_count += len(re.findall(
        r"guid:\s*,\s*type:\s*3",
        content
    ))

    # Another pattern: {fileID: 0} directly
    # But this is too broad alone, so we look specifically near m_Script context

    # GraphicRaycaster: added by Unity UI system
    # Look for the component name or script reference
    info.graphic_raycaster_count = len(re.findall(
        r"GraphicRaycaster",
        content, re.IGNORECASE
    ))

    # Canvas: base UI component
    info.canvas_count = len(re.findall(
        r"m_Component:\s*\n\s*- component:\s*\{fileID:\s*\d+\}\s*\n\s*\s*m_GameObject:.*\n(?:\s*.*\n)*?\s*-\s*\{fileID:\s*\d+\}:\s*\n\s*Canvas:",
        content
    ))
    # Simpler fallback: count Canvas references
    if info.canvas_count == 0:
        info.canvas_count = len(re.findall(
            r"\bCanvas\b",
            content
        ))

    # ParticleSystem
    info.particle_system_count = len(re.findall(
        r"ParticleSystem",
        content, re.IGNORECASE
    ))

    return info
