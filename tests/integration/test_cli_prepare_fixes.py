"""Integration tests for the auto-fix preparation CLI."""

import json
import subprocess
import sys


def test_prepare_fixes_generates_read_write_unity_package(tmp_path):
    project = tmp_path / "UnityProject"
    project.mkdir()
    input_path = tmp_path / "agent_fix_plans.json"
    output_dir = tmp_path / "prepared"
    input_path.write_text(
        json.dumps([
            {
                "issue_id": "TEX_RW_1",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.91,
                "usage_context": "ui",
                "evidence_strength": "none",
                "fix_plan": {
                    "fix_type": "importer_setting",
                    "target_asset": "Textures/UI/button.png",
                    "changes": {"isReadable": False},
                    "verification_steps": ["Run UI smoke test"],
                    "requires_approval": True,
                },
            },
            {
                "issue_id": "TEX_MIPMAP_1",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.88,
                "usage_context": "ui",
                "evidence_strength": "none",
                "fix_plan": {
                    "fix_type": "importer_setting",
                    "target_asset": "Textures/UI/icon.png",
                    "changes": {"mipmapEnabled": False},
                    "verification_steps": ["Run UI smoke test"],
                    "requires_approval": True,
                },
            },
            {
                "issue_id": "TEX_MAX_1",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.84,
                "usage_context": "ui",
                "evidence_strength": "none",
                "fix_plan": {
                    "fix_type": "importer_setting",
                    "target_asset": "Textures/UI/panel.png",
                    "changes": {"maxTextureSize": 1024},
                    "verification_steps": ["Check UI quality"],
                    "requires_approval": True,
                },
            }
        ]),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unity_audit.cli",
            "prepare-fixes",
            str(project),
            "--input",
            str(input_path),
            "--output",
            str(output_dir),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "Prepared 3 TextureImporter fix" in result.stdout
    manifest_path = output_dir / "unity_audit_texture_importer_fix_manifest.json"
    script_path = output_dir / "UnityAuditTextureImporterAutoFixer.cs"
    assert manifest_path.exists()
    assert script_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {item["setting"] for item in manifest["items"]} == {
        "isReadable", "mipmapEnabled", "maxTextureSize",
    }


def test_prepare_fixes_approved_writes_under_project_assets_editor(tmp_path):
    project = tmp_path / "UnityProject"
    project.mkdir()
    input_path = tmp_path / "agent_fix_plans.json"
    input_path.write_text(
        json.dumps([
            {
                "issue_id": "TEX_RW_1",
                "recommended_action": "auto_fix_candidate",
                "confidence": 0.91,
                "fix_plan": {
                    "fix_type": "importer_setting",
                    "target_asset": "Assets/Textures/UI/button.png",
                    "changes": {"isReadable": False},
                    "verification_steps": ["Run UI smoke test"],
                    "requires_approval": True,
                },
            }
        ]),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unity_audit.cli",
            "prepare-fixes",
            str(project),
            "--input",
            str(input_path),
            "--approve",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    package_dir = project / "Assets" / "Editor" / "UnityAuditAutoFix"
    assert (package_dir / "UnityAuditTextureImporterAutoFixer.cs").exists()
    assert (package_dir / "unity_audit_texture_importer_fix_manifest.json").exists()
