"""Unit tests for Evidence Builder (Section 13.5)."""
import os

from unity_audit.evidence import (
    CodeEvidence,
    _deduplicate_evidence,
    _determine_association_level,
    _is_comment_line,
    _strip_comments,
    build_evidence_for_issues,
)
from unity_audit.meta_parser import parse_meta
from unity_audit.rules.engine import Issue
from unity_audit.scanner import scan_project

EVIDENCE_PROJECT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "fixtures", "evidence_project")
)


class TestCommentDetection:
    """Helper function tests for comment handling."""

    def test_is_comment_line_single(self):
        assert _is_comment_line("// this is a comment")
        assert _is_comment_line("  // indented comment")
        assert not _is_comment_line("var x = GetPixels(); // trailing")

    def test_is_comment_line_block(self):
        assert _is_comment_line("/* block comment */")
        assert _is_comment_line("* block continuation")

    def test_is_comment_line_code(self):
        assert not _is_comment_line("texture.GetPixels();")
        assert not _is_comment_line("int x = 42;")

    def test_strip_comments_basic(self):
        assert _strip_comments("GetPixels(); // comment") == "GetPixels();"
        assert _strip_comments("x = 1; // comment // more") == "x = 1;"

    def test_strip_comments_no_comment(self):
        assert _strip_comments("GetPixels();") == "GetPixels();"

    def test_strip_comments_pure_comment(self):
        assert _strip_comments("// GetPixels()") == ""


class TestDeduplication:
    """EVID-005: Deduplication."""

    def test_deduplicate_same_file_line_api(self):
        """Duplicate entries with same (file, line, api) should merge."""
        e1 = CodeEvidence(
            file="test.cs", line=10, content="line", api="GetPixels",
            association_type="direct", association_value="name=test",
            confidence=0.95,
        )
        e2 = CodeEvidence(
            file="test.cs", line=10, content="line", api="GetPixels",
            association_type="possible", association_value="name=test",
            confidence=0.5,
        )
        result = _deduplicate_evidence([e1, e2])
        assert len(result) == 1
        assert result[0].confidence == 0.95  # Higher confidence kept

    def test_deduplicate_different_lines(self):
        """Different lines should not be deduplicated."""
        e1 = CodeEvidence(
            file="test.cs", line=10, content="a", api="GetPixels",
            association_type="direct", association_value="x", confidence=0.95,
        )
        e2 = CodeEvidence(
            file="test.cs", line=20, content="b", api="GetPixels",
            association_type="direct", association_value="x", confidence=0.95,
        )
        result = _deduplicate_evidence([e1, e2])
        assert len(result) == 2


class TestAssociationLevel:
    """Test association level determination."""

    def test_direct_wins_over_possible(self):
        evidence = [
            CodeEvidence(file="a.cs", line=1, content="x", api="GetPixels",
                         association_type="possible", association_value="x", confidence=0.5),
            CodeEvidence(file="a.cs", line=2, content="x", api="GetPixels",
                         association_type="direct", association_value="x", confidence=0.95),
        ]
        assert _determine_association_level(evidence) == "direct"

    def test_possible_second_to_none(self):
        evidence = [
            CodeEvidence(file="a.cs", line=1, content="x", api="GetPixels",
                         association_type="possible", association_value="x", confidence=0.5),
        ]
        assert _determine_association_level(evidence) == "possible"

    def test_empty_is_none(self):
        assert _determine_association_level([]) == "none"

    def test_all_none_is_none(self):
        evidence = [
            CodeEvidence(file="a.cs", line=1, content="x", api="GetPixels",
                         association_type="none", association_value="x", confidence=0.1),
        ]
        assert _determine_association_level(evidence) == "none"


