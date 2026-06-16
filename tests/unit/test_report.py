"""Unit tests for report generation (markdown + HTML)."""

import json
import os

from unity_audit.fix_planner import FixDecision
from unity_audit.report import (
    generate_html_report,
    generate_json_reports,
    generate_markdown_report,
)
from unity_audit.rules.engine import Issue
from unity_audit.scanner import AssetInfo

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_asset(path="Textures/test.png", atype="Texture", ext=".png", size=1024):
    return AssetInfo(
        asset_path=path,
        absolute_path=f"/fake/Assets/{path}",
        asset_type=atype,
        extension=ext,
        file_size=size,
        meta_path=f"/fake/Assets/{path}.meta",
    )


def _make_issue(issue_id="TEX_NPOT_1", rule_id="TEX_NPOT_DETECTED",
                severity="low", asset_path="Textures/test.png",
                title="NPOT texture", message="Texture is NPOT",
                suggestion="Resize to POT", evidence=None, auto_fixable=False):
    return Issue(
        issue_id=issue_id,
        rule_id=rule_id,
        severity=severity,
        asset_path=asset_path,
        title=title,
        message=message,
        evidence=evidence or {"width": 300, "height": 250},
        suggestion=suggestion,
        auto_fixable=auto_fixable,
    )


def _make_decision(issue_id="TEX_NPOT_1", rule_id="TEX_NPOT_DETECTED",
                   asset_path="Textures/test.png", severity="low",
                   action="manual_confirm_required", risk_level="low",
                   reason="需要人工确认", suggestion="Resize to POT"):
    return FixDecision(
        issue_id=issue_id,
        rule_id=rule_id,
        asset_path=asset_path,
        severity=severity,
        action=action,
        risk_level=risk_level,
        reason=reason,
        suggestion=suggestion,
    )


# ── Markdown Report Tests ─────────────────────────────────────────────────────

class TestMarkdownReport:
    def test_generates_report_file(self, tmp_path):
        """Should create report.md in output directory."""
        path = generate_markdown_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            warnings=[],
        )
        assert os.path.exists(path)
        assert path.endswith("report.md")

    def test_report_contains_sections(self, tmp_path):
        """Report should have all 5 standard sections."""
        path = generate_markdown_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "## 1. Summary" in content
        assert "## 2. Issues by Rule" in content
        assert "## 3. Fix Decision Summary" in content
        assert "## 4. High Risk Evidence" in content

    def test_empty_issues_produces_valid_report(self, tmp_path):
        """Should handle empty issues list."""
        path = generate_markdown_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[],
            issues=[],
            fix_decisions=[],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "0" in content  # Should show 0 counts

    def test_warnings_section_shown_when_present(self, tmp_path):
        """Warnings section should appear when there are warnings."""
        path = generate_markdown_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[],
            issues=[],
            fix_decisions=[],
            evidence_map={},
            warnings=["Test warning"],
        )
        content = open(path).read()
        assert "## 5. Scan Warnings" in content
        assert "Test warning" in content

    def test_llm_enhanced_flag(self, tmp_path):
        """LLM Enhanced should reflect llm_used parameter."""
        path_true = generate_markdown_report(
            output_dir=str(tmp_path / "true"),
            project_root="/fake", platform="Android",
            assets=[], issues=[], fix_decisions=[],
            evidence_map={}, warnings=[], llm_used=True,
        )
        path_false = generate_markdown_report(
            output_dir=str(tmp_path / "false"),
            project_root="/fake", platform="Android",
            assets=[], issues=[], fix_decisions=[],
            evidence_map={}, warnings=[], llm_used=False,
        )
        assert "LLM Enhanced:** Yes" in open(path_true).read()
        assert "LLM Enhanced:** No" in open(path_false).read()

    def test_chinese_text_preserved(self, tmp_path):
        """Chinese text in reasons should be preserved."""
        path = generate_markdown_report(
            output_dir=str(tmp_path),
            project_root="/fake", platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision(reason="需要人工确认是否关闭读写")],
            evidence_map={}, warnings=[],
        )
        content = open(path).read()
        assert "需要人工确认是否关闭读写" in content


# ── HTML Report Tests ─────────────────────────────────────────────────────────

