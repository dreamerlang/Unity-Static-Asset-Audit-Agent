"""Approval-gated auto-fix package generation.

The agent decides whether a fix is safe and emits a structured fix_plan.
This module keeps the write side deterministic: it only accepts known-safe
TextureImporter-setting plans and emits a Unity Editor script plus manifest for
human-approved execution.
"""

from dataclasses import dataclass
import json
from pathlib import Path


MANIFEST_FILENAME = "unity_audit_texture_importer_fix_manifest.json"
SCRIPT_FILENAME = "UnityAuditTextureImporterAutoFixer.cs"


class AutoFixPackageError(ValueError):
    """Raised when no safe auto-fix package can be generated."""


@dataclass(frozen=True)
class ReadWriteFixCandidate:
    """A deterministic TextureImporter setting operation."""
    issue_id: str
    asset_path: str
    confidence: float
    changes: dict
    verification_steps: list[str]
    setting: str = "isReadable"
    value: bool | int = False


@dataclass(frozen=True)
class AutoFixPackage:
    """Paths for the generated Unity auto-fix package."""
    manifest_path: Path
    script_path: Path
    candidate_count: int
    approved: bool
    rejected: list[dict]


def _unity_asset_path(path: str) -> str:
    """Normalize a scanner-relative path to a Unity Assets/... path."""
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        raise ValueError("target_asset is empty")
    if normalized.startswith("/") or ".." in normalized.split("/"):
        raise ValueError(f"unsafe target_asset path: {path}")
    if normalized.startswith("Assets/"):
        return normalized
    return f"Assets/{normalized}"


def collect_read_write_fix_candidates(
    fix_plan_records: list[dict],
) -> tuple[list[ReadWriteFixCandidate], list[dict]]:
    """Collect safe Read/Write auto-fix candidates from agent_fix_plans data."""
    candidates: list[ReadWriteFixCandidate] = []
    rejected: list[dict] = []

    for record in fix_plan_records:
        issue_id = record.get("issue_id", "<unknown>")
        fix_plan = record.get("fix_plan")
        reason = None

        if record.get("recommended_action") != "auto_fix_candidate":
            reason = "recommended_action is not auto_fix_candidate"
        elif not isinstance(fix_plan, dict):
            reason = "missing fix_plan"
        elif fix_plan.get("requires_approval") is not True:
            reason = "fix_plan.requires_approval must be true"
        elif fix_plan.get("fix_type") != "importer_setting":
            reason = "fix_plan.fix_type must be importer_setting"
        elif fix_plan.get("changes") != {"isReadable": False}:
            reason = "only changes {'isReadable': false} are supported"

        if reason is not None:
            rejected.append({"issue_id": issue_id, "reason": reason})
            continue

        try:
            asset_path = _unity_asset_path(fix_plan.get("target_asset", ""))
        except ValueError as exc:
            rejected.append({"issue_id": issue_id, "reason": str(exc)})
            continue

        candidates.append(ReadWriteFixCandidate(
            issue_id=issue_id,
            asset_path=asset_path,
            confidence=float(record.get("confidence", 0.0)),
            changes={"isReadable": False},
            verification_steps=list(fix_plan.get("verification_steps", [])),
            setting="isReadable",
            value=False,
        ))

    return candidates, rejected


def collect_texture_importer_fix_candidates(
    fix_plan_records: list[dict],
) -> tuple[list[ReadWriteFixCandidate], list[dict]]:
    """Collect supported TextureImporter auto-fix candidates."""
    candidates: list[ReadWriteFixCandidate] = []
    rejected: list[dict] = []

    for record in fix_plan_records:
        issue_id = record.get("issue_id", "<unknown>")
        fix_plan = record.get("fix_plan")
        reason = None
        setting = ""
        value: bool | int = False

        if record.get("recommended_action") != "auto_fix_candidate":
            reason = "recommended_action is not auto_fix_candidate"
        elif not isinstance(fix_plan, dict):
            reason = "missing fix_plan"
        elif fix_plan.get("requires_approval") is not True:
            reason = "fix_plan.requires_approval must be true"
        elif fix_plan.get("fix_type") != "importer_setting":
            reason = "fix_plan.fix_type must be importer_setting"
        else:
            setting, value, reason = _supported_texture_importer_change(
                fix_plan.get("changes")
            )

        if reason is not None:
            rejected.append({"issue_id": issue_id, "reason": reason})
            continue

        try:
            asset_path = _unity_asset_path(fix_plan.get("target_asset", ""))
        except ValueError as exc:
            rejected.append({"issue_id": issue_id, "reason": str(exc)})
            continue

        candidates.append(ReadWriteFixCandidate(
            issue_id=issue_id,
            asset_path=asset_path,
            confidence=float(record.get("confidence", 0.0)),
            changes={setting: value},
            verification_steps=list(fix_plan.get("verification_steps", [])),
            setting=setting,
            value=value,
        ))

    return candidates, rejected


def _supported_texture_importer_change(
    changes: object,
) -> tuple[str, bool | int, str | None]:
    """Return the supported TextureImporter setting encoded by a changes object."""
    if not isinstance(changes, dict) or len(changes) != 1:
        return "", False, "changes must contain exactly one supported setting"

    if changes == {"isReadable": False}:
        return "isReadable", False, None
    if changes == {"mipmapEnabled": False}:
        return "mipmapEnabled", False, None

    if set(changes) == {"maxTextureSize"}:
        value = changes["maxTextureSize"]
        if type(value) is int and value > 0:
            return "maxTextureSize", value, None
        return "", False, "maxTextureSize must be a positive integer"

    return "", False, (
        "only isReadable=false, mipmapEnabled=false, or positive "
        "maxTextureSize changes are supported"
    )


