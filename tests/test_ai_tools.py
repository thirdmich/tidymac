"""Unit tests for tidymac AI tools and safety guardrails."""

import unittest
from pathlib import Path

from tidymac.ai.tools import (
    execute_tool,
    tool_get_disk_overview,
    tool_propose_cleanup_plan,
)


class TestAITools(unittest.TestCase):
    def test_disk_overview_tool(self):
        result = tool_get_disk_overview()
        self.assertIn("total_bytes", result)
        self.assertIn("used_bytes", result)
        self.assertIn("free_bytes", result)
        self.assertIn("free_human", result)
        self.assertGreater(result["total_bytes"], 0)

    def test_propose_cleanup_plan_safety_block_tier1(self):
        """Verify that Tier 1 system paths are strictly BLOCKED."""
        protected_paths = [
            "/System/Library",
            "/usr/bin",
            "/Applications/Safari.app",
            str(Path.home() / "Library/Keychains"),
            str(Path.home() / ".ssh"),
            str(Path.home() / ".Trash"),
        ]

        items = [
            {
                "category": "Test",
                "path": p,
                "size_bytes": 1024 * 1024 * 50,
                "reason": "Test deletion",
            }
            for p in protected_paths
        ]

        plan = tool_propose_cleanup_plan("Protected Test Plan", items)

        self.assertEqual(plan["blocked_count"], len(protected_paths))
        for it in plan["items"]:
            self.assertTrue(it["is_protected"])
            self.assertFalse(it["is_safe"])
            self.assertIn("BLOCKED", it["reason"])

    def test_propose_cleanup_plan_safe_items(self):
        """Verify that harmless cache paths or CLI commands are marked safe."""
        items = [
            {
                "category": "Package Manager",
                "cleanup_command": "npm cache clean --force",
                "size_bytes": 1024 * 1024 * 100,
                "reason": "Clean npm cache",
            }
        ]
        plan = tool_propose_cleanup_plan("CLI Command Plan", items)
        self.assertEqual(plan["blocked_count"], 0)
        self.assertEqual(len(plan["items"]), 1)
        self.assertTrue(plan["items"][0]["is_safe"])

    def test_execute_tool_wrapper(self):
        res = execute_tool("get_disk_overview", {})
        self.assertNotIn("error", res)
        self.assertIn("free_human", res)

        bad_res = execute_tool("non_existent_tool", {})
        self.assertIn("error", bad_res)


if __name__ == "__main__":
    unittest.main()
