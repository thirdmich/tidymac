"""Tests for the Gemini provider's request/response mapping (no network)."""

import io
import json
import unittest
import unittest.mock

from tidymac.ai.providers.base import ChatMessage
from tidymac.ai.providers.gemini import GeminiProvider


def _fake_response(payload: dict) -> unittest.mock.MagicMock:
    resp = unittest.mock.MagicMock()
    resp.__enter__.return_value = io.BytesIO(json.dumps(payload).encode("utf-8"))
    return resp


class TestGeminiThoughtSignatures(unittest.TestCase):
    """Gemini 3 rejects a follow-up request unless each functionCall's thoughtSignature is echoed back."""

    def test_signature_round_trips_on_function_call_part(self):
        provider = GeminiProvider(api_key="k", model="gemini-3.8-flash")
        first = {
            "candidates": [{"content": {"parts": [
                {"functionCall": {"name": "get_disk_overview", "args": {}}, "thoughtSignature": "SIG_A"},
                {"functionCall": {"name": "scan_clutter", "args": {}}},
            ]}}]
        }
        with unittest.mock.patch("urllib.request.urlopen", return_value=_fake_response(first)):
            reply = provider.chat([ChatMessage(role="user", content="clean safe caches")])

        history = [
            ChatMessage(role="user", content="clean safe caches"),
            ChatMessage(role="assistant", content=reply.content, tool_calls=reply.tool_calls),
            ChatMessage(role="tool", content="{}", name="get_disk_overview"),
            ChatMessage(role="tool", content="{}", name="scan_clutter"),
        ]
        final = {"candidates": [{"content": {"parts": [{"text": "done"}]}}]}
        with unittest.mock.patch(
            "urllib.request.urlopen", return_value=_fake_response(final)
        ) as urlopen:
            provider.chat(history)

        sent = json.loads(urlopen.call_args.args[0].data)
        model_parts = next(c for c in sent["contents"] if c["role"] == "model")["parts"]
        self.assertEqual(model_parts[0]["thoughtSignature"], "SIG_A")
        self.assertNotIn("thoughtSignature", model_parts[1])


if __name__ == "__main__":
    unittest.main()