class TestHtmlReport:
    def test_generates_html_file(self, tmp_path):
        """Should create report.html in output directory."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            warnings=[],
        )
        assert os.path.exists(path)
        assert path.endswith("report.html")

    def test_html_is_valid_structure(self, tmp_path):
        """HTML should have doctype, html, head, body tags."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "<!DOCTYPE html>" in content
        assert "<html" in content
        assert "<head>" in content
        assert "<body>" in content
        assert "</html>" in content

    def test_html_contains_summary_section(self, tmp_path):
        """HTML should have summary cards with counts."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset(), _make_asset("Textures/t2.png")],
            issues=[_make_issue(), _make_issue("TEX_NPOT_2")],
            fix_decisions=[_make_decision(), _make_decision("TEX_NPOT_2")],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        # Summary grid with counts
        assert "summary-grid" in content
        assert ">2<" in content  # asset count
        assert ">2<" in content  # issue count (same number but different context)

    def test_html_contains_filter_bar(self, tmp_path):
        """HTML should have filter checkboxes and search input."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "filter-bar" in content
        assert 'data-filter="severity"' in content
        assert 'data-filter="action"' in content
        assert "search-input" in content

    def test_html_contains_rule_sections(self, tmp_path):
        """HTML should have rule sections with collapsible headers."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue(rule_id="TEX_NPOT_DETECTED")],
            fix_decisions=[_make_decision()],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "rule-section" in content
        assert "rule-header" in content
        assert "TEX_NPOT_DETECTED" in content

    def test_html_embeds_json_data(self, tmp_path):
        """HTML should embed JSON data blocks for client-side filtering."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert 'id="data-meta"' in content
        assert 'id="data-issues"' in content
        assert 'id="data-decisions"' in content
        assert 'id="data-evidence"' in content
        assert 'application/json' in content

    def test_html_embedded_json_is_valid(self, tmp_path):
        """Embedded JSON data blocks should be parseable."""
        import re
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset(), _make_asset("Textures/t2.png")],
            issues=[_make_issue(), _make_issue("TEX_NPOT_2")],
            fix_decisions=[_make_decision(), _make_decision("TEX_NPOT_2")],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()

        # Extract and parse each JSON block from <script type="application/json"> tags
        for data_id in ["data-meta", "data-issues", "data-decisions", "data-evidence"]:
            pattern = rf'<script type="application/json" id="{data_id}">(.*?)</script>'
            match = re.search(pattern, content, re.DOTALL)
            assert match is not None, f"Missing {data_id} JSON block"
            data = json.loads(match.group(1))
            assert data is not None

    def test_html_empty_issues_produces_valid_output(self, tmp_path):
        """Should handle empty issues list gracefully."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[],
            issues=[],
            fix_decisions=[],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "<!DOCTYPE html>" in content
        assert "</html>" in content

    def test_html_chinese_text_preserved(self, tmp_path):
        """Chinese text should be preserved in HTML output."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision(
                reason="贴图需要人工确认是否关闭读写权限",
                suggestion="建议关闭Read/Write以减少内存占用",
            )],
            evidence_map={},
            warnings=["警告：缺少 .meta 文件"],
        )
        content = open(path).read()
        assert "贴图需要人工确认是否关闭读写权限" in content
        assert "建议关闭Read/Write以减少内存占用" in content
        assert "警告：缺少 .meta 文件" in content

    def test_html_contains_action_badges(self, tmp_path):
        """HTML should have CSS classes for action and severity badges."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue(severity="low")],
            fix_decisions=[_make_decision(action="manual_confirm_required", risk_level="low")],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "badge-low" in content
        assert "badge-manual" in content

    def test_html_multiple_rules_get_separate_sections(self, tmp_path):
        """Each rule_id should get its own collapsible section."""
        issues = [
            _make_issue("TEX_NPOT_1", "TEX_NPOT_DETECTED", "low", "a.png"),
            _make_issue("TEX_RW_1", "TEX_READ_WRITE_ENABLED", "high", "b.png"),
        ]
        decisions = [
            _make_decision("TEX_NPOT_1", "TEX_NPOT_DETECTED", "a.png"),
            _make_decision("TEX_RW_1", "TEX_READ_WRITE_ENABLED", "b.png"),
        ]
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset("a.png"), _make_asset("b.png")],
            issues=issues,
            fix_decisions=decisions,
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        # Both rules should appear as sections
        assert 'data-rule="TEX_NPOT_DETECTED"' in content
        assert 'data-rule="TEX_READ_WRITE_ENABLED"' in content
        # Count section elements
        assert content.count('class="rule-section"') == 2

    def test_html_warnings_rendered_when_present(self, tmp_path):
        """Warnings should be shown when present."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[],
            issues=[],
            fix_decisions=[],
            evidence_map={},
            warnings=["Warning 1", "Warning 2"],
        )
        content = open(path).read()
        assert "Scan Warnings" in content
        assert "Warning 1" in content
        assert "Warning 2" in content

    def test_html_no_warnings_when_empty(self, tmp_path):
        """No warnings section should appear if warnings list is empty."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[],
            issues=[],
            fix_decisions=[],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "Scan Warnings" not in content

    def test_html_filter_javascript_present(self, tmp_path):
        """HTML should contain the filter/search JavaScript logic."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert "function applyFilters()" in content
        assert "function toggleDetail(" in content
        assert "function toggleRule(" in content
        assert "function expandAll()" in content
        assert "function collapseAll()" in content

    def test_html_llm_badge(self, tmp_path):
        """LLM badge should reflect llm_used parameter."""
        path_yes = generate_html_report(
            output_dir=str(tmp_path / "yes"),
            project_root="/fake", platform="Android",
            assets=[], issues=[], fix_decisions=[],
            evidence_map={}, warnings=[], llm_used=True,
        )
        path_no = generate_html_report(
            output_dir=str(tmp_path / "no"),
            project_root="/fake", platform="Android",
            assets=[], issues=[], fix_decisions=[],
            evidence_map={}, warnings=[], llm_used=False,
        )
        assert "llm-yes" in open(path_yes).read()
        assert "llm-no" in open(path_no).read()

    def test_html_data_attributes_on_rows(self, tmp_path):
        """Issue rows should have data-severity and data-action attributes for filtering."""
        path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake/project",
            platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue(severity="low")],
            fix_decisions=[_make_decision(action="do_not_fix", risk_level="low")],
            evidence_map={},
            warnings=[],
        )
        content = open(path).read()
        assert 'data-severity="low"' in content
        assert 'data-action="do_not_fix"' in content


