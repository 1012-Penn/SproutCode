from sproutcode.cli import Renderer, preview_arguments


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
