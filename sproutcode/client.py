"""LLM 客户端:把 OpenAI SDK 的流式响应聚合成 Agent 能用的统一形态。

整个项目只依赖 openai 这一个第三方库,就集中用在这一层。Agent 不直接
碰 SDK——它只依赖下面定义的 ChatClient 协议(接口),测试时用不触网的
FakeLLM 平替。换模型/厂商只改 config.py 的 BASE_URL 和 MODEL 两个常量,
因为走的是 OpenAI 兼容协议。

本文件要重点理解两件事:
1. AssistantTurn 这个"统一形态"——把流式/非流式、真客户端/假客户端的
   差异全部收敛到同一个 dataclass,Agent 只认它;
2. collect_stream 的分片拼接——SSE 流式响应里 tool_calls 不是一次性给全的。
"""

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
        """转成可原样写回 messages 的 assistant 消息。

        注意 arguments 保持 JSON 字符串原样、不做 loads/dumps 往返:
        模型给什么就存什么,再次发送时才不会因为重新序列化引入差异。
        """
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
    """Agent 只依赖这一层接口,测试用 FakeLLM 平替,不触网。

    用 typing.Protocol(结构化类型)而不是抽象基类:LLMClient 没有显式
    继承它,只要方法签名长得一样就算实现了协议——依赖倒置不需要
    "为了接口而接口"的继承开销。
    """

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

    什么是 SSE 流式:服务端把一次响应拆成很多小 chunk,通过 HTTP 长连接
    逐个推送,每个 chunk 只有增量(delta)。文本部分是 Content 字符串碎片,
    可以直接边收边打印;但 tool_calls 的碎片结构完全不同——一次调用可能
    并行发生多个(用 index 区分是第几个),且每个调用的 id / name /
    arguments 都可能被拆到多个 chunk 里。所以这里用 slots 按 index 分槽:
    同一个 index 的碎片追加到同一个槽里,收完流再统一"出仓"。
    这是流式相对非流式最容易写错的地方。
    """
    text_parts: list[str] = []
    # dict[int, dict[str, str]]:槽下标(index)→ {id, name, arguments} 三个
    # 待拼接字段。不用 list 是因为 index 序号未必从 0 连续到达。
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

    # 出仓:按 index 排序保证调用顺序与模型发出的一致;个别厂商不给 id 时
    # 用槽位合成一个(call_0/call_1),arguments 空缺兜底成 "{}"——保证下游
    # 一定能配对、一定能 json.loads。
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
        # stream=True 是刻意选择:终端里逐字出现体验远好于干等十几秒。
        # 代价就是上面 collect_stream 要处理的分片拼接复杂度。
        # 没有工具时不传 tools 字段——部分兼容端点收到空 tools 数组会报错。
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request["tools"] = tools
        stream = self._sdk.chat.completions.create(**request)
        return collect_stream(stream, on_text)