class TestEvidenceProject:
    """EVID-001, EVID-002, EVID-003: Evidence with direct/possible/none levels."""

    def test_linked_texture_direct_evidence(self):
        """EVID-001: API + asset name reference = direct."""
        scan_result = scan_project(EVIDENCE_PROJECT)
        tex_assets = [a for a in scan_result.assets if a.asset_path == "Textures/linked_texture.png"]
        assert len(tex_assets) == 1

        meta_map = {}
        for a in scan_result.assets:
            meta_map[a.asset_path] = parse_meta(a.meta_path)

        # Create a fake issue for linked_texture
        issue = Issue(
            issue_id="TEX_READ_WRITE_ENABLED_0",
            rule_id="TEX_READ_WRITE_ENABLED",
            severity="high",
            asset_path=tex_assets[0].asset_path,
            title="Test",
            message="Test",
            evidence={"read_write_enabled": True},
        )

        meta_guid_map = {p: m.guid for p, m in meta_map.items()}
        evidence_map = build_evidence_for_issues(EVIDENCE_PROJECT, [issue], meta_guid_map)

        ev = evidence_map.get(issue.issue_id)
        assert ev is not None
        assert ev.association_level == "direct", \
            f"Expected direct, got {ev.association_level}. Evidence: {ev.code_evidence}"
        assert len(ev.code_evidence) > 0

    def test_unlinked_texture_possible_or_none(self):
        """EVID-002: API exists but unlinked_texture not directly referenced."""
        scan_result = scan_project(EVIDENCE_PROJECT)
        tex_assets = [a for a in scan_result.assets if a.asset_path == "Textures/unlinked_texture.png"]
        assert len(tex_assets) == 1

        meta_map = {}
        for a in scan_result.assets:
            meta_map[a.asset_path] = parse_meta(a.meta_path)

        issue = Issue(
            issue_id="TEX_READ_WRITE_ENABLED_1",
            rule_id="TEX_READ_WRITE_ENABLED",
            severity="high",
            asset_path=tex_assets[0].asset_path,
            title="Test",
            message="Test",
            evidence={"read_write_enabled": True},
        )

        meta_guid_map = {p: m.guid for p, m in meta_map.items()}
        evidence_map = build_evidence_for_issues(EVIDENCE_PROJECT, [issue], meta_guid_map)

        ev = evidence_map.get(issue.issue_id)
        assert ev is not None
        # Unlinked texture should NOT be direct (it's not referenced by code)
        assert ev.association_level != "direct", \
            f"Unlinked texture should not be direct, got {ev.association_level}"

    def test_two_textures_different_levels(self):
        """EVID-003: linked gets direct, unlinked gets not-direct."""
        scan_result = scan_project(EVIDENCE_PROJECT)
        meta_map = {a.asset_path: parse_meta(a.meta_path) for a in scan_result.assets}
        meta_guid_map = {p: m.guid for p, m in meta_map.items()}

        tex_issues = []
        for a in scan_result.assets:
            if a.asset_type == "Texture":
                tex_issues.append(Issue(
                    issue_id=f"TEX_RW_{a.asset_path}",
                    rule_id="TEX_READ_WRITE_ENABLED",
                    severity="high",
                    asset_path=a.asset_path,
                    title="Test",
                    message="Test",
                    evidence={"read_write_enabled": True},
                ))

        evidence_map = build_evidence_for_issues(EVIDENCE_PROJECT, tex_issues, meta_guid_map)

        # linked_texture should be direct
        linked = [i for i in tex_issues if i.asset_path == "Textures/linked_texture.png"]
        unlinked = [i for i in tex_issues if i.asset_path == "Textures/unlinked_texture.png"]
        assert len(linked) == 1
        assert len(unlinked) == 1

        ev_linked = evidence_map[linked[0].issue_id]
        ev_unlinked = evidence_map[unlinked[0].issue_id]

        assert ev_linked.association_level == "direct"
        assert ev_unlinked.association_level != "direct"

    def test_comment_only_api_not_counted(self):
        """EVID-004: API names in comments should not count as valid hits."""
        # The TextureProcessor.cs has commented-out GetPixels() calls
        scan_result = scan_project(EVIDENCE_PROJECT)
        meta_map = {a.asset_path: parse_meta(a.meta_path) for a in scan_result.assets}
        meta_guid_map = {p: m.guid for p, m in meta_map.items()}

        tex_issues = []
        for a in scan_result.assets:
            if a.asset_type == "Texture":
                tex_issues.append(Issue(
                    issue_id=f"TEX_RW_{a.asset_path}",
                    rule_id="TEX_READ_WRITE_ENABLED",
                    severity="high",
                    asset_path=a.asset_path,
                    title="Test",
                    message="Test",
                ))

        evidence_map = build_evidence_for_issues(EVIDENCE_PROJECT, tex_issues, meta_guid_map)

        # Check that commented-out API on line "// var x = anotherTexture.GetPixels();"
        # is not counted as a valid API call
        for ev in evidence_map.values():
            for ce in ev.code_evidence:
                # The line content from comments should be filtered
                assert not ce.content.strip().startswith("//"), \
                    f"Comment line should have been filtered: {ce.content}"
                assert not ce.content.strip().startswith("*"), \
                    f"Block comment line should have been filtered: {ce.content}"

    def test_no_crash_on_regex_special_chars(self):
        """EVID-007: Asset names with regex special chars should work."""
        import os
        import tempfile

        # Create a temp project with a specially-named asset
        with tempfile.TemporaryDirectory() as tmp:
            assets_dir = os.path.join(tmp, "Assets", "Textures")
            os.makedirs(assets_dir)

            # Asset name with regex special chars
            png_path = os.path.join(assets_dir, "texture(1).png")
            # Create minimal PNG
            import struct
            import zlib
            def chunk(ct, d):
                return struct.pack('>I', len(d)) + ct + d + struct.pack('>I', zlib.crc32(ct + d) & 0xFFFFFFFF)
            ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 4, 8, 2, 0, 0, 0))
            raw = b'\x00' + b'\xff\x00\x00\xff' * 16
            idat = chunk(b'IDAT', zlib.compress(raw))
            iend = chunk(b'IEND', b'')
            with open(png_path, 'wb') as f:
                f.write(b'\x89PNG\r\n\x1a\n' + ihdr + idat + iend)

            meta_path = png_path + ".meta"
            with open(meta_path, 'w') as f:
                f.write("fileFormatVersion: 2\n"
                       "guid: spec00000000000000000000000000\n"
                       "TextureImporter:\n"
                       "  readable: 1\n"
                       "  textureType: 0\n"
                       "  maxTextureSize: 1024\n")

            issue = Issue(
                issue_id="TEST",
                rule_id="TEX_READ_WRITE_ENABLED",
                severity="high",
                asset_path="Textures/texture(1).png",
                title="Test",
                message="Test",
            )

            meta_guid_map = {"Textures/texture(1).png": "spec00000000000000000000000000"}
            evidence_map = build_evidence_for_issues(tmp, [issue], meta_guid_map)
            # Should not crash, even if no API hits
            ev = evidence_map.get("TEST")
            assert ev is not None
            assert ev.association_level == "none"
