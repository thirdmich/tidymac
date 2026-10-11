"""Cleaning ~/Library/Caches and honest reporting of what a plan actually did."""

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from rich.console import Console

from tidymac.ai.copilot import TidyCopilot
from tidymac.ai.providers.heuristic import HeuristicProvider
from tidymac.ai.tools import tool_propose_cleanup_plan
from tidymac.cleaner import is_safe_to_remove

CACHES = Path.home() / "Library/Caches"


@unittest.skipUnless(sys.platform == "darwin", "macOS cache layout")
class TestCachesContentsOnly(unittest.TestCase):
    """macOS forbids deleting ~/Library/Caches itself; only what's inside may go."""

    def test_caches_folder_itself_is_not_removable(self):
        self.assertFalse(is_safe_to_remove(CACHES))

    def test_items_inside_caches_are_removable(self):
        self.assertTrue(is_safe_to_remove(CACHES / "com.example.someapp"))

    def test_plan_targets_contents_of_caches(self):
        plan = tool_propose_cleanup_plan("t", [{"path": str(CACHES), "size_bytes": 10}])
        item = plan["items"][0]
        self.assertTrue(item["contents_only"])
        self.assertTrue(item["is_safe"])
        self.assertEqual(plan["blocked_count"], 0)


class TestPlanOutcomeReporting(unittest.TestCase):
    """The model must be told what actually happened, not just that the user said yes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "junk.bin"
        self.target.write_bytes(b"x")
        self.copilot = TidyCopilot(provider=HeuristicProvider(), console=Console(quiet=True))
        self.plan = {
            "title": "t",
            "items": [{"path": str(self.target), "size_bytes": 1, "is_safe": True}],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_failed_removal_is_reported_as_failed(self):
        with unittest.mock.patch("tidymac.ai.copilot.Confirm.ask", return_value=True), \
             unittest.mock.patch("tidymac.ai.copilot.remove_files", return_value=(0, 0)):
            outcome = self.copilot._render_plan(self.plan)
        self.assertTrue(outcome["approved"])
        self.assertEqual(outcome["removed_count"], 0)
        self.assertEqual(outcome["failed_count"], 1)
        self.assertEqual(outcome["freed_bytes"], 0)

    def test_declined_plan_reports_nothing_done(self):
        with unittest.mock.patch("tidymac.ai.copilot.Confirm.ask", return_value=False):
            outcome = self.copilot._render_plan(self.plan)
        self.assertFalse(outcome["approved"])
        self.assertEqual(outcome["freed_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