# ── JSON Report Tests ─────────────────────────────────────────────────────────

class TestJsonReports:
    def test_generates_all_json_files(self, tmp_path):
        """Should generate assets.json, issues.json, fix_decisions.json."""
        generate_json_reports(
            output_dir=str(tmp_path),
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
        )
        assert os.path.exists(os.path.join(str(tmp_path), "assets.json"))
        assert os.path.exists(os.path.join(str(tmp_path), "issues.json"))
        assert os.path.exists(os.path.join(str(tmp_path), "fix_decisions.json"))

    def test_json_has_schema_version(self, tmp_path):
        """JSON files should include schema_version."""
        generate_json_reports(
            output_dir=str(tmp_path),
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
        )
        for fname in ["assets.json", "issues.json", "fix_decisions.json"]:
            with open(os.path.join(str(tmp_path), fname)) as f:
                data = json.load(f)
            assert "schema_version" in data

    def test_empty_lists_produce_valid_json(self, tmp_path):
        """Should handle empty data gracefully."""
        generate_json_reports(
            output_dir=str(tmp_path),
            assets=[], issues=[], fix_decisions=[],
        )
        for fname in ["assets.json", "issues.json", "fix_decisions.json"]:
            with open(os.path.join(str(tmp_path), fname)) as f:
                data = json.load(f)
            assert data["items"] == []


# ── CI Annotation Tests ───────────────────────────────────────────────────────

