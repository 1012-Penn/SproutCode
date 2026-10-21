"""上下文管理:token 估算 + 超预算时把旧消息块压缩成一段 LLM 摘要。

LLM 的上下文窗口有限(deepseek-chat 64K),而 Coding Agent 的工具结果
(读文件、跑命令)会快速膨胀历史。本模块的策略:每次 agent.run 开始前
估算当前历史的 token 量,超过预算阈值就把"除 system 和最近 N 个 user 轮
以外"的旧消息交给模型自己摘要,用摘要替换原文——完整原文仍留在会话
JSONL 文件里不动,压缩只影响内存视图。

与 agent.py 的协作:Agent.run 在处理新输入前调用 maybe_compact;
摘要调用本身走同一个 ChatClient 协议(不传工具,拿纯文本)。
"""

from __future__ import annotations

import json
import math
from typing import Any

from .client import ChatClient
from .config import (
    COMPACT_THRESHOLD,
    CONTEXT_BUDGET_TOKENS,
    KEEP_RECENT_TURNS,
    MAX_SUMMARY_MSG_CHARS,
)
from .prompts import load as load_prompt

# 近似规则(spec §4.4):CJK 字符 ≈ 1 token,其余 ≈ 4 字符/token。
# 不引 tokenizer 换零依赖,误差靠预算常量留余量来吸收。
_CJK_RANGES = ((0x4E00, 0x9FFF), (0x3040, 0x30FF), (0x3000, 0x303F), (0xFF00, 0xFFEF))


def _is_cjk(ch: str) -> bool:
    return any(start <= ord(ch) <= end for start, end in _CJK_RANGES)


def estimate_text_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if _is_cjk(ch))
    return cjk + math.ceil((len(text) - cjk) / 4)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """整条消息 json.dumps 后估算:role、键名、tool_calls 开销一并近似进去。

    为什么字符数近似就够用:这里只做"是否该压缩"的阈值判断,不是计费。
    近似会低估/高估一定比例,误差靠三层余量吸收——估算公式本身偏保守、
    预算常量比真实窗口小、阈值只设在 70%。就算偶尔估偏,最坏结果是
    早压或晚压一次,不会丢数据。
    """
    return sum(
        estimate_text_tokens(json.dumps(message, ensure_ascii=False))
        for message in messages
    )


def _render_for_summary(
    messages: list[dict[str, Any]], max_chars: int
) -> str:
    """旧消息块 → 摘要输入文本;单条截断,防摘要调用自己把上下文撑爆。"""
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "?")
        parts = [str(message.get("content") or "")]
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            parts.append(
                f'{function.get("name", "?")}({function.get("arguments", "")})'
            )
        body = " ".join(part for part in parts if part).strip() or "(空)"
        if len(body) > max_chars:
            body = body[:max_chars] + "…"
        lines.append(f"[{role}] {body}")
    return "\n".join(lines)


class ContextManager:
    """超过预算阈值时,把最旧消息块替换成一次 LLM 摘要调用。

    只改内存视图、不动会话文件:文件是完整事实,视图是发给模型的现状。
    切口选在倒数第 keep_recent 个 user 消息——user 轮边界天然不会
    拆散 assistant(tool_calls)/tool 配对。
    """

    def __init__(
        self,
        *,
        budget: int = CONTEXT_BUDGET_TOKENS,
        threshold: float = COMPACT_THRESHOLD,
        keep_recent: int = KEEP_RECENT_TURNS,
        max_summary_msg_chars: int = MAX_SUMMARY_MSG_CHARS,
    ) -> None:
        self.budget = budget
        self.threshold = threshold
        self.keep_recent = keep_recent
        self.max_summary_msg_chars = max_summary_msg_chars

    def maybe_compact(
        self, messages: list[dict[str, Any]], client: ChatClient
    ) -> tuple[list[dict[str, Any]], str | None]:
        """返回 (新 messages, 提示文本);不需要或无法压缩时原样返回。

        "无法压缩"的几种情况都返回原列表而不是报错:user 轮太少、旧块为空、
        摘要调用失败或返回空。压缩是优化项,任何失败都不该挡住正常对话。
        """
        # 阈值判断用乘法而不是先算上限常量:预算和阈值都是 config 常量,
        # 运行时不会变,但直接表达"超过预算的 70%"读起来最直白。
        if estimate_tokens(messages) <= self.budget * self.threshold:
            return messages, None

        # 压缩切口必须落在 user 消息边界,这是本模块最重要的一条不变式:
        # 一轮对话的形态是 user → assistant(带 tool_calls)→ tool → …,
        # 只有切在 user 前,被移走的旧块才不会把 assistant/tool 配对拆散到
        # 两侧——留在新历史里的第一条消息若是孤儿 tool 消息,API 直接 400。
        user_indexes = [
            i for i, m in enumerate(messages) if m.get("role") == "user"
        ]
        if len(user_indexes) <= self.keep_recent:
            return messages, None
        cut = user_indexes[-self.keep_recent]
        old_block = messages[1:cut]  # messages[0] 恒为 system,永远保留
        if not old_block:
            return messages, None

        prompt = (
            load_prompt("compact")
            + "\n\n=== 待压缩的早期对话 ===\n"
            + _render_for_summary(old_block, self.max_summary_msg_chars)
        )
        # 摘要调用不带工具(tools 传空),只要纯文本;即使模型意外返回
        # tool_calls,这里的 AssistantTurn 也不会被写进历史——只取 content。
        summary_turn = client.chat(
            [{"role": "user", "content": prompt}], [], on_text=None
        )
        summary = (summary_turn.content or "").strip()
        if not summary:
            return messages, None

        compacted = (
            [messages[0]]
            + [
                {
                    "role": "user",
                    "content": "(以下是本会话更早对话的摘要,供你延续上下文)\n"
                    + summary,
                }
            ]
            + messages[cut:]
        )
        note = (
            f"已压缩上下文:估算 {estimate_tokens(messages)}→"
            f"{estimate_tokens(compacted)} tokens"
        )
        return compacted, note
