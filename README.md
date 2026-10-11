# tidymac

> **Safe, intelligent disk cleanup and storage optimization for Mac and Windows.**  
> Built by FusionICT · https://fusionict.com

`tidymac` analyzes your filesystem to locate reclaimable space, large files, duplicate files, stale developer workspaces, and system clutter. All file removals are routed to the OS Trash/Recycle Bin or safely backed up to an external drive.

---

## What's New: TidyCopilot AI Agent (`tidymac ai`)

`tidymac` includes **TidyCopilot**, an autonomous AI agent capable of multi-step analysis, plan synthesis, and execution directly from conversational natural language prompts.

### Key Capabilities

- **Zero-Config Instant Offline Mode:** Operates immediately using the built-in heuristic intent parser with zero API keys or external services required.
- **Pluggable Local & Cloud LLMs:** Connects out of the box with:
  - **Ollama** (100% private on-device execution with `llama3.2`, `mistral`, `qwen2.5`, etc.)
  - **Google Gemini** (`gemini-3.8-flash`)
  - **OpenAI & Compatible APIs** (OpenAI `gpt-4o-mini`, Groq, OpenRouter, vLLM, LM Studio)
  - **Anthropic Claude** (`claude-3-5-haiku`, `claude-3-5-sonnet`)
- **Strict Tier 1/2 Safety Guardrails:** System files (`/System`, `/usr`, `/Applications`, `~/.ssh`, `~/Library/Keychains`) are blocked at the tool boundary. Package manager caches (npm, pnpm, yarn, go) are cleaned exclusively via validated CLI commands to prevent index corruption.
- **Interactive Review:** All cleanup proposals are presented with itemized Rich tables and require explicit confirmation before execution.

---

## Installation

```bash
# Clone and install in virtual environment
git clone https://github.com/thirdmich/tidymac.git
cd tidymac
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

---

## AI Copilot Usage

### 1. Direct Natural Language Prompts

```bash
# Inspect overall disk usage
tidymac ai "how much disk space do I have left?"

# Clean system caches and logs
tidymac ai "clean system clutter and caches"

# Reclaim a specific amount of space
tidymac ai "free up 10GB of storage"

# Find disk hogs
tidymac ai "find large files"

# Locate duplicates in Downloads
tidymac ai "scan for duplicate files"

# Clean stale developer project dependencies
tidymac ai "clean stale node_modules"
```

### 2. Interactive Terminal Chat Mode

Start an ongoing conversational session:

```bash
tidymac ai --chat
# or simply:
tidymac ai
```

### 3. External Drive Backup Mode

Move candidates to an external volume instead of Trash:

```bash
tidymac ai --backup "free up 20GB of large files"
```

### 4. AI Provider Configuration & Status

```bash
# Check current AI status and connectivity
tidymac ai status

# View configuration
tidymac ai config

# Configure Ollama model
tidymac ai config --ollama-model llama3.2

# Configure Google Gemini
tidymac ai config --gemini-key YOUR_GEMINI_API_KEY --provider gemini

# Configure OpenAI
tidymac ai config --openai-key YOUR_OPENAI_API_KEY --provider openai

# Configure Anthropic
tidymac ai config --anthropic-key YOUR_ANTHROPIC_API_KEY --provider anthropic

# Reset to automatic provider detection
tidymac ai config --provider auto
```

Environment variables override the saved config: `TIDYMAC_AI_PROVIDER`, `OLLAMA_HOST`,
`OLLAMA_MODEL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
`OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`. Use the model variables to
switch models without editing code, e.g. when a provider retires one.

---

## Standard CLI Commands

In addition to the AI Copilot, standard deterministic CLI commands are available:

- `tidymac scan` — Show disk overview and quick clutter summary.
- `tidymac media` — Scan videos, photos, screen recordings, screenshots, and duplicates (with `--backup` support).
- `tidymac large` — Find files over a given threshold (e.g. `--min-size 500MB`).
- `tidymac dupes` — Find duplicate files using 3-phase hash verification.
- `tidymac clutter` — Clean system caches, Xcode derived data, package caches, logs.
- `tidymac offload` — Verify and archive large files to an external drive.
- `tidymac all` — Guided interactive cleanup wizard scanning all categories.


---

## Safety Guarantees

Read [`SAFETY.md`](SAFETY.md) for full details on safety policies.

- **Tier 1 (Non-negotiable blocks):** OS binaries, application bundles, cryptographic keychains, SSH configurations, and system volume roots cannot be deleted.
- **Tier 2 (User data protection):** User documents, downloads, and media files are flagged with `CAUTION` and are never automatically selected for deletion.
- **Safe Package Management:** Store paths for `npm`, `pnpm`, `yarn`, and `go` are cleared using CLI prune commands (`npm cache clean --force`, `pnpm store prune`) rather than destructive filesystem deletion.

---

## Running Tests

```bash
.venv/bin/python -m unittest discover tests
```