class TestCiAnnotations:
    """Tests for GitHub Actions and GitLab CI annotation output."""

    def _make_evidence_with_code(self, issue_id="TEX_RW_1"):
        """Create an EvidenceResult with code evidence."""
        from unity_audit.evidence import CodeEvidence, EvidenceResult
        return EvidenceResult(
            issue_id=issue_id,
            context_summary="Test context",
            risk_hint="Test risk",
            code_evidence=[
                CodeEvidence(
                    file="Assets/Scripts/TextureUtils.cs",
                    line=10,
                    content="sourceTexture.GetPixels();",
                    api="GetPixels",
                    association_type="possible",
                    association_value="test.png",
                    confidence=0.5,
                )
            ],
        )

    def test_github_annotations_file_generated(self, tmp_path):
        """Should create ci-annotations.txt."""
        from unity_audit.report import generate_ci_annotations
        path = generate_ci_annotations(
            output_dir=str(tmp_path),
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            ci_format="github",
        )
        assert os.path.exists(path)
        assert path.endswith("ci-annotations.txt")

    def test_github_annotations_format(self, tmp_path):
        """Each line should be a valid GitHub workflow command."""
        from unity_audit.report import generate_ci_annotations
        path = generate_ci_annotations(
            output_dir=str(tmp_path),
            issues=[
                _make_issue("TEX_NPOT_1", "TEX_NPOT_DETECTED", "low",
                            "Textures/npot.png", "NPOT texture"),
                _make_issue("TEX_RW_1", "TEX_READ_WRITE_ENABLED", "high",
                            "Textures/rw.png", "RW enabled"),
            ],
            fix_decisions=[
                _make_decision("TEX_NPOT_1", action="manual_confirm_required"),
                _make_decision("TEX_RW_1", action="do_not_fix"),
            ],
            evidence_map={},
            ci_format="github",
        )
        lines = open(path).read().strip().split("\n")
        assert len(lines) == 2

        # First line should be low → notice
        assert lines[0].startswith("::notice ")
        assert "file=Assets/Textures/npot.png.meta" in lines[0]
        assert "line=1" in lines[0]
        assert "title=TEX_NPOT_DETECTED" in lines[0]
        assert "NPOT texture" in lines[0]

        # Second line should be high → warning
        assert lines[1].startswith("::warning ")
        assert "title=TEX_READ_WRITE_ENABLED" in lines[1]

    def test_github_critical_maps_to_error(self, tmp_path):
        """Critical severity should map to ::error."""
        from unity_audit.report import generate_ci_annotations
        path = generate_ci_annotations(
            output_dir=str(tmp_path),
            issues=[_make_issue("PREFAB_1", "PREFAB_MISSING_SCRIPT", "critical",
                               "Prefabs/broken.prefab", "Missing script")],
            fix_decisions=[_make_decision("PREFAB_1", action="manual_confirm_required")],
            evidence_map={},
            ci_format="github",
        )
        lines = open(path).read().strip().split("\n")
        assert lines[0].startswith("::error ")

    def test_github_uses_code_evidence_file(self, tmp_path):
        """When code evidence exists, annotate the .cs file not .meta."""
        from unity_audit.report import generate_ci_annotations
        ev = self._make_evidence_with_code("TEX_RW_1")
        path = generate_ci_annotations(
            output_dir=str(tmp_path),
            issues=[_make_issue("TEX_RW_1", "TEX_READ_WRITE_ENABLED", "high",
                               "Textures/rw.png", "RW enabled")],
            fix_decisions=[_make_decision("TEX_RW_1")],
            evidence_map={"TEX_RW_1": ev},
            ci_format="github",
        )
        lines = open(path).read().strip().split("\n")
        assert "file=Assets/Scripts/TextureUtils.cs" in lines[0]
        assert "line=10" in lines[0]

    def test_github_special_chars_escaped(self, tmp_path):
        """Percent signs and newlines in messages should be escaped."""
        from unity_audit.report import generate_ci_annotations
        path = generate_ci_annotations(
            output_dir=str(tmp_path),
            issues=[_make_issue("TEX_1", "TEX_NPOT_DETECTED", "low",
                               "Textures/test.png", "50% NPOT")],
            fix_decisions=[_make_decision("TEX_1")],
            evidence_map={},
            ci_format="github",
        )
        content = open(path).read()
        assert "%25" in content  # % should be escaped

    def test_gitlab_annotations_file_generated(self, tmp_path):
        """Should create gl-code-quality-report.json."""
        from unity_audit.report import generate_ci_annotations
        path = generate_ci_annotations(
            output_dir=str(tmp_path),
            issues=[_make_issue()],
            fix_decisions=[_make_decision()],
            evidence_map={},
            ci_format="gitlab",
        )
        assert os.path.exists(path)
        assert path.endswith("gl-code-quality-report.json")

    def test_gitlab_json_structure(self, tmp_path):
        """GitLab output should be a valid JSON array with required fields."""
        from unity_audit.report import generate_ci_annotations
        path = generate_ci_annotations(
            output_dir=str(tmp_path),
            issues=[
                _make_issue("TEX_1", "TEX_NPOT_DETECTED", "low", "a.png", "NPOT"),
                _make_issue("TEX_2", "TEX_READ_WRITE_ENABLED", "high", "b.png", "RW"),
            ],
            fix_decisions=[
                _make_decision("TEX_1", action="manual_confirm_required"),
                _make_decision("TEX_2", action="do_not_fix"),
            ],
            evidence_map={},
            ci_format="gitlab",
        )
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 2

        for entry in data:
            assert "description" in entry
            assert "severity" in entry
            assert "location" in entry
            assert "path" in entry["location"]
            assert "lines" in entry["location"]

        # Severity mapping: low → info
        assert data[0]["severity"] == "info"
        # Severity mapping: high → major
        assert data[1]["severity"] == "major"

    def test_gitlab_uses_code_evidence_file(self, tmp_path):
        """When code evidence exists, GitLab should reference the .cs file."""
        from unity_audit.report import generate_ci_annotations
        ev = self._make_evidence_with_code("TEX_RW_1")
        path = generate_ci_annotations(
            output_dir=str(tmp_path),
            issues=[_make_issue("TEX_RW_1", "TEX_READ_WRITE_ENABLED", "high",
                               "Textures/rw.png", "RW enabled")],
            fix_decisions=[_make_decision("TEX_RW_1")],
            evidence_map={"TEX_RW_1": ev},
            ci_format="gitlab",
        )
        with open(path) as f:
            data = json.load(f)
        assert data[0]["location"]["path"] == "Assets/Scripts/TextureUtils.cs"
        assert data[0]["location"]["lines"]["begin"] == 10

    def test_empty_issues_produces_empty_output(self, tmp_path):
        """Empty issue list should produce empty but valid output."""
        from unity_audit.report import generate_ci_annotations

        # GitHub
        gh_path = generate_ci_annotations(
            output_dir=str(tmp_path / "gh"),
            issues=[], fix_decisions=[], evidence_map={},
            ci_format="github",
        )
        gh_content = open(gh_path).read().strip()
        assert gh_content == ""

        # GitLab
        gl_path = generate_ci_annotations(
            output_dir=str(tmp_path / "gl"),
            issues=[], fix_decisions=[], evidence_map={},
            ci_format="gitlab",
        )
        with open(gl_path) as f:
            gl_data = json.load(f)
        assert gl_data == []


