"""Provider factory for creating and resolving LLM instances."""

from __future__ import annotations

from typing import cast

from .base import BaseLLMProvider
from .heuristic import HeuristicProvider
from .ollama import OllamaProvider
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider
from .anthropic import AnthropicProvider
from ..config import AIConfig, get_active_provider, load_ai_config


def create_provider(
    name: str | None = None,
    cfg: AIConfig | None = None,
    model_override: str | None = None,
) -> BaseLLMProvider:
    """Instantiate and return the appropriate LLM provider."""
    if cfg is None:
        cfg = load_ai_config()

    if not name or name == "auto":
        name, _ = get_active_provider(cfg)

    name = name.lower().strip()

    if name == "ollama":
        model = model_override or cfg.ollama_model
        return OllamaProvider(host=cfg.ollama_host, model=model)

    if name == "gemini":
        if not cfg.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is not configured. Set it in env or run `tidymac ai config`.")
        model = model_override or cfg.gemini_model
        return GeminiProvider(api_key=cfg.gemini_api_key, model=model)

    if name == "openai":
        if not cfg.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not configured. Set it in env or run `tidymac ai config`.")
        model = model_override or cfg.openai_model
        return OpenAICompatProvider(
            api_key=cfg.openai_api_key,
            model=model,
            base_url=cfg.openai_base_url,
        )

    if name == "anthropic":
        if not cfg.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not configured. Set it in env or run `tidymac ai config`.")
        model = model_override or cfg.anthropic_model
        return AnthropicProvider(api_key=cfg.anthropic_api_key, model=model)

    # Default to zero-dependency heuristic
    return HeuristicProvider()
