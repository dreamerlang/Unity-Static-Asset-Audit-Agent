"""Tests for approval-gated auto-fix package generation."""

import json

from unity_audit.harness.auto_fix import (
    AutoFixPackageError,
    collect_read_write_fix_candidates,
    collect_texture_importer_fix_candidates,
    write_read_write_fix_package,
    write_texture_importer_fix_package,
)


def _plan(
    *,
    issue_id: str = "TEX_RW_1",
    action: str = "auto_fix_candidate",
    fix_type: str = "importer_setting",
    target_asset: str = "Textures/UI/button.png",
    changes: dict | None = None,
    requires_approval: bool = True,
) -> dict:
    return {
        "issue_id": issue_id,
        "recommended_action": action,
        "confidence": 0.9,
        "usage_context": "ui",
        "evidence_strength": "none",
        "fix_plan": {
            "fix_type": fix_type,
            "target_asset": target_asset,
            "changes": {"isReadable": False} if changes is None else changes,
            "verification_steps": ["Run texture smoke test"],
            "requires_approval": requires_approval,
        },
    }


def test_collect_read_write_fix_candidates_filters_to_safe_approved_plans():
    plans = [
        _plan(issue_id="SAFE"),
        _plan(issue_id="MANUAL", action="manual_confirm_required"),
        _plan(issue_id="WRONG_SETTING", changes={"mipmapEnabled": False}),
        _plan(issue_id="UNAPPROVED", requires_approval=False),
        _plan(issue_id="EDITOR_SCRIPT", fix_type="editor_script"),
    ]

    candidates, rejected = collect_read_write_fix_candidates(plans)

    assert [c.issue_id for c in candidates] == ["SAFE"]
    assert candidates[0].asset_path == "Assets/Textures/UI/button.png"
    assert candidates[0].changes == {"isReadable": False}
    assert {r["issue_id"] for r in rejected} == {
        "MANUAL", "WRONG_SETTING", "UNAPPROVED", "EDITOR_SCRIPT",
    }


def test_collect_texture_importer_fix_candidates_supports_mipmap_and_max_size():
    plans = [
        _plan(
            issue_id="MIPMAP",
            target_asset="Textures/UI/button.png",
            changes={"mipmapEnabled": False},
        ),
        _plan(
            issue_id="MAX_SIZE",
            target_asset="Assets/Textures/UI/panel.png",
            changes={"maxTextureSize": 1024},
        ),
        _plan(
            issue_id="BAD_MAX_SIZE",
            target_asset="Textures/UI/bad.png",
            changes={"maxTextureSize": -1},
        ),
    ]

    candidates, rejected = collect_texture_importer_fix_candidates(plans)

    assert [(c.issue_id, c.setting, c.value) for c in candidates] == [
        ("MIPMAP", "mipmapEnabled", False),
        ("MAX_SIZE", "maxTextureSize", 1024),
    ]
    assert [r["issue_id"] for r in rejected] == ["BAD_MAX_SIZE"]


def test_write_read_write_fix_package_dry_run_writes_manifest_and_script(tmp_path):
    output_dir = tmp_path / "fix-package"
    candidates, _ = collect_read_write_fix_candidates([_plan()])

    package = write_read_write_fix_package(
        candidates,
        output_dir=output_dir,
        approved=False,
    )

    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    script = package.script_path.read_text(encoding="utf-8")

    assert package.approved is False
    assert manifest["approved"] is False
    assert manifest["items"][0]["asset_path"] == "Assets/Textures/UI/button.png"
    assert manifest["items"][0]["changes"] == {"isReadable": False}
    assert "TextureImporter" in script
    assert "importer.isReadable = false;" in script
    assert "SaveAndReimport" in script


def test_write_texture_importer_fix_package_writes_generalized_script(tmp_path):
    output_dir = tmp_path / "fix-package"
    candidates, _ = collect_texture_importer_fix_candidates([
        _plan(issue_id="RW", changes={"isReadable": False}),
        _plan(issue_id="MIPMAP", changes={"mipmapEnabled": False}),
        _plan(issue_id="MAX", changes={"maxTextureSize": 1024}),
    ])

    package = write_texture_importer_fix_package(
        candidates,
        output_dir=output_dir,
        approved=True,
    )

    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    script = package.script_path.read_text(encoding="utf-8")

    assert package.approved is True
    assert manifest["fix_type"] == "texture_importer_setting"
    assert {item["setting"] for item in manifest["items"]} == {
        "isReadable", "mipmapEnabled", "maxTextureSize",
    }
    assert "UnityAuditTextureImporterAutoFixer" in script
    assert "importer.mipmapEnabled = false;" in script
    assert "importer.maxTextureSize = intValue;" in script


def test_write_read_write_fix_package_rejects_empty_candidate_set(tmp_path):
    try:
        write_read_write_fix_package([], output_dir=tmp_path, approved=False)
    except AutoFixPackageError as exc:
        assert "No eligible" in str(exc)
    else:
        raise AssertionError("expected AutoFixPackageError")
