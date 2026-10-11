"""Configuration management for tidymac AI agents and LLM providers."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _get_default_config_path() -> Path:
    """Get platform-appropriate config file path."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "tidymac" / "ai_config.json"


@dataclass
class AIConfig:
    provider: str = "auto"  # 'auto', 'ollama', 'gemini', 'openai', 'anthropic', 'heuristic'
    # Ollama settings (default local, 100% private)
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    # Gemini settings
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    # OpenAI settings (also works with compatible servers like Groq, vLLM, OpenRouter)
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    # Anthropic settings
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-5-haiku-20241022"
    # Excluded directories (never scanned or modified)
    excluded_paths: list[str] = field(default_factory=lambda: [str(Path.home() / "projects")])


def load_ai_config(path: Path | None = None) -> AIConfig:
    """Load configuration from disk, falling back to defaults."""
    cfg_path = path or _get_default_config_path()
    cfg = AIConfig()

    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            for key, val in data.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, val)
        except Exception:
            pass

    # Ensure ~/projects is protected
    projects_dir = str(Path.home() / "projects")
    if projects_dir not in cfg.excluded_paths:
        cfg.excluded_paths.append(projects_dir)


    # Environment variables override config file
    if os.environ.get("TIDYMAC_AI_PROVIDER"):
        cfg.provider = os.environ["TIDYMAC_AI_PROVIDER"]
    if os.environ.get("OLLAMA_HOST"):
        cfg.ollama_host = os.environ["OLLAMA_HOST"]
    if os.environ.get("OLLAMA_MODEL"):
        cfg.ollama_model = os.environ["OLLAMA_MODEL"]
    if os.environ.get("GEMINI_API_KEY"):
        cfg.gemini_api_key = os.environ["GEMINI_API_KEY"]
    if os.environ.get("GEMINI_MODEL"):
        cfg.gemini_model = os.environ["GEMINI_MODEL"]
    if os.environ.get("OPENAI_API_KEY"):
        cfg.openai_api_key = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_MODEL"):
        cfg.openai_model = os.environ["OPENAI_MODEL"]
    if os.environ.get("OPENAI_BASE_URL"):
        cfg.openai_base_url = os.environ["OPENAI_BASE_URL"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        cfg.anthropic_api_key = os.environ["ANTHROPIC_API_KEY"]
    if os.environ.get("ANTHROPIC_MODEL"):
        cfg.anthropic_model = os.environ["ANTHROPIC_MODEL"]

    return cfg


def save_ai_config(cfg: AIConfig, path: Path | None = None) -> Path:
    """Save configuration to disk."""
    cfg_path = path or _get_default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
    return cfg_path


def is_ollama_online(host: str = "http://localhost:11434") -> bool:
    """Check if local Ollama server is running and reachable."""
    try:
        url = f"{host.rstrip('/')}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_active_provider(cfg: AIConfig | None = None) -> tuple[str, str]:
    """Determine the active provider and reason.
    Returns (provider_name, explanation).
    """
    if cfg is None:
        cfg = load_ai_config()

    pref = cfg.provider.lower().strip()

    if pref != "auto":
        return pref, f"Configured explicitly in settings ({pref})"

    # Auto-detection priority:
    # 1. Local Ollama if online (fast, zero cost, 100% private)
    if is_ollama_online(cfg.ollama_host):
        return "ollama", f"Local Ollama detected at {cfg.ollama_host}"

    # 2. Cloud API keys if set
    if cfg.gemini_api_key:
        return "gemini", "Gemini API key found"
    if cfg.openai_api_key:
        return "openai", "OpenAI API key found"
    if cfg.anthropic_api_key:
        return "anthropic", "Anthropic API key found"

    # 3. Fallback: Heuristic rule-based agent
    return "heuristic", "No LLM server or API key detected; using built-in intent engine"
