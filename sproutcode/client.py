from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from openai import OpenAI

from .config import BASE_URL, MODEL

TextCallback = Callable[[str], None]


@dataclass
class ToolCall:
    """聚合完成的一次工具调用。"""

    id: str
    name: str
    arguments: str


@dataclass
class AssistantTurn:
    """一轮模型响应的统一形态:真实客户端与 FakeLLM 都返回它。"""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_message(self) -> dict[str, Any]:
        """转成可原样写回 messages 的 assistant 消息。"""
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class ChatClient(Protocol):
    """Agent 只依赖这一层接口,测试用 FakeLLM 平替,不触网。"""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: TextCallback | None = None,
    ) -> AssistantTurn: ...


def collect_stream(
    chunks: Iterable[Any], on_text: TextCallback | None = None
) -> AssistantTurn:
    """把 SSE 增量聚合成一轮完整响应。

    文本 delta 直接回调给渲染层;tool_calls 按 index 分槽,id/name/arguments
    都可能被拆到多个 chunk,必须逐段拼接——这是流式相对非流式最容易写错的地方。
    """
    text_parts: list[str] = []
    slots: dict[int, dict[str, str]] = {}

    for chunk in chunks:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = choices[0].delta

        content = getattr(delta, "content", None)
        if content:
            text_parts.append(content)
            if on_text is not None:
                on_text(content)

        for call_delta in getattr(delta, "tool_calls", None) or []:
            index = getattr(call_delta, "index", 0) or 0
            slot = slots.setdefault(index, {"id": "", "name": "", "arguments": ""})

            call_id = getattr(call_delta, "id", None)
            if call_id and not slot["id"]:
                slot["id"] = call_id

            function = getattr(call_delta, "function", None)
            if function is not None:
                name = getattr(function, "name", None)
                arguments = getattr(function, "arguments", None)
                if name:
                    slot["name"] += name
                if arguments:
                    slot["arguments"] += arguments

    tool_calls = [
        ToolCall(
            id=slot["id"] or f"call_{index}",
            name=slot["name"],
            arguments=slot["arguments"] or "{}",
        )
        for index, slot in sorted(slots.items())
    ]
    return AssistantTurn(content="".join(text_parts) or None, tool_calls=tool_calls)


class LLMClient:
    """OpenAI 兼容流式客户端;换模型只改 config 里的两个常量。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        model: str = MODEL,
        sdk: Any | None = None,
    ) -> None:
        self.model = model
        self._sdk = sdk or OpenAI(api_key=api_key, base_url=base_url, max_retries=3)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: TextCallback | None = None,
    ) -> AssistantTurn:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request["tools"] = tools
        stream = self._sdk.chat.completions.create(**request)
        return collect_stream(stream, on_text)
