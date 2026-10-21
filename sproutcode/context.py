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
    """整条消息 json.dumps 后估算:role、键名、tool_calls 开销一并近似进去。"""
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
        """返回 (新 messages, 提示文本);不需要或无法压缩时原样返回。"""
        if estimate_tokens(messages) <= self.budget * self.threshold:
            return messages, None

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
