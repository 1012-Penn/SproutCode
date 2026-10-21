"""Agent 核心:整个 Coding Agent 的"主循环"所在。

一个 Agent = 一份 messages 消息历史 + 一个 LLM 客户端 + 一套工具注册表。
它不关心模型怎么调(client.py)、工具怎么执行(tools/)、历史怎么存(sessions.py)、
上下文超没超预算(context.py),只负责把它们串成循环:

    用户输入 → 问模型 → 模型要调工具?→ 执行并把结果喂回去 → 再问模型 …
    直到模型给出纯文本回复为止。

这是整个项目里"协议细节"最密集的一层:OpenAI 的 tool calling 对消息历史有
严格的配对要求,这里用 task_start 回退机制来保证任何时刻历史都是合法的。
"""

from __future__ import annotations

from typing import Callable

from .client import ChatClient, TextCallback
from .config import MAX_ROUNDS
from .context import ContextManager
from .prompts import load as load_prompt
from .tools import ToolRegistry, registry as default_registry

ToolCallCallback = Callable[[str, str], None]
ToolResultCallback = Callable[[str, bool], None]
TurnCommitCallback = Callable[[list[dict]], None]
CompactCallback = Callable[[str], None]


class Agent:
    """持有消息历史并驱动 LLM↔工具循环;messages 就是 Agent 的全部状态。

    对新手来说关键的一点:这个类没有"记忆"之类的隐藏字段,所有上下文都
    显式放在 self.messages 里——所以会话保存/恢复、上下文压缩,都只是
    对这份列表做追加或重放,Agent 本身无需感知。
    """

    def __init__(
        self,
        client: ChatClient,
        *,
        registry: ToolRegistry | None = None,
        max_rounds: int = MAX_ROUNDS,
        system_prompt: str | None = None,
        context: ContextManager | None = None,
    ) -> None:
        self.client = client
        self.registry = registry or default_registry
        self.max_rounds = max_rounds
        # 提示词外置:默认读 prompts/system.txt,调提示词不用动代码。
        self.messages: list[dict] = [
            {
                "role": "system",
                "content": (
                    system_prompt
                    if system_prompt is not None
                    else load_prompt("system")
                ),
            }
        ]
        self.context = context or ContextManager()

    def run(
        self,
        user_input: str,
        *,
        on_text: TextCallback | None = None,
        on_tool_call: ToolCallCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
        on_turn_commit: TurnCommitCallback | None = None,
        on_compact: CompactCallback | None = None,
    ) -> str:
        """处理一条用户消息,直到模型不再发起工具调用。

        所有 on_* 参数都是可选回调,由 CLI 层用来做终端渲染和会话落盘——
        Agent 不直接 print 也不直接写文件,这让它在测试里可以完全无副作用。
        """

        # 先压缩再进本轮:resume 重放出的超长历史也在这里被收敛。
        # 压缩若失败,user 消息尚未入历史,不影响下方 task_start 的回退语义。
        self.messages, note = self.context.maybe_compact(
            self.messages, self.client
        )
        if note and on_compact is not None:
            on_compact(note)

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
                # 这是 OpenAI tool calling 的硬性协议:assistant 发起了 N 个调用,
                # 历史里就要跟 N 条 role=tool 且 tool_call_id 一一对应的消息,
                # 缺一条,下一次 API 请求就会直接 400。所以这里逐个执行、逐条回填。
                self.messages.append(turn.to_message())
                if not turn.tool_calls:
                    if on_turn_commit is not None:
                        on_turn_commit(self.messages[task_start:])
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

            # 轮数上限是保险丝:模型陷入"调工具→看结果→再调同一工具"的循环时,
            # 与其烧完 token,不如让用户把任务拆小。注意失败兜底文本也要走
            # on_turn_commit 落盘——会话文件里这段历史是完整合法的(每轮都有
            # tool 回复),所以不需要回退。
            fallback = "(已达单任务轮数上限,已停止。请把任务拆小后重试。)"
            if on_text is not None:
                on_text(fallback)
            if on_turn_commit is not None:
                on_turn_commit(self.messages[task_start:])
            return fallback
        except BaseException:
            # task_start 回退机制防的是"半截历史":如果本轮 user 已经入历史、
            # assistant 的 tool_calls 也入了,但某个工具结果还没回填就被中断
            # (Ctrl+C / 网络异常),残缺的 tool_call_id 会让下一次请求 400。
            # 直接把 messages 截回本轮开始前,等于这条用户输入从未发生过。
            # 捕获 BaseException 是因为 KeyboardInterrupt 也要走这条清理路径。
            del self.messages[task_start:]
            raise
