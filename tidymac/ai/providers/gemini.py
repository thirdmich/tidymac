"""Google Gemini provider via direct REST API with function declarations."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import BaseLLMProvider, ChatMessage, LLMResponse, ToolCall


class GeminiProvider(BaseLLMProvider):
    """Google Gemini API client using standard HTTP without third-party dependencies."""

    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash") -> None:
        self.api_key = api_key
        self.model = model

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        # Key goes in a header, not the query string, so it can't leak via logged URLs.
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

        contents = []
        system_text = ""

        for m in messages:
            if m.role == "system":
                system_text += m.content + "\n"
                continue

            role = "user" if m.role in ("user", "tool") else "model"
            parts: list[dict[str, Any]] = []

            if m.role == "tool":
                try:
                    resp_data = json.loads(m.content)
                except Exception:
                    resp_data = {"result": m.content}
                parts.append({
                    "functionResponse": {
                        "name": m.name or "tool_result",
                        "response": resp_data,
                    }
                })
            else:
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    part: dict[str, Any] = {
                        "functionCall": {
                            "name": tc.name,
                            "args": tc.arguments,
                        }
                    }
                    if tc.thought_signature:
                        part["thoughtSignature"] = tc.thought_signature
                    parts.append(part)

            if parts:
                contents.append({"role": role, "parts": parts})

        payload: dict[str, Any] = {"contents": contents}

        if system_text.strip():
            payload["systemInstruction"] = {
                "parts": [{"text": system_text.strip()}]
            }

        if tools:
            decls = []
            for t in tools:
                fn = t.get("function", {})
                params = fn.get("parameters", {})
                decls.append({
                    "name": fn.get("name"),
                    "description": fn.get("description"),
                    "parameters": params,
                })
            payload["tools"] = [{"functionDeclarations": decls}]

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8") if e.fp else ""
            raise RuntimeError(f"Gemini API returned error {e.code}: {err_body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Failed to reach Gemini API: {e}") from e

        candidates = data.get("candidates", [])
        if not candidates:
            return LLMResponse(content="No response received from Gemini.")

        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        parts = content_obj.get("parts", [])

        text_parts = []
        tool_calls: list[ToolCall] = []

        for i, p in enumerate(parts):
            if "text" in p:
                text_parts.append(p["text"])
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append(
                    ToolCall(
                        id=f"gemini_call_{i}",
                        name=fc.get("name", ""),
                        arguments=fc.get("args", {}),
                        thought_signature=p.get("thoughtSignature"),
                    )
                )

        return LLMResponse(content="\n".join(text_parts), tool_calls=tool_calls)
