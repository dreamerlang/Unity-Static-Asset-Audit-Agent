"""Unit tests for Fix Planner (Section 13.6)."""

from unity_audit.evidence import CodeEvidence, EvidenceResult
from unity_audit.fix_planner import plan_fixes
from unity_audit.rules.engine import Issue


def _make_issue(rule_id, asset_path="test.png", severity="high"):
    return Issue(
        issue_id=f"{rule_id}_0",
        rule_id=rule_id,
        severity=severity,
        asset_path=asset_path,
        title="Test",
        message="Test",
    )


class TestReadWriteFixDecisions:
    """FIX-001, FIX-002, FIX-003: Read/Write evidence levels → decisions."""

    def test_direct_evidence_do_not_fix(self):
        """FIX-001: Direct evidence → do_not_fix / high."""
        issue = _make_issue("TEX_READ_WRITE_ENABLED")
        evidence = EvidenceResult(
            issue_id=issue.issue_id,
            association_level="direct",
            code_evidence=[
                CodeEvidence(
                    file="Scripts/Test.cs", line=10, content="tex.GetPixels()",
                    api="GetPixels", association_type="direct",
                    association_value="name=test", confidence=0.95,
                )
            ],
        )
        decisions = plan_fixes([issue], {issue.issue_id: evidence})
        assert len(decisions) == 1
        assert decisions[0].action == "do_not_fix"
        assert decisions[0].risk_level == "high"

    def test_possible_evidence_manual_confirm(self):
        """FIX-002: Possible evidence → manual_confirm_required."""
        issue = _make_issue("TEX_READ_WRITE_ENABLED")
        evidence = EvidenceResult(
            issue_id=issue.issue_id,
            association_level="possible",
            code_evidence=[
                CodeEvidence(
                    file="Scripts/Test.cs", line=10, content="GetPixels()",
                    api="GetPixels", association_type="possible",
                    association_value="API found in project", confidence=0.5,
                )
            ],
        )
        decisions = plan_fixes([issue], {issue.issue_id: evidence})
        assert len(decisions) == 1
        assert decisions[0].action == "manual_confirm_required"

    def test_none_evidence_manual_confirm(self):
        """FIX-003: No evidence → manual_confirm_required, NOT do_not_fix."""
        issue = _make_issue("TEX_READ_WRITE_ENABLED")
        evidence = EvidenceResult(
            issue_id=issue.issue_id,
            association_level="none",
            code_evidence=[],
        )
        decisions = plan_fixes([issue], {issue.issue_id: evidence})
        assert len(decisions) == 1
        assert decisions[0].action == "manual_confirm_required"
        # Must NOT be do_not_fix
        assert decisions[0].action != "do_not_fix"

    def test_missing_evidence_manual_confirm(self):
        """No evidence at all → manual_confirm_required."""
        issue = _make_issue("TEX_READ_WRITE_ENABLED")
        decisions = plan_fixes([issue], {})
        assert len(decisions) == 1
        assert decisions[0].action == "manual_confirm_required"


class TestUIMipmapFixDecisions:
    """FIX-004, FIX-005: UI Mipmap decisions."""

    def test_standard_ui_mipmap_auto_fix(self):
        """FIX-004: Standard UI texture with mipmap → auto_fix_candidate / low."""
        issue = _make_issue("TEX_UI_MIPMAP_ENABLED", asset_path="UI/button.png")
        evidence = EvidenceResult(issue_id=issue.issue_id, association_level="none")
        decisions = plan_fixes([issue], {issue.issue_id: evidence})
        assert decisions[0].action == "auto_fix_candidate"
        assert decisions[0].risk_level == "low"

    def test_worldspace_ui_mipmap_manual_confirm(self):
        """FIX-005: WorldSpaceUI path → manual_confirm_required / medium."""
        issue = _make_issue("TEX_UI_MIPMAP_ENABLED", asset_path="WorldSpaceUI/panel.png")
        evidence = EvidenceResult(issue_id=issue.issue_id, association_level="possible")
        decisions = plan_fixes([issue], {issue.issue_id: evidence})
        assert decisions[0].action == "manual_confirm_required"
        assert decisions[0].risk_level == "medium"

    def test_billboard_path_manual_confirm(self):
        """Billboard path → manual_confirm_required."""
        issue = _make_issue("TEX_UI_MIPMAP_ENABLED", asset_path="UI/Billboard/ad.png")
        decisions = plan_fixes([issue], {issue.issue_id: None})
        assert decisions[0].action == "manual_confirm_required"


class TestMissingScriptFixDecisions:
    """FIX-006: Missing Script → manual_confirm_required / high."""

    def test_missing_script_manual_confirm(self):
        issue = _make_issue("PREFAB_MISSING_SCRIPT", asset_path="Prefabs/Broken.prefab", severity="critical")
        decisions = plan_fixes([issue], {})
        assert decisions[0].action == "manual_confirm_required"
        assert decisions[0].risk_level == "high"