def write_read_write_fix_package(
    candidates: list[ReadWriteFixCandidate],
    output_dir: str | Path,
    approved: bool = False,
    rejected: list[dict] | None = None,
) -> AutoFixPackage:
    """Write a Unity Editor script and manifest for approved Read/Write fixes."""
    return write_texture_importer_fix_package(
        candidates,
        output_dir=output_dir,
        approved=approved,
        rejected=rejected,
    )


def write_texture_importer_fix_package(
    candidates: list[ReadWriteFixCandidate],
    output_dir: str | Path,
    approved: bool = False,
    rejected: list[dict] | None = None,
) -> AutoFixPackage:
    """Write a Unity Editor script and manifest for approved TextureImporter fixes."""
    if not candidates:
        raise AutoFixPackageError("No eligible TextureImporter auto-fix candidates found")

    package_dir = Path(output_dir)
    package_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = package_dir / MANIFEST_FILENAME
    script_path = package_dir / SCRIPT_FILENAME

    manifest = {
        "schema_version": 1,
        "approved": approved,
        "fix_type": "texture_importer_setting",
        "items": [
            {
                "issue_id": candidate.issue_id,
                "asset_path": candidate.asset_path,
                "setting": candidate.setting,
                "value": str(candidate.value).lower(),
                "changes": candidate.changes,
                "confidence": candidate.confidence,
                "verification_steps": candidate.verification_steps,
            }
            for candidate in candidates
        ],
        "rejected": rejected or [],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    script_path.write_text(_unity_editor_script(), encoding="utf-8")

    return AutoFixPackage(
        manifest_path=manifest_path,
        script_path=script_path,
        candidate_count=len(candidates),
        approved=approved,
        rejected=rejected or [],
    )


def approved_project_package_dir(project_root: str | Path) -> Path:
    """Return the Unity project package directory used for approved fixes."""
    return Path(project_root) / "Assets" / "Editor" / "UnityAuditAutoFix"


def _unity_editor_script() -> str:
    """Return the deterministic Unity Editor script for TextureImporter fixes."""
    return r'''// Generated by Unity Static Asset Audit Agent.
// Applies only human-approved TextureImporter setting fixes.

using System;
using UnityEditor;
using UnityEngine;

public static class UnityAuditTextureImporterAutoFixer
{
    private const string ManifestAssetPath =
        "Assets/Editor/UnityAuditAutoFix/unity_audit_texture_importer_fix_manifest.json";

    [Serializable]
    private sealed class Manifest
    {
        public bool approved;
        public FixItem[] items;
    }

    [Serializable]
    private sealed class FixItem
    {
        public string issue_id;
        public string asset_path;
        public string setting;
        public string value;
        public ChangeSet changes;
    }

    [Serializable]
    private sealed class ChangeSet
    {
        public bool isReadable;
        public bool mipmapEnabled;
        public int maxTextureSize;
    }

    [MenuItem("Tools/Unity Audit/Apply Approved Texture Importer Fixes")]
    public static void ApplyApprovedTextureImporterFixes()
    {
        ApplyFromManifest(ManifestAssetPath);
    }

    public static void ApplyFromManifest(string manifestAssetPath)
    {
        var manifestAsset = AssetDatabase.LoadAssetAtPath<TextAsset>(manifestAssetPath);
        if (manifestAsset == null)
        {
            throw new InvalidOperationException(
                "Unity Audit fix manifest not found at " + manifestAssetPath);
        }

        var manifest = JsonUtility.FromJson<Manifest>(manifestAsset.text);
        if (manifest == null || !manifest.approved)
        {
            throw new InvalidOperationException(
                "Unity Audit fix manifest is not approved. Regenerate with --approve after review.");
        }

        var changed = 0;
        foreach (var item in manifest.items ?? Array.Empty<FixItem>())
        {
            if (item == null || item.changes == null)
            {
                Debug.LogWarning("Skipping unsupported Unity Audit fix item: " + item?.issue_id);
                continue;
            }

            var importer = AssetImporter.GetAtPath(item.asset_path) as TextureImporter;
            if (importer == null)
            {
                Debug.LogWarning("Skipping non-texture asset: " + item.asset_path);
                continue;
            }

            if (ApplyImporterChange(importer, item))
            {
                importer.SaveAndReimport();
                changed++;
                Debug.Log("Unity Audit applied " + item.setting + " to " + item.asset_path);
            }
        }

        AssetDatabase.SaveAssets();
        Debug.Log("Unity Audit TextureImporter auto-fix complete. Changed textures: " + changed);
    }

    private static bool ApplyImporterChange(TextureImporter importer, FixItem item)
    {
        switch (item.setting)
        {
            case "isReadable":
                if (item.value != "false" || !importer.isReadable)
                {
                    return false;
                }
                importer.isReadable = false;
                return true;

            case "mipmapEnabled":
                if (item.value != "false" || !importer.mipmapEnabled)
                {
                    return false;
                }
                importer.mipmapEnabled = false;
                return true;

            case "maxTextureSize":
                if (!int.TryParse(item.value, out var intValue) || intValue <= 0 ||
                    importer.maxTextureSize == intValue)
                {
                    return false;
                }
                importer.maxTextureSize = intValue;
                return true;

            default:
                Debug.LogWarning("Unsupported Unity Audit setting: " + item.setting);
                return false;
        }
    }
}
'''
