"""Anthropic Claude provider via Messages API with tool use."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import BaseLLMProvider, ChatMessage, LLMResponse, ToolCall


class AnthropicProvider(BaseLLMProvider):
    """Anthropic Claude client using HTTP API."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-3-5-haiku-20241022") -> None:
        self.api_key = api_key
        self.model = model

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        url = "https://api.anthropic.com/v1/messages"

        system_text = ""
        api_messages: list[dict[str, Any]] = []

        for m in messages:
            if m.role == "system":
                system_text += m.content + "\n"
                continue

            if m.role == "tool":
                api_messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id or "tool_0",
                            "content": m.content,
                        }
                    ],
                })
            elif m.role == "assistant":
                content_blocks: list[dict[str, Any]] = []
                if m.content:
                    content_blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                api_messages.append({"role": "assistant", "content": content_blocks})
            else:
                api_messages.append({"role": "user", "content": m.content})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": 4096,
        }
        if system_text.strip():
            payload["system"] = system_text.strip()

        if tools:
            anthropic_tools = []
            for t in tools:
                fn = t.get("function", {})
                anthropic_tools.append({
                    "name": fn.get("name"),
                    "description": fn.get("description"),
                    "input_schema": fn.get("parameters", {}),
                })
            payload["tools"] = anthropic_tools

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(f"Anthropic API error {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to connect to Anthropic API: {e}") from e

        content_blocks = data.get("content", [])
        text_parts = []
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}),
                    )
                )

        return LLMResponse(content="\n".join(text_parts), tool_calls=tool_calls)
