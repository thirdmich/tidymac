"""Regression tests for deletion guardrails: ancestors of protected paths and case variants."""

import sys
import unittest
import unittest.mock
from pathlib import Path

from tidymac.ai.tools import is_path_excluded, tool_propose_cleanup_plan
from tidymac.cleaner import is_safe_to_remove

HOME = Path.home()
CASE_INSENSITIVE = sys.platform in ("darwin", "win32")


class TestAncestorProtection(unittest.TestCase):
    """Deleting a folder deletes everything in it, so parents of protected paths are protected."""

    def test_home_and_parents_of_protected_paths_are_not_removable(self):
        for p in (HOME, HOME / "Library", HOME / "Library/Application Support", Path("/")):
            with self.subTest(path=p):
                self.assertFalse(is_safe_to_remove(p))

    def test_user_data_folders_themselves_are_not_removable(self):
        self.assertFalse(is_safe_to_remove(HOME / "Documents"))

    def test_files_inside_user_data_folders_stay_removable(self):
        self.assertTrue(is_safe_to_remove(HOME / "Downloads/old-installer.dmg"))

    def test_plan_blocks_home_and_protected_parents(self):
        paths = [str(HOME), str(HOME / "Library"), str(HOME / "Documents")]
        plan = tool_propose_cleanup_plan("t", [{"path": p, "size_bytes": 1} for p in paths])
        self.assertEqual(plan["blocked_count"], len(paths))
        for item in plan["items"]:
            self.assertTrue(item["is_protected"])
            self.assertFalse(item["is_safe"])

    def test_plan_blocks_parent_of_excluded_path(self):
        with unittest.mock.patch(
            "tidymac.ai.config.load_ai_config",
            return_value=unittest.mock.Mock(excluded_paths=["/tmp/tidymac-test/ws/projects"]),
        ):
            self.assertTrue(is_path_excluded("/tmp/tidymac-test/ws"))


@unittest.skipUnless(CASE_INSENSITIVE, "case-insensitive filesystem only")
class TestCaseVariants(unittest.TestCase):
    def test_case_variant_of_protected_path_is_not_removable(self):
        self.assertFalse(is_safe_to_remove(HOME / ".SSH"))
        self.assertFalse(is_safe_to_remove(HOME / "library/keychains"))

    def test_case_variant_of_excluded_path_is_excluded(self):
        self.assertTrue(is_path_excluded(HOME / "PROJECTS/app/node_modules"))


if __name__ == "__main__":
    unittest.main()
