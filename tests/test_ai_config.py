"""Unit tests for tidymac AI configuration and provider detection."""

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from tidymac.ai.config import (
    AIConfig,
    get_active_provider,
    load_ai_config,
    save_ai_config,
)


class TestAIConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "test_config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_default_config(self):
        cfg = AIConfig()
        self.assertEqual(cfg.provider, "auto")
        self.assertEqual(cfg.ollama_host, "http://localhost:11434")
        self.assertEqual(cfg.ollama_model, "llama3.2")
        self.assertEqual(cfg.gemini_model, "gemini-3.8-flash")

    def test_save_and_load_config(self):
        cfg = AIConfig(
            provider="gemini",
            gemini_api_key="test_key_123",
            gemini_model="gemini-3.8-flash",
        )
        saved_path = save_ai_config(cfg, path=self.config_path)
        self.assertTrue(saved_path.exists())

        loaded = load_ai_config(path=self.config_path)
        self.assertEqual(loaded.provider, "gemini")
        self.assertEqual(loaded.gemini_api_key, "test_key_123")
        self.assertEqual(loaded.gemini_model, "gemini-3.8-flash")

    def test_model_env_vars_override_config_file(self):
        save_ai_config(AIConfig(gemini_model="from-file"), path=self.config_path)
        overrides = {
            "GEMINI_MODEL": "gemini-env",
            "OPENAI_MODEL": "openai-env",
            "ANTHROPIC_MODEL": "anthropic-env",
        }
        with unittest.mock.patch.dict(os.environ, overrides):
            loaded = load_ai_config(path=self.config_path)
        self.assertEqual(loaded.gemini_model, "gemini-env")
        self.assertEqual(loaded.openai_model, "openai-env")
        self.assertEqual(loaded.anthropic_model, "anthropic-env")

    def test_get_active_provider_fallback(self):
        cfg = AIConfig(provider="auto", ollama_host="http://invalid.host:99999")
        provider, reason = get_active_provider(cfg)
        self.assertEqual(provider, "heuristic")
        self.assertIn("built-in intent engine", reason)

    def test_get_active_provider_explicit(self):
        cfg = AIConfig(provider="gemini", gemini_api_key="xyz")
        provider, reason = get_active_provider(cfg)
        self.assertEqual(provider, "gemini")


if __name__ == "__main__":
    unittest.main()
