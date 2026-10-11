"""Unit tests for TidyCopilot agent orchestration and heuristic intent handling."""

import unittest
from tidymac.ai.copilot import TidyCopilot
from tidymac.ai.providers.heuristic import HeuristicProvider
from tidymac.ai.providers.base import BaseLLMProvider, LLMResponse, ToolCall, ChatMessage


class MockLLMProvider(BaseLLMProvider):
    name = "mock"

    def __init__(self, responses: list[LLMResponse]):
        self.responses = responses
        self.call_count = 0

    def chat(self, messages, tools=None):
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
            self.call_count += 1
            return resp
        return LLMResponse(content="Done!")


class TestTidyCopilot(unittest.TestCase):
    def test_copilot_heuristic_disk_query(self):
        copilot = TidyCopilot(provider=HeuristicProvider())
        answer = copilot.run_turn("how much space is left on my disk?")
        self.assertIn("Storage Overview", answer)
        self.assertIn("Free:", answer)

    def test_copilot_mock_flow(self):
        mock_provider = MockLLMProvider([
            LLMResponse(
                content="Checking your disk...",
                tool_calls=[ToolCall(id="call_1", name="get_disk_overview", arguments={})],
            ),
            LLMResponse(content="You have plenty of space remaining!"),
        ])
        copilot = TidyCopilot(provider=mock_provider)
        final = copilot.run_turn("Check my storage status")
        self.assertEqual(final, "You have plenty of space remaining!")
        self.assertEqual(mock_provider.call_count, 2)


if __name__ == "__main__":
    unittest.main()
