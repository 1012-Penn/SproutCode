from __future__ import annotations

from typing import Callable

from .client import ChatClient, TextCallback
from .config import MAX_ROUNDS, SYSTEM_PROMPT
from .tools import ToolRegistry, registry as default_registry

ToolCallCallback = Callable[[str, str], None]
ToolResultCallback = Callable[[str, bool], None]


class Agent:
    """持有消息历史并驱动 LLM↔工具循环;messages 就是 Agent 的全部状态。"""

    def __init__(
        self,
        client: ChatClient,
        *,
        registry: ToolRegistry | None = None,
        max_rounds: int = MAX_ROUNDS,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.client = client
        self.registry = registry or default_registry
        self.max_rounds = max_rounds
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def run(
        self,
        user_input: str,
        *,
        on_text: TextCallback | None = None,
        on_tool_call: ToolCallCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
    ) -> str:
        """处理一条用户消息,直到模型不再发起工具调用。"""
        # 记录本轮起点:失败/中断时把本轮 user 与半截 tool_calls 一起回退,
        # 否则残缺的 tool_call_id 会让下一次 API 请求直接 400。
        task_start = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})
        try:
            for _ in range(self.max_rounds):
                turn = self.client.chat(
                    self.messages,
                    self.registry.schemas(),
                    on_text=on_text,
                )
                # 带 tool_calls 的 assistant 消息原样入历史,每个 call 都必须有 tool 回复。
                self.messages.append(turn.to_message())
                if not turn.tool_calls:
                    return turn.content or ""

                for call in turn.tool_calls:
                    if on_tool_call is not None:
                        on_tool_call(call.name, call.arguments)
                    content, is_error = self.registry.execute(
                        call.name, call.arguments
                    )
                    if on_tool_result is not None:
                        on_tool_result(content, is_error)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": content,
                        }
                    )

            fallback = "(已达单任务轮数上限,已停止。请把任务拆小后重试。)"
            if on_text is not None:
                on_text(fallback)
            return fallback
        except BaseException:
            del self.messages[task_start:]
            raise
