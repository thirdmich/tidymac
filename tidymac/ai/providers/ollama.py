"""Ollama provider for local, 100% private LLM execution."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import BaseLLMProvider, ChatMessage, LLMResponse, ToolCall


class OllamaProvider(BaseLLMProvider):
    """Local Ollama client using HTTP API with native tool calling."""

    name = "ollama"

    def __init__(self, host: str = "http://localhost:11434", model: str = "llama3.2") -> None:
        self.host = host.rstrip("/")
        self.model = model

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        url = f"{self.host}/api/chat"

        # Format messages for Ollama
        api_messages = []
        for m in messages:
            msg_dict: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg_dict["tool_calls"] = [
                    {"function": {"name": tc.name, "arguments": tc.arguments}}
                    for tc in m.tool_calls
                ]
            api_messages.append(msg_dict)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Failed to connect to local Ollama server at {self.host}: {e}.\n"
                f"Ensure Ollama is running (`ollama serve`) and the model is pulled (`ollama pull {self.model}`)."
            ) from e

        msg = data.get("message", {})
        content = msg.get("content", "") or ""

        parsed_calls: list[ToolCall] = []
        raw_tool_calls = msg.get("tool_calls", [])
        for i, raw in enumerate(raw_tool_calls):
            fn = raw.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            parsed_calls.append(ToolCall(id=f"ollama_{i}", name=name, arguments=args))

        return LLMResponse(content=content, tool_calls=parsed_calls)
