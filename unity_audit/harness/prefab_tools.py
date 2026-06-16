"""Prefab Reference Tracer — Parse Unity .prefab/.unity YAML to trace
GameObject → Component → Material → Texture reference chains.

Unity prefab files use a custom YAML-like format with document markers
(--- !u!<class_id> &<file_id>) and reference syntax ({fileID: N, guid: HEX}).

This module does text-level parsing — no external YAML library needed.
"""

import re
from dataclasses import dataclass

# Unity class ID → human-readable name mapping (commonly used types)
UNITY_CLASS_NAMES = {
    1: "GameObject",
    4: "Transform",
    20: "Camera",
    21: "Material",
    23: "MeshRenderer",
    25: "CanvasRenderer",
    28: "Texture2D",
    33: "MeshFilter",
    43: "Mesh",
    48: "Shader",
    65: "BoxCollider",
    82: "AudioSource",
    95: "Animator",
    111: "Animation",
    114: "MonoBehaviour",
    115: "MonoScript",
    137: "SkinnedMeshRenderer",
    212: "SpriteRenderer",
    213: "Sprite",
    222: "Canvas",
    223: "Canvas",
    224: "RectTransform",
    225: "CanvasGroup",
}


@dataclass
class PrefabReference:
    """A single reference found in a prefab/scene."""
    source_object: str          # GameObject name
    source_component: str       # Component type (e.g., "MeshRenderer")
    reference_type: str         # "Material", "Sprite", "Texture", "Shader", "Unknown"
    target_guid: str            # GUID of the referenced asset
    target_file_id: str         # Local fileID within the prefab
    raw_line: str               # The raw matched line


@dataclass
class PrefabTraceResult:
    """Result of tracing a prefab/scene for asset references."""
    asset_path: str
    prefab_type: str            # "prefab" or "scene"
    game_object_count: int
    component_summary: dict     # {class_name: count}
    references: list[PrefabReference]
    resolved_references: list[dict]  # References resolved to asset paths
    parse_error: str | None = None


# ── Regex patterns ──────────────────────────────────────────────────────

# Document marker: --- !u!<class_id> &<file_id>
_DOC_MARKER = re.compile(r'^--- !u!(\d+) &(\d+)')

# GameObject name
_GO_NAME = re.compile(r'^\s+m_Name:\s+(.+)$')

# Component list entry: - component: {fileID: N}
_COMP_ENTRY = re.compile(r'^\s*- component:\s*\{fileID:\s*(\d+)\}')

# GUID reference: {fileID: N, guid: HEX, type: T}
_GUID_REF = re.compile(r'\{fileID:\s*(\d+),\s*guid:\s*([a-fA-F0-9]+),\s*type:\s*(\d+)\}')

# m_Sprite direct reference (often used for SpriteRenderer)
_SPRITE_REF = re.compile(r'm_Sprite:\s*\{fileID:\s*(\d+),\s*guid:\s*([a-fA-F0-9]+),\s*type:\s*(\d+)\}')

# Material array entries: - {fileID: N, guid: HEX, type: 2}
_MATERIAL_ENTRY = re.compile(r'^\s*-\s*\{fileID:\s*(\d+),\s*guid:\s*([a-fA-F0-9]+),\s*type:\s*(\d+)\}')


def _reference_type_from_class_and_line(class_id: int, line: str) -> tuple[str, str]:
    """Determine reference type based on class ID and line content.

    Returns (reference_type, human_label).
    """
    # type: 2 = Material, type: 3 = Sprite/Texture, type: 0 = Texture2D
    _type_match = re.search(r'type:\s*(\d+)', line)
    ref_type_num = int(_type_match.group(1)) if _type_match else -1

    if ref_type_num == 2:
        return "Material", "Material"
    elif ref_type_num == 3:
        return "Sprite", "Sprite"
    elif ref_type_num == 0:
        # Could be Texture2D in material context
        return "Texture", "Texture2D"
    elif class_id == 212 and 'm_Sprite' in line:
        return "Sprite", "Sprite"
    elif class_id == 23 and 'm_Materials' in line:
        return "Material", "Material"
    elif class_id == 114 and 'm_Materials' in line:
        return "Material", "Material"
    else:
        return "Unknown", f"Class_{class_id}"


