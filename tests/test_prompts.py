import pytest

from sproutcode.agent import Agent
from sproutcode.prompts import load


def test_load_system_prompt_nonempty_and_key_sentence():
    text = load("system")
    assert "SproutCode" in text
    assert "工具" in text
    assert text.strip() == text


def test_load_compact_prompt_mentions_summary():
    assert "摘要" in load("compact")


def test_load_missing_prompt_raises():
    with pytest.raises(FileNotFoundError):
        load("nope")


def test_agent_default_prompt_comes_from_external_file():
    agent = Agent(client=None)
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[0]["content"] == load("system")
