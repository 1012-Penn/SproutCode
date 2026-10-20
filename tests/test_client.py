from types import SimpleNamespace

from sproutcode.client import collect_stream


def _chunk(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def _tool_delta(index, *, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_collect_stream_emits_text_deltas_and_builds_message():
    seen = []
    turn = collect_stream([_chunk("你"), _chunk("好")], on_text=seen.append)

    assert turn.content == "你好"
    assert turn.tool_calls == []
    assert seen == ["你", "好"]
    assert turn.to_message() == {"role": "assistant", "content": "你好"}


def test_collect_stream_aggregates_tool_call_fragments_by_index():
    chunks = [
        _chunk(
            tool_calls=[
                _tool_delta(0, id="call_1", name="read_", arguments='{"pa')
            ]
        ),
        _chunk(tool_calls=[_tool_delta(0, name="file", arguments='th": "a.txt"}')]),
        _chunk(
            tool_calls=[
                _tool_delta(
                    1, id="call_2", name="run_shell", arguments='{"command": "ls"}'
                )
            ]
        ),
    ]

    turn = collect_stream(chunks)

    assert turn.content is None
    assert [(call.id, call.name, call.arguments) for call in turn.tool_calls] == [
        ("call_1", "read_file", '{"path": "a.txt"}'),
        ("call_2", "run_shell", '{"command": "ls"}'),
    ]
    assert turn.to_message() == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'},
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "run_shell",
                    "arguments": '{"command": "ls"}',
                },
            },
        ],
    }


def test_collect_stream_ignores_chunks_without_choices():
    empty = SimpleNamespace(choices=[])
    turn = collect_stream([empty, _chunk("hi")])
    assert turn.content == "hi"
