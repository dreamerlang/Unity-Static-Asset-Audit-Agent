"""Extractors for Shader and Material assets.

Shader: Parses .shader files for variant keywords (multi_compile, shader_feature)
        and estimates variant count to flag excessive compilation targets.

Material: Parses .mat YAML files to detect texture references and flag
          properties that match Unity defaults (redundant).
"""

import re
from dataclasses import dataclass


@dataclass
class ShaderInfo:
    """Extracted data from a .shader file."""
    asset_path: str
    variant_keywords: list[str]       # Detected shader_feature/multi_compile keywords
    variant_count: int                # Estimated variant count (2^n approx)
    pass_count: int                   # Number of shader passes
    has_geometry: bool = False
    has_tessellation: bool = False
    parse_error: str | None = None


@dataclass
class MaterialInfo:
    """Extracted data from a .mat file."""
    asset_path: str
    shader_name: str = ""
    property_count: int = 0
    texture_references: dict[str, str] = None  # property → GUID
    missing_textures: list[str] = None
    parse_error: str | None = None

    def __post_init__(self):
        if self.texture_references is None:
            self.texture_references = {}
        if self.missing_textures is None:
            self.missing_textures = []


# Regex patterns for shader analysis
_RE_PRAGMA_MULTI_COMPILE = re.compile(
    r'#pragma\s+(multi_compile|shader_feature)\s+(\w.*?)(?:\n|$)',
    re.MULTILINE,
)
_RE_PRAGMA_VERTEX = re.compile(r'#pragma\s+vertex\s+(\w+)')
_RE_PRAGMA_FRAGMENT = re.compile(r'#pragma\s+fragment\s+(\w+)')
_RE_PRAGMA_GEOMETRY = re.compile(r'#pragma\s+geometry\s+(\w+)')
_RE_PRAGMA_HULL = re.compile(r'#pragma\s+hull\s+(\w+)')
_RE_SUBSHADER = re.compile(r'\bSubShader\b')
_RE_PASS = re.compile(r'\bPass\b\s*\{')
_RE_CGPROGRAM = re.compile(r'\b(CGPROGRAM|HLSLPROGRAM)\b')

# Material YAML patterns
_RE_MATERIAL_SHADER = re.compile(r'm_Shader:\s*\{fileID:\s*[-]?\d+,\s*guid:\s*([a-fA-F0-9]+)')
_RE_MATERIAL_TEX_ENV = re.compile(
    r'-\s*(\w+):\s*\{m_Texture:\s*\{fileID:\s*\d+,\s*guid:\s*([a-fA-F0-9]+)',
    re.MULTILINE,
)
_RE_MATERIAL_PROPERTY = re.compile(r'-\s*(\w+):\s*([^\n]+)')


def extract_shader_info(asset_path: str, source: str) -> ShaderInfo:
    """Extract shader metadata from .shader source text.

    Args:
        asset_path: Relative path within Assets/.
        source: Raw text content of the .shader file.

    Returns:
        ShaderInfo with variant and pass counts.
    """
    try:
        # Count passes
        pass_count = len(_RE_PASS.findall(source))
        if pass_count == 0 and _RE_CGPROGRAM.search(source):
            pass_count = 1  # At least one pass if CGPROGRAM present

        # Count SubShaders
        max(1, len(_RE_SUBSHADER.findall(source)))

        # Extract multi_compile / shader_feature keywords
        keywords = []
        for match in _RE_PRAGMA_MULTI_COMPILE.finditer(source):
            match.group(1)
            kw_args = match.group(2).strip()
            # Split keyword arguments (space-separated)
            for kw in kw_args.split():
                kw = kw.strip()
                if kw and kw not in ("_", "__") and not kw.startswith("//"):
                    keywords.append(kw)

        # Estimate variant count: each keyword roughly doubles variants
        # For multi_compile: 2^(number of toggle keywords)
        # This is an approximation; actual count depends on keyword combinations
        unique_keywords = list(set(keywords))
        if unique_keywords:
            # Conservative estimate: 2^unique_keywords, capped
            variant_count = min(2 ** len(unique_keywords), 65536)
        else:
            variant_count = 1

        # Check for geometry/tessellation shaders
        has_geometry = bool(_RE_PRAGMA_GEOMETRY.search(source))
        has_tessellation = bool(_RE_PRAGMA_HULL.search(source))

        return ShaderInfo(
            asset_path=asset_path,
            variant_keywords=unique_keywords,
            variant_count=variant_count,
            pass_count=pass_count,
            has_geometry=has_geometry,
            has_tessellation=has_tessellation,
        )

    except Exception as e:
        return ShaderInfo(
            asset_path=asset_path,
            variant_keywords=[],
            variant_count=0,
            pass_count=0,
            parse_error=str(e),
        )


def extract_material_info(asset_path: str, source: str) -> MaterialInfo:
    """Extract material metadata from .mat YAML text.

    Args:
        asset_path: Relative path within Assets/.
        source: Raw text content of the .mat file.

    Returns:
        MaterialInfo with shader reference and texture GUIDs.
    """
    try:
        # Extract shader reference
        shader_match = _RE_MATERIAL_SHADER.search(source)
        shader_name = shader_match.group(1) if shader_match else "unknown"

        # Extract texture references (property → GUID)
        textures = {}
        for match in _RE_MATERIAL_TEX_ENV.finditer(source):
            prop_name = match.group(1)
            guid = match.group(2)
            textures[prop_name] = guid

        # Count material properties
        properties = _RE_MATERIAL_PROPERTY.findall(source)
        property_count = len(properties)

        # Detect missing textures (GUID is all zeros → broken reference)
        missing = []
        for prop_name, guid in textures.items():
            if guid == "00000000000000000000000000000000":
                missing.append(prop_name)

        return MaterialInfo(
            asset_path=asset_path,
            shader_name=shader_name,
            property_count=property_count,
            texture_references=textures,
            missing_textures=missing,
        )

    except Exception as e:
        return MaterialInfo(
            asset_path=asset_path,
            parse_error=str(e),
        )
