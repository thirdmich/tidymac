"""OpenAI-compatible provider supporting OpenAI, Groq, OpenRouter, Mistral, and local vLLM."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import BaseLLMProvider, ChatMessage, LLMResponse, ToolCall


class OpenAICompatProvider(BaseLLMProvider):
    """OpenAI API and compatible endpoints with standard tool calling."""

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"

        api_messages = []
        for m in messages:
            msg_dict: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                msg_dict["tool_call_id"] = m.tool_call_id
            if m.name:
                msg_dict["name"] = m.name
            api_messages.append(msg_dict)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
        }
        if tools:
            payload["tools"] = tools

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(f"OpenAI API error {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to connect to OpenAI endpoint: {e}") from e

        choices = data.get("choices", [])
        if not choices:
            return LLMResponse(content="No response returned from model.")

        choice = choices[0]
        msg = choice.get("message", {})
        content = msg.get("content", "") or ""

        tool_calls: list[ToolCall] = []
        for raw_tc in msg.get("tool_calls", []):
            tc_id = raw_tc.get("id", "")
            fn = raw_tc.get("function", {})
            name = fn.get("name", "")
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except Exception:
                args = {}
            tool_calls.append(ToolCall(id=tc_id, name=name, arguments=args))

        return LLMResponse(content=content, tool_calls=tool_calls)
