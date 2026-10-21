from sproutcode.client import AssistantTurn
from sproutcode.context import (
    ContextManager,
    estimate_text_tokens,
    estimate_tokens,
)


class SummaryFakeLLM:
    """记录请求、返回固定摘要文本的替身,只服务压缩测试。"""

    def __init__(self, summary: str = "早期:用户创建了 a.txt,内容是码苗 v2。") -> None:
        self.summary = summary
        self.requests = []

    def chat(self, messages, tools, on_text=None):
        self.requests.append(
            {"messages": [dict(m) for m in messages], "tools": list(tools)}
        )
        return AssistantTurn(content=self.summary)


def build_messages(turns: int = 6, filler: str = "数据" * 200) -> list[dict]:
    """够长的多轮对话:每轮 user/assistant 各一条,超预算但不至于跑不动。"""
    messages = [{"role": "system", "content": "sys"}]
    for i in range(turns):
        messages.append({"role": "user", "content": f"第{i}轮 {filler}"})
        messages.append({"role": "assistant", "content": f"完成{i} {filler}"})
    return messages


def test_estimate_text_tokens_cjk_vs_ascii():
    # 中文 ≈ 1 字符/token,英文 ≈ 4 字符/token:量级关系正确即可(spec §4.4)
    assert estimate_text_tokens("汉" * 100) == 100
    assert estimate_text_tokens("a" * 100) == 25
    assert estimate_text_tokens("a" * 99) == 25


def test_estimate_tokens_covers_tool_calls_payload():
    plain = [{"role": "user", "content": "hi"}]
    with_tools = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "a.txt"}',
                    },
                }
            ],
        }
    ]
    assert estimate_tokens(plain) > 0
    assert estimate_tokens(with_tools) > estimate_tokens(plain)


def test_maybe_compact_noop_under_threshold():
    client = SummaryFakeLLM()
    ctx = ContextManager(budget=10_000)
    messages = build_messages()

    result, note = ctx.maybe_compact(messages, client)

    assert result is messages
    assert note is None
    assert client.requests == []


def test_maybe_compact_replaces_old_block_and_keeps_recent_turns():
    client = SummaryFakeLLM()
    messages = build_messages(turns=6)
    ctx = ContextManager(budget=300, threshold=1.0, keep_recent=2)

    result, note = ctx.maybe_compact(messages, client)

    assert note and "已压缩" in note
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"
    assert "摘要" in result[1]["content"]
    assert "a.txt" in result[1]["content"]  # 摘要来自 LLM 返回
    assert [m["role"] for m in result[2:]] == ["user", "assistant", "user", "assistant"]
    assert result[2]["content"].startswith("第4轮")  # 保留的是最近 2 个 user 轮
    # 摘要调用:单条输入、不带任何工具
    request = client.requests[0]
    assert request["tools"] == []
    assert len(request["messages"]) == 1
    assert "待压缩的早期对话" in request["messages"][0]["content"]


def test_maybe_compact_never_splits_tool_call_pairs():
    client = SummaryFakeLLM()
    messages = build_messages(turns=4)
    messages.append({"role": "user", "content": "读一下"})
    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": "call_9", "content": "xx"})
    ctx = ContextManager(budget=300, threshold=1.0, keep_recent=1)

    result, _ = ctx.maybe_compact(messages, client)

    assert result[2]["role"] == "user"  # 保留区从 user 消息开始
    # 保留区内不存在悬空 tool 消息:每个 tool_call_id 都有配对的 assistant
    ids = {
        call["id"]
        for m in result
        if m.get("tool_calls")
        for call in m["tool_calls"]
    }
    assert "call_9" in ids
    assert all(
        m["tool_call_id"] in ids for m in result if m.get("role") == "tool"
    )


def test_maybe_compact_truncates_each_message_in_summary_input():
    client = SummaryFakeLLM()
    messages = build_messages(turns=6, filler="长" * 3000)
    ctx = ContextManager(
        budget=100, threshold=1.0, keep_recent=2, max_summary_msg_chars=100
    )

    _, note = ctx.maybe_compact(messages, client)

    assert note
    body = client.requests[0]["messages"][0]["content"]
    rendered = [
        line
        for line in body.splitlines()
        if line.startswith("[user]") or line.startswith("[assistant]")
    ]
    assert rendered
    assert all(len(line) <= len("[assistant] ") + 100 + 1 for line in rendered)


def test_maybe_compact_empty_summary_is_noop():
    client = SummaryFakeLLM(summary="   ")
    messages = build_messages(turns=6)
    ctx = ContextManager(budget=300, threshold=1.0, keep_recent=2)

    result, note = ctx.maybe_compact(messages, client)

    assert result is messages
    assert note is None


def test_maybe_compact_noop_when_no_old_block():
    client = SummaryFakeLLM()
    messages = [{"role": "system", "content": "s"}]
    for _ in range(3):
        messages += [
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]
    ctx = ContextManager(budget=1, threshold=1.0, keep_recent=4)

    result, note = ctx.maybe_compact(messages, client)

    assert result is messages  # user 轮数 ≤ keep_recent,没有旧块可摘
    assert note is None
    assert client.requests == []
