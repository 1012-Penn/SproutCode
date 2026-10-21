import json

import pytest

from sproutcode.sessions import Session, SessionStore

TOOL_TURN = [
    {"role": "user", "content": "建个文件"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "write_file", "arguments": '{"path": "a.txt"}'},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "content": "已写入 a.txt"},
    {"role": "assistant", "content": "完成"},
]


def test_new_session_writes_meta_line(tmp_path):
    store = SessionStore(tmp_path)

    session = store.new(model="deepseek-chat", cwd="/tmp/x")

    assert session.path == tmp_path / f"{session.meta.id}.jsonl"
    meta = json.loads(session.path.read_text(encoding="utf-8").splitlines()[0])
    assert meta["type"] == "meta"
    assert meta["id"] == session.meta.id
    assert meta["model"] == "deepseek-chat"
    assert meta["cwd"] == "/tmp/x"
    assert meta["created_at"]


def test_append_then_replay_roundtrip(tmp_path):
    store = SessionStore(tmp_path)
    session = store.new(model="m", cwd="/tmp")
    session.append_messages(TOOL_TURN)

    lines = session.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + len(TOOL_TURN)
    first = json.loads(lines[1])
    assert first["ts"]
    assert first["message"] == TOOL_TURN[0]

    # 新对象重放 == 新进程 resume:消息逐条等价
    assert Session(session.path, session.meta).load_messages() == TOOL_TURN


def test_open_by_prefix_across_store_instances(tmp_path):
    session = SessionStore(tmp_path).new(model="m", cwd="/tmp")
    session.append_messages(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]
    )

    reopened = SessionStore(tmp_path).open(session.meta.id[:8])

    assert reopened.meta.id == session.meta.id
    assert reopened.load_messages()[-1] == {"role": "assistant", "content": "ok"}


def test_open_missing_prefix_raises(tmp_path):
    with pytest.raises(ValueError, match="找不到会话"):
        SessionStore(tmp_path).open("nope")


def test_open_ambiguous_prefix_raises(tmp_path):
    for suffix in ("aaaaaa", "bbbbbb"):
        path = tmp_path / f"20260101-000000-{suffix}.jsonl"
        path.write_text(
            json.dumps({"type": "meta", "id": path.stem}) + "\n", encoding="utf-8"
        )

    with pytest.raises(ValueError, match="不唯一"):
        SessionStore(tmp_path).open("20260101")


def test_load_skips_corrupt_lines_with_warning(tmp_path, capsys):
    session = SessionStore(tmp_path).new(model="m", cwd="/tmp")
    session.append_messages(
        [{"role": "user", "content": "好"}, {"role": "assistant", "content": "的"}]
    )
    with open(session.path, "a", encoding="utf-8") as f:
        f.write('{"broken json\n')

    messages = session.load_messages()

    assert len(messages) == 2
    assert "跳过损坏行" in capsys.readouterr().err


def test_list_returns_summaries_newest_first(tmp_path):
    store = SessionStore(tmp_path)
    older = store.new(model="m", cwd="/tmp")
    older.append_messages(
        [{"role": "user", "content": "第一个会话的问题"}, {"role": "assistant", "content": "答"}]
    )
    newer = store.new(model="m", cwd="/tmp")
    newer.append_messages([{"role": "user", "content": "第二个"}])

    rows = store.list(limit=10)

    assert [row.id for row in rows] == [newer.meta.id, older.meta.id]
    assert rows[1].message_count == 2
    assert rows[1].preview == "第一个会话的问题"


def test_list_preview_truncates_long_first_message(tmp_path):
    store = SessionStore(tmp_path)
    session = store.new(model="m", cwd="/tmp")
    session.append_messages([{"role": "user", "content": "长" * 100}])

    row = store.list(limit=1)[0]

    assert row.preview.endswith("…")
    assert len(row.preview) == 41


def test_list_on_missing_dir_returns_empty(tmp_path):
    assert SessionStore(tmp_path / "never").list() == []
