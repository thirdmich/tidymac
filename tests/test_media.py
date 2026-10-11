"""Unit tests for media scanning, protection, and tool integration."""

import tempfile
import unittest
from pathlib import Path

from tidymac.ai.tools import execute_tool, tool_scan_media
from tidymac.patterns import is_system_protected
from tidymac.scanner import DuplicateGroup, scan_media


class TestMediaScanning(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp_dir.name)


        # Create mock media files
        self.video1 = self.root / "clip1.mp4"
        self.video1.write_bytes(b"V" * 1024 * 100)  # 100 KB

        self.screen_rec = self.root / "Screen Recording 2026-01-01.mov"
        self.screen_rec.write_bytes(b"S" * 1024 * 50)  # 50 KB

        self.screenshot = self.root / "Screenshot 2026-01-01.png"
        self.screenshot.write_bytes(b"P" * 1024 * 20)  # 20 KB

        # Create mock photo library bundle
        self.mock_library = self.root / "Family.photoslibrary"
        self.mock_library.mkdir()
        (self.mock_library / "database.sqlite").write_bytes(b"DB")
        (self.mock_library / "photo.jpg").write_bytes(b"IMG")

        # Create mock duplicates (>= 10KB threshold)
        self.dupe1 = self.root / "photo_a.jpg"
        self.dupe2 = self.root / "photo_b.jpg"
        content = b"IDENTICAL_PHOTO_DATA_12345" * 1000
        self.dupe1.write_bytes(content)
        self.dupe2.write_bytes(content)


    def tearDown(self):
        self.temp_dir.cleanup()

    def test_library_protection(self):
        """Ensure photo and video library bundles are system protected."""
        self.assertTrue(is_system_protected(self.mock_library))
        self.assertTrue(is_system_protected(self.mock_library / "photo.jpg"))
        self.assertTrue(is_system_protected("/Users/test/Movies/test.imovielibrary"))
        self.assertTrue(is_system_protected("/Users/test/Movies/test.fcpbundle"))

    def test_scan_media_detection(self):
        """Verify scan_media categorizes libraries, recordings, screenshots, and dupes."""
        res = scan_media(roots=self.root, min_video_size_mb=0, find_duplicates=True)

        # Check library
        self.assertEqual(len(res.photo_libraries), 1)
        self.assertEqual(res.photo_libraries[0].name, "Family.photoslibrary")
        self.assertTrue(res.photo_libraries[0].is_apple_photos)

        # Check screen recording
        self.assertTrue(any(f.path.name.startswith("Screen Recording") for f in res.screen_recordings))

        # Check screenshot
        self.assertTrue(any(f.path.name.startswith("Screenshot") for f in res.screenshots))

        # Check duplicate group
        self.assertTrue(len(res.duplicate_groups) >= 1)
        dupe_names = [f.name for g in res.duplicate_groups for f in g.files]
        self.assertIn("photo_a.jpg", dupe_names)
        self.assertIn("photo_b.jpg", dupe_names)

    def test_tool_scan_media(self):
        """Test AI tool scan_media wrapper."""
        result = tool_scan_media(search_path=str(self.root), min_video_size_mb=0)
        self.assertIn("photo_libraries", result)
        self.assertIn("screen_recordings", result)
        self.assertIn("screenshots", result)
        self.assertIn("duplicate_groups", result)
        self.assertEqual(len(result["photo_libraries"]), 1)
        self.assertIn("PROTECTED BUNDLE", result["photo_libraries"][0]["safety_note"])

    def test_duplicate_group_formatting(self):
        group = DuplicateGroup(
            file_hash="abc12345",
            size=1024 * 1024 * 10,
            files=[Path("a.mp4"), Path("b.mp4")],
        )
        self.assertEqual(group.size_human(), "10.0 MB")
        self.assertEqual(group.wasted_space_human(), "10.0 MB")


if __name__ == "__main__":
    unittest.main()