def trace_prefab_references(absolute_path: str, asset_path: str) -> PrefabTraceResult:
    """Parse a Unity .prefab or .unity file and extract asset reference chains.

    Args:
        absolute_path: Absolute filesystem path to the .prefab/.unity file.
        asset_path: Relative path from project root (for display).

    Returns:
        PrefabTraceResult with all extracted references.
    """
    is_scene = asset_path.endswith('.unity')
    prefab_type = "scene" if is_scene else "prefab"

    result = PrefabTraceResult(
        asset_path=asset_path,
        prefab_type=prefab_type,
        game_object_count=0,
        component_summary={},
        references=[],
        resolved_references=[],
    )

    # Read file
    try:
        with open(absolute_path, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError as e:
        result.parse_error = f"Cannot read file: {e}"
        return result

    # Parse state
    current_class_id: int | None = None
    current_file_id: str | None = None
    current_object_name: str = "(unknown)"
    doc_objects: dict[str, tuple[int, str]] = {}  # file_id → (class_id, name)

    component_map: dict[str, list[str]] = {}  # file_id → [component_file_ids]
    component_types: dict[str, int] = {}  # file_id → class_id
    game_object_count = 0

    # ── Pass 1: Parse document structure ────────────────────────────────
    for _i, line in enumerate(lines):
        # Document marker
        dm = _DOC_MARKER.match(line)
        if dm:
            current_class_id = int(dm.group(1))
            current_file_id = dm.group(2)
            component_types[current_file_id] = current_class_id
            if current_class_id == 1:  # GameObject
                game_object_count += 1
            continue

        # GameObject name
        name_match = _GO_NAME.match(line)
        if name_match and current_file_id:
            current_object_name = name_match.group(1).strip()
            doc_objects[current_file_id] = (current_class_id or 0, current_object_name)
            continue

        # Component reference in GameObject
        comp_match = _COMP_ENTRY.match(line)
        if comp_match and current_class_id == 1:  # Inside a GameObject
            comp_fid = comp_match.group(1)
            component_map.setdefault(current_file_id or "", []).append(comp_fid)

    # ── Pass 2: Extract GUID references ─────────────────────────────────
    # For each document, search for GUID references in its content block
    current_file_id = None
    in_relevant_section = False

    for _i, line in enumerate(lines):
        dm = _DOC_MARKER.match(line)
        if dm:
            current_file_id = dm.group(2)
            cid = int(dm.group(1))
            # Only search in renderers and materials
            in_relevant_section = cid in (23, 212, 114, 137, 21, 28)
            continue

        if not in_relevant_section or not current_file_id:
            continue

        # Try to find which GameObject owns this component
        owner_name = "(unknown)"
        for go_fid, comp_fids in component_map.items():
            if current_file_id in comp_fids:
                owner_name = doc_objects.get(go_fid, (0, "(unknown)"))[1]
                break
        # Also check if this document itself is a GameObject
        if current_file_id in doc_objects:
            owner_name = doc_objects[current_file_id][1]

        class_id = component_types.get(current_file_id, 0)
        class_name = UNITY_CLASS_NAMES.get(class_id, f"Unknown({class_id})")

        # Find all GUID references on this line
        for ref_match in _GUID_REF.finditer(line):
            file_id = ref_match.group(1)
            guid = ref_match.group(2)
            int(ref_match.group(3))

            ref_type, _ = _reference_type_from_class_and_line(class_id, line)

            # Skip self-references (fileID references within the same prefab)
            if not guid or guid == "00000000000000000000000000000000":
                continue

            result.references.append(PrefabReference(
                source_object=owner_name,
                source_component=class_name,
                reference_type=ref_type,
                target_guid=guid,
                target_file_id=file_id,
                raw_line=line.strip()[:200],
            ))

        # Also check m_Sprite pattern (common in SpriteRenderer)
        sprite_match = _SPRITE_REF.search(line)
        if sprite_match:
            file_id = sprite_match.group(1)
            guid = sprite_match.group(2)
            if guid and guid != "00000000000000000000000000000000":
                result.references.append(PrefabReference(
                    source_object=owner_name,
                    source_component=class_name,
                    reference_type="Sprite",
                    target_guid=guid,
                    target_file_id=file_id,
                    raw_line=line.strip()[:200],
                ))

    # ── Summary ──────────────────────────────────────────────────────────
    result.game_object_count = game_object_count
    from collections import Counter
    result.component_summary = dict(Counter(
        UNITY_CLASS_NAMES.get(cid, f"Unknown({cid})")
        for cid in component_types.values()
    ))

    return result


def resolve_references(
    trace_result: PrefabTraceResult,
    guid_to_asset: dict[str, str],
) -> list[dict]:
    """Resolve GUID references to actual asset paths.

    Args:
        trace_result: Result from trace_prefab_references.
        guid_to_asset: Mapping of GUID → asset_path (reverse of meta_guid_map).

    Returns:
        List of resolved references with asset paths.
    """
    resolved = []
    seen = set()

    for ref in trace_result.references:
        guid_lower = ref.target_guid.lower()
        asset_path = guid_to_asset.get(guid_lower)
        if asset_path is None:
            # Try case-insensitive search
            for g, p in guid_to_asset.items():
                if g.lower() == guid_lower:
                    asset_path = p
                    break

        key = (ref.source_object, ref.reference_type, ref.target_guid)
        if key in seen:
            continue
        seen.add(key)

        resolved.append({
            "source_object": ref.source_object,
            "source_component": ref.source_component,
            "reference_type": ref.reference_type,
            "target_guid": ref.target_guid,
            "target_asset": asset_path or "(not found in project)",
            "resolved": asset_path is not None,
        })

    return resolved
