import copy

import pytest

from sproutcode.agent import Agent
from sproutcode.client import AssistantTurn, ToolCall
from sproutcode.context import ContextManager
from sproutcode.tools import ToolRegistry


class FakeLLM:
    """与 LLMClient.chat 同接口的脚本化替身。"""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def chat(self, messages, tools, on_text=None):
        self.requests.append(
            {"messages": copy.deepcopy(messages), "tools": list(tools)}
        )
        if not self.turns:
            raise AssertionError("FakeLLM 脚本已耗尽")
        turn = self.turns.pop(0)
        if isinstance(turn, BaseException):
            raise turn
        if turn.content and on_text is not None:
            on_text(turn.content)
        return turn


def make_registry():
    reg = ToolRegistry()

    @reg.tool
    def echo(text: str) -> str:
        """原样返回文本。"""
        return text

    return reg


def test_agent_returns_text_without_tools():
    client = FakeLLM(AssistantTurn(content="你好"))
    agent = Agent(client, registry=make_registry())
    chunks = []

    reply = agent.run("hi", on_text=chunks.append)

    assert reply == "你好"
    assert chunks == ["你好"]
    assert agent.messages[-1] == {"role": "assistant", "content": "你好"}
    assert client.requests[0]["messages"][-1] == {"role": "user", "content": "hi"}


def test_agent_executes_tool_and_writes_matching_tool_message():
    call = ToolCall(id="call_1", name="echo", arguments='{"text": "ok"}')
    client = FakeLLM(
        AssistantTurn(content=None, tool_calls=[call]),
        AssistantTurn(content="完成"),
    )
    agent = Agent(client, registry=make_registry())
    events = []

    reply = agent.run(
        "do",
        on_tool_call=lambda name, arguments: events.append(
            ("call", name, arguments)
        ),
        on_tool_result=lambda content, is_error: events.append(
            ("result", content, is_error)
        ),
    )

    assert reply == "完成"
    assert events == [
        ("call", "echo", '{"text": "ok"}'),
        ("result", "ok", False),
    ]
    assert agent.messages[1] == {"role": "user", "content": "do"}
    assert agent.messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"text": "ok"}'},
            }
        ],
    }
    assert agent.messages[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "ok",
    }
    assert client.requests[1]["messages"][-1]["role"] == "tool"


def test_agent_returns_tool_errors_to_the_model():
    call = ToolCall(id="call_1", name="echo", arguments='{"wrong": 1}')
    client = FakeLLM(
        AssistantTurn(content=None, tool_calls=[call]),
        AssistantTurn(content="已看到错误"),
    )
    agent = Agent(client, registry=make_registry())

    reply = agent.run("do")

    assert reply == "已看到错误"
    assert agent.messages[-2]["role"] == "tool"
    assert "ERROR" in agent.messages[-2]["content"]


def test_agent_stops_at_max_rounds_and_keeps_history_consistent():
    call = ToolCall(id="call_1", name="echo", arguments='{"text": "x"}')
    looping = AssistantTurn(content=None, tool_calls=[call])
    client = FakeLLM(*([looping] * 3))
    agent = Agent(client, registry=make_registry(), max_rounds=3)
    chunks = []

    reply = agent.run("loop", on_text=chunks.append)

    assert "轮数上限" in reply
    assert chunks == [reply]
    assert len(client.requests) == 3
    assert sum(1 for message in agent.messages if message["role"] == "tool") == 3


def test_agent_rolls_back_the_whole_task_on_exception():
    client = FakeLLM(AssistantTurn(content="ok"), RuntimeError("boom"))
    agent = Agent(client, registry=make_registry())
    agent.run("first")
    before = copy.deepcopy(agent.messages)

    with pytest.raises(RuntimeError, match="boom"):
        agent.run("second")

    assert agent.messages == before


def test_agent_commits_exact_turn_slice_on_success():
    client = FakeLLM(AssistantTurn(content="一"))
    agent = Agent(client, registry=make_registry())
    committed = []

    agent.run("hi", on_turn_commit=committed.append)

    assert committed == [
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "一"},
        ]
    ]


def test_agent_does_not_commit_on_exception():
    client = FakeLLM(RuntimeError("boom"))
    agent = Agent(client, registry=make_registry())
    committed = []

    with pytest.raises(RuntimeError, match="boom"):
        agent.run("hi", on_turn_commit=committed.append)

    assert committed == []


def test_agent_compacts_at_run_start_with_toolless_summary_call():
    # 两次 run 各消耗「一次摘要 + 一次正常对话」,共 4 份脚本:
    # budget=10 下第一次 run 后历史仍超预算,第二次 run 起点会再压一次。
    client = FakeLLM(
        AssistantTurn(content="早期摘要"),
        AssistantTurn(content="好的"),
        AssistantTurn(content="再摘要"),
        AssistantTurn(content="好"),
    )
    agent = Agent(
        client,
        registry=make_registry(),
        context=ContextManager(budget=10, threshold=1.0, keep_recent=1),
    )
    # 预置一段超预算历史,让 run 起点必触发压缩
    agent.messages += [
        {"role": "user", "content": "第一轮" + "长" * 200},
        {"role": "assistant", "content": "完成" + "长" * 200},
        {"role": "user", "content": "第二轮" + "长" * 200},
        {"role": "assistant", "content": "完成" + "长" * 200},
    ]
    notes = []

    reply = agent.run("继续", on_compact=notes.append)

    assert reply == "好的"
    assert notes and "已压缩" in notes[0]
    assert client.requests[0]["tools"] == []        # 第一次调用是摘要,不带工具
    assert client.requests[1]["tools"]              # 第二次是正常对话,带工具
    assert agent.messages[1]["role"] == "user"      # system 之后第一条是摘要注入
    assert "早期摘要" in agent.messages[1]["content"]
    # 本轮提交的切片不含摘要(摘要只存在于视图,永不落盘);
    # 第二次 run 起点仍超预算会再压一次,但 task_start 在压缩之后,
    # 提交切片仍精确始于本轮 user 消息。
    committed = []
    agent.run("再继续", on_turn_commit=committed.append)
    assert committed[0][0] == {"role": "user", "content": "再继续"}


def test_agent_max_rounds_turn_still_commits():
    call = ToolCall(id="call_1", name="echo", arguments='{"text": "x"}')
    client = FakeLLM(*([AssistantTurn(content=None, tool_calls=[call])] * 3))
    agent = Agent(client, registry=make_registry(), max_rounds=3)
    committed = []

    agent.run("loop", on_turn_commit=committed.append)

    assert len(committed) == 1
    assert committed[0][0]["role"] == "user"
    assert sum(1 for m in committed[0] if m["role"] == "tool") == 3