# ── Report Stability Tests ────────────────────────────────────────────────────

class TestReportStability:
    """REPORT-001, REPORT-003: Report stability and agent assessment content."""

    def test_report_stable_across_runs(self, tmp_path):
        """REPORT-001: Running twice should produce stable output
        (except timestamps and run IDs)."""
        dir1 = tmp_path / "run1"
        dir2 = tmp_path / "run2"

        issues = [
            _make_issue("TEX_1", "TEX_NPOT_DETECTED", "low", "a.png", "NPOT"),
            _make_issue("TEX_2", "TEX_READ_WRITE_ENABLED", "high", "b.png", "RW"),
        ]
        decisions = [
            _make_decision("TEX_1", "TEX_NPOT_DETECTED", "a.png",
                           action="manual_confirm_required", risk_level="low",
                           reason="需要人工确认"),
            _make_decision("TEX_2", "TEX_READ_WRITE_ENABLED", "b.png",
                           action="do_not_fix", risk_level="high",
                           reason="代码中确实使用了GetPixels"),
        ]

        path1 = generate_markdown_report(
            output_dir=str(dir1), project_root="/fake",
            platform="Android", assets=[_make_asset("a.png"), _make_asset("b.png")],
            issues=issues, fix_decisions=decisions,
            evidence_map={}, warnings=[], llm_used=False,
        )
        path2 = generate_markdown_report(
            output_dir=str(dir2), project_root="/fake",
            platform="Android", assets=[_make_asset("a.png"), _make_asset("b.png")],
            issues=issues, fix_decisions=decisions,
            evidence_map={}, warnings=[], llm_used=False,
        )

        content1 = open(path1).read()
        content2 = open(path2).read()

        # Remove run-specific lines (timestamps, paths that include tmp dir)
        def _stable(content):
            lines = []
            for line in content.split("\n"):
                if "Time elapsed" in line:
                    continue
                lines.append(line)
            return "\n".join(lines)

        assert _stable(content1) == _stable(content2), \
            "Report content should be stable across runs"

        # Check key structural elements are present in both
        for marker in ["## 1. Summary", "## 2. Issues by Rule",
                        "## 3. Fix Decision Summary", "需要人工确认"]:
            assert marker in content1
            assert marker in content2

    def test_report_reflects_agent_enhanced(self, tmp_path):
        """REPORT-003: When LLM is used, report should indicate it."""
        # Test with llm_used=True
        path = generate_markdown_report(
            output_dir=str(tmp_path),
            project_root="/fake", platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision(
                reason="Agent分析：该贴图位于ReferenceImages目录，属于测试参考图，NPOT尺寸不影响运行时性能"
            )],
            evidence_map={}, warnings=[], llm_used=True,
        )
        content = open(path).read()
        # Should show LLM Enhanced badge
        assert "LLM Enhanced:** Yes" in content
        # Should include agent assessment content in reason
        assert "Agent分析" in content
        assert "ReferenceImages" in content

        # Also test HTML report
        html_path = generate_html_report(
            output_dir=str(tmp_path),
            project_root="/fake", platform="Android",
            assets=[_make_asset()],
            issues=[_make_issue()],
            fix_decisions=[_make_decision(
                reason="Agent分析：该贴图位于ReferenceImages目录，属于测试参考图，NPOT尺寸不影响运行时性能"
            )],
            evidence_map={}, warnings=[], llm_used=True,
        )
        html_content = open(html_path).read()
        assert "llm-yes" in html_content
        assert "Agent分析" in html_content
