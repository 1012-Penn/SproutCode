import json

import pytest

from sproutcode.tools import ToolRegistry, registry


def test_builtin_registry_exposes_schemas_generated_from_signatures():
    by_name = {
        schema["function"]["name"]: schema["function"]
        for schema in registry.schemas()
    }

    assert set(by_name) == {"read_file", "write_file", "list_files", "run_shell"}
    read = by_name["read_file"]
    assert read["description"] == "读取指定路径文本文件的内容,返回全文。"
    assert read["parameters"]["required"] == ["path"]
    assert read["parameters"]["properties"]["path"] == {
        "type": "string",
        "description": "文件路径。",
    }
    assert by_name["list_files"]["parameters"].get("required", []) == []


def test_tool_decorator_generates_defaults_and_supported_types():
    reg = ToolRegistry()

    @reg.tool
    def stats(values: list[int], verbose: bool = False) -> str:
        """统计参数。

        Args:
            values: 整数列表。
            verbose: 是否输出详情。
        """
        return f"{len(values)}:{verbose}"

    schema = reg.schemas()[0]["function"]
    assert schema["name"] == "stats"
    assert schema["description"] == "统计参数。"
    assert schema["parameters"] == {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "整数列表。",
            },
            "verbose": {"type": "boolean", "description": "是否输出详情。"},
        },
        "required": ["values"],
    }


def test_tool_decorator_rejects_missing_docstring():
    reg = ToolRegistry()

    with pytest.raises(ValueError, match="docstring"):

        @reg.tool
        def undocumented(value: str) -> str:
            return value


def test_execute_returns_content_and_error_flag():
    reg = ToolRegistry()

    @reg.tool
    def add(a: int, b: int) -> int:
        """相加。"""
        return a + b

    assert reg.execute("add", '{"a": 2, "b": 3}') == ("5", False)


def test_execute_unknown_tool_and_bad_json_are_error_text():
    content, is_error = registry.execute("nope", "{}")
    assert is_error and "未知工具" in content

    content, is_error = registry.execute("read_file", "not-json")
    assert is_error and "JSONDecodeError" in content


def test_execute_exception_is_captured_as_error_text():
    reg = ToolRegistry()

    @reg.tool
    def boom() -> str:
        """总是失败。"""
        raise ValueError("炸了")

    content, is_error = reg.execute("boom", "{}")
    assert is_error
    assert "ValueError" in content and "炸了" in content


def test_write_read_list_roundtrip(tmp_path):
    target = tmp_path / "nested" / "hello.txt"
    payload = json.dumps({"path": str(target), "content": "码苗"})

    content, is_error = registry.execute("write_file", payload)
    assert not is_error and target.read_text(encoding="utf-8") == "码苗"

    assert registry.execute(
        "read_file", json.dumps({"path": str(target)})
    ) == ("码苗", False)

    (tmp_path / "top.txt").write_text("top", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    listed, is_error = registry.execute(
        "list_files", json.dumps({"path": str(tmp_path)})
    )
    assert not is_error
    assert set(listed.splitlines()) == {"nested/", "sub/", "top.txt"}


def test_run_shell_nonzero_exit_is_not_an_error():
    content, is_error = registry.execute("run_shell", '{"command": "exit 3"}')
    assert not is_error
    assert "[exit 3]" in content


def test_read_missing_file_is_error_text():
    content, is_error = registry.execute(
        "read_file", json.dumps({"path": "/definitely/not/here.txt"})
    )
    assert is_error and "ERROR" in content
