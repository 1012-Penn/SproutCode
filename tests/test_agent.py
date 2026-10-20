import copy

import pytest

from sproutcode.agent import Agent
from sproutcode.client import AssistantTurn, ToolCall
from sproutcode.tools import ToolRegistry


class FakeLLM:
    """与 LLMClient.chat 同接口的脚本化替身。"""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []

    def chat(self, messages, tools, on_text=None):
        self.requests.append(copy.deepcopy(messages))
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
    assert client.requests[0][-1] == {"role": "user", "content": "hi"}


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
    assert client.requests[1][-1]["role"] == "tool"


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
