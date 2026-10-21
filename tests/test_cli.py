from sproutcode.cli import Renderer, preview_arguments
from sproutcode.agent import Agent
from sproutcode.cli import (
    CliState,
    format_session_list,
    handle_slash,
    parse_args,
)
from sproutcode.prompts import load as load_prompt
from sproutcode.sessions import SessionStore


def test_preview_arguments_truncates_display_only():
    assert preview_arguments("short") == "short"
    assert preview_arguments("x" * 100) == "x" * 57 + "..."


def test_renderer_prefixes_stream_once_and_prints_newline(capsys):
    renderer = Renderer()
    renderer.on_text("你")
    renderer.on_text("好")

    assert renderer.finish() is True
    assert capsys.readouterr().out == "\n码苗> 你好\n"


def test_renderer_reports_no_text(capsys):
    renderer = Renderer()

    assert renderer.finish() is False
    assert capsys.readouterr().out == ""


class StubClient:
    def chat(self, messages, tools, on_text=None):
        raise AssertionError("斜杠命令不应触发 LLM 调用")


def make_state(tmp_path):
    store = SessionStore(tmp_path)
    session = store.new(model="m", cwd="/tmp")
    return CliState(Agent(StubClient()), session, store)


def test_parse_args_resume():
    assert parse_args(["--resume", "abc"]).resume == "abc"
    assert parse_args([]).resume is None


def test_handle_slash_help_lists_commands(tmp_path, capsys):
    state = make_state(tmp_path)

    assert handle_slash("/help", state) is True
    out = capsys.readouterr().out
    assert "/resume" in out and "/clear" in out


def test_handle_slash_clear_resets_and_starts_new_session(tmp_path, capsys):
    state = make_state(tmp_path)
    state.agent.messages.append({"role": "user", "content": "hi"})
    old = state.session
    old.append_messages([{"role": "user", "content": "hi"}])

    handle_slash("/clear", state)

    assert [m["role"] for m in state.agent.messages] == ["system"]
    assert state.agent.messages[0]["content"] == load_prompt("system")
    assert state.session.meta.id != old.meta.id
    assert old.path.exists()  # 旧会话文件保留,可事后 /resume 回去


def test_handle_slash_resume_lists_and_restores(tmp_path, capsys):
    state = make_state(tmp_path)
    state.session.append_messages(
        [{"role": "user", "content": "之前的问题"}, {"role": "assistant", "content": "回答"}]
    )
    target_id = state.session.meta.id

    handle_slash("/resume", state)
    out = capsys.readouterr().out
    assert target_id in out and "之前的问题" in out

    handle_slash(f"/resume {target_id[:8]}", state)
    assert [
        m["content"] for m in state.agent.messages[1:]
    ] == ["之前的问题", "回答"]


def test_handle_slash_resume_missing_id_reports_error(tmp_path, capsys):
    state = make_state(tmp_path)

    handle_slash("/resume nope", state)

    assert "resume 失败" in capsys.readouterr().out


def test_handle_slash_unknown_command_shows_hint(tmp_path, capsys):
    state = make_state(tmp_path)

    assert handle_slash("/whatever", state) is True
    assert "未知命令" in capsys.readouterr().out


def test_format_session_list_empty():
    assert format_session_list([]) == "(没有可恢复的会话)"
