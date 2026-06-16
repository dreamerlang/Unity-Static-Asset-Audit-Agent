"""Unit tests for Project Scanner (Section 13.1)."""
import os

from unity_audit.scanner import scan_project


class TestScanner:
    """SCAN-001: Scan existing test_project."""

    def test_scan_finds_seven_assets(self, test_project_root):
        """Should find exactly 7 supported assets."""
        result = scan_project(test_project_root)
        assert len(result.assets) == 7, f"Expected 7 assets, got {len(result.assets)}"
        assert len(result.scan_errors) == 0
        assert len(result.warnings) == 0

    def test_scan_no_cs_files_as_assets(self, test_project_root):
        """C# files should not appear as assets."""
        result = scan_project(test_project_root)
        cs_assets = [a for a in result.assets if a.extension == ".cs"]
        assert len(cs_assets) == 0

    def test_scan_no_meta_files_as_assets(self, test_project_root):
        """.meta files should not appear as assets."""
        result = scan_project(test_project_root)
        meta_assets = [a for a in result.assets if a.asset_path.endswith(".meta")]
        assert len(meta_assets) == 0

    def test_scan_asset_types_correct(self, test_project_root):
        """Asset types should be correctly identified."""
        result = scan_project(test_project_root)
        types_found = {a.asset_type for a in result.assets}
        assert types_found == {"Texture", "Audio", "Prefab", "Scene"}

    def test_scan_meta_paths_exist(self, test_project_root):
        """Each asset should have a .meta path set."""
        result = scan_project(test_project_root)
        for asset in result.assets:
            assert asset.meta_path.endswith(".meta")
            assert os.path.basename(asset.meta_path) == os.path.basename(asset.absolute_path) + ".meta"

    def test_scan_relative_paths(self, test_project_root):
        """Asset paths should be relative to Assets/."""
        result = scan_project(test_project_root)
        for asset in result.assets:
            assert not asset.asset_path.startswith("/")
            assert ".." not in asset.asset_path

    def test_scan_file_sizes_positive(self, test_project_root):
        """All scanned files should have positive size."""
        result = scan_project(test_project_root)
        for asset in result.assets:
            assert asset.file_size > 0, f"{asset.asset_path} has size {asset.file_size}"


class TestScannerErrors:
    """SCAN-002: Project without Assets/ directory."""

    def test_missing_assets_dir(self, tmp_path):
        """Should return a scan error, not crash."""
        empty_dir = tmp_path / "empty_project"
        empty_dir.mkdir()
        result = scan_project(str(empty_dir))
        assert len(result.scan_errors) >= 1
        assert len(result.assets) == 0

    def test_nonexistent_project(self, tmp_path):
        """Should handle non-existent directories gracefully."""
        result = scan_project(str(tmp_path / "does_not_exist"))
        assert len(result.scan_errors) >= 1


class TestScannerSkipDirs:
    """SCAN-003: Skip directories."""

    def test_skip_library_temp_logs(self, tmp_path):
        """Library, Temp, Logs dirs should be skipped."""
        proj = tmp_path / "skip_proj"
        assets = proj / "Assets"
        assets.mkdir(parents=True)
        # Create a dummy audio file in Assets/
        wav = assets / "valid.wav"
        wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
        # Create Library/ and Temp/ with files
        lib = proj / "Library"
        lib.mkdir()
        (lib / "metadata.db").write_text("data")
        tempd = proj / "Temp"
        tempd.mkdir()
        (tempd / "tempfile.txt").write_text("temp")
        logs = proj / "Logs"
        logs.mkdir()
        (logs / "editor.log").write_text("log")
        result = scan_project(str(proj))
        # Only the Assets/wav should be found
        assert len(result.assets) == 1
        assert result.assets[0].asset_path == "valid.wav"

    def test_skip_hidden_directories(self, tmp_path):
        """Hidden directories (starting with .) should be skipped."""
        proj = tmp_path / "hidden_proj"
        assets = proj / "Assets"
        assets.mkdir(parents=True)
        # Valid file
        (assets / "valid.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        # Hidden dir
        hidden = assets / ".hidden_dir"
        hidden.mkdir()
        (hidden / "hidden.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        result = scan_project(str(proj))
        assert len(result.assets) == 1
        assert result.assets[0].asset_path == "valid.png"


class TestScannerCaseInsensitive:
    """SCAN-004: Case-insensitive extension matching."""

    def test_uppercase_extension(self, tmp_path):
        """Files with uppercase extensions should be recognized."""
        proj = tmp_path / "case_proj"
        assets = proj / "Assets"
        assets.mkdir(parents=True)
        for fname in ["tex.PNG", "tex.Jpg", "snd.WAV", "scn.UNITY"]:
            (assets / fname).write_text("mock")
        result = scan_project(str(proj))
        assert len(result.assets) == 4

    def test_mixed_case_extension(self, tmp_path):
        """Mixed-case extensions should be recognized and normalized."""
        proj = tmp_path / "mixed_proj"
        assets = proj / "Assets"
        assets.mkdir(parents=True)
        (assets / "tex.PnG").write_text("mock")
        result = scan_project(str(proj))
        assert len(result.assets) == 1
        assert result.assets[0].extension == ".png"


class TestScannerWarnings:
    """SCAN-005: File unreadable handling."""

    def test_file_size_failure(self, tmp_path, monkeypatch):
        """If os.path.getsize raises OSError, record warning not crash."""
        proj = tmp_path / "warn_proj"
        assets = proj / "Assets"
        assets.mkdir(parents=True)
        (assets / "test.png").write_text("mock")


        original_getsize = os.path.getsize

        def failing_getsize(path):
            if path.endswith("test.png"):
                raise OSError("Permission denied")
            return original_getsize(path)

        monkeypatch.setattr(os.path, "getsize", failing_getsize)

        result = scan_project(str(proj))
        assert len(result.assets) == 1
        assert result.assets[0].file_size == 0
        assert len(result.warnings) >= 1
