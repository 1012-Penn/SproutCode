# SproutCode v1 实现计划:模块化 + 流式 + @tool + pytest

> **执行方式(子代理分工):** 机械性工作交给通用子智能体(general-purpose)执行——照本计划逐字写入包代码与测试、跑 pytest 自检、回报全部输出;主会话负责环境准备、对照计划逐行审查 diff、真机验收门禁、git 提交与 tag。子代理不执行 git 命令。

**Goal:** 把 v0 的 195 行单文件拆成 `sproutcode/` 包,加入流式输出、`@tool` 装饰器自动生成 JSON Schema、pytest + FakeLLM 测试基建;行为与 v0 等价,通过真机验收后打 `v1` tag 并写演进笔记。

**Architecture:** `config` 管常量与 Key;`client` 把 OpenAI 流式 chunk 聚合为统一的 `AssistantTurn`;`agent` 持有 messages 并驱动 LLM↔工具循环;`tools` 用可实例化的 `ToolRegistry` 存放「函数 + 自动生成的 Schema」;`cli` 只负责 REPL 与 ANSI 渲染。FakeLLM 与真实客户端实现同一个 `chat()` 接口,测试不触网。

**Tech Stack:** Python 3.10.12、openai SDK(唯一运行时依赖)、pytest(仅开发依赖)、DeepSeek `deepseek-chat`。

**Spec:** `docs/superpowers/specs/2026-10-20-sproutcode-design.md` §2 技术选型、§3 v1 行、§4.1 包结构、§4.2 流式循环、§4.3 工具协议、§5 错误处理、§6 测试策略。

## Global Constraints

- 语言 Python 3.10+,运行时依赖仅 `openai` SDK,CLI 只用标准库
- LLM 走 OpenAI 兼容协议:base_url `https://api.deepseek.com`,模型 `deepseek-chat`;Key 从环境变量或 `/root/.env` 的 `DEEPSEEK_API_KEY` 读取,永不入库
- 单任务工具调用轮数上限 25;工具结果统一 `(content, is_error)`,异常不向上抛、转错误文本回传模型
- 带 `tool_calls` 的 assistant 消息必须原样入历史,每个 `tool_call_id` 都要有对应的 `role=tool` 回复;任务中断/失败必须把 messages 回退到本轮 user 消息之前
- 功能与 v0 等价;工具注册表单测通过;流式文本与工具调用过程可视化
- 周期纪律:实现 → 测试 → 真机验收 → tag → 演进笔记,不提前做 v2 的会话/上下文

## 文件结构(v1 终态)

```
sproutcode/
├── __init__.py          # 版本号
├── __main__.py          # python -m sproutcode 入口
├── cli.py               # REPL、流式渲染、工具轨迹
├── agent.py             # Agent 类:消息历史 + 工具循环 + 本轮回退
├── client.py            # LLMClient + SSE 聚合 + AssistantTurn/ToolCall/ChatClient
├── config.py            # 常量、SYSTEM_PROMPT、load_api_key
└── tools/
    ├── __init__.py      # ToolRegistry + @tool + Schema 生成 + 内置工具导入
    ├── files.py         # read_file / write_file / list_files
    └── shell.py         # run_shell
tests/
├── test_config.py
├── test_client.py
├── test_tools.py
├── test_agent.py
└── test_cli.py
pyproject.toml
```

根目录 `sproutcode.py` 删除:包目录与同名 .py 文件会撞名,入口统一改为 `python -m sproutcode`(v0 的实现保留在 tag `v0` 历史里)。

---

### Task 1: 计划、pyproject 与 pytest 环境(主会话执行)

**Files:**
- Create: `docs/superpowers/plans/2026-10-20-v1-modular-streaming-pytest.md`
- Create: `pyproject.toml`

- [ ] **Step 1: 保存本计划全文到上述路径**

- [ ] **Step 2: 写入 `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "sproutcode"
version = "0.1.0"
description = "SproutCode(码苗):从零实现的终端 Coding Agent"
requires-python = ">=3.10"
dependencies = ["openai"]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
sproutcode = "sproutcode.cli:main"

[tool.setuptools.packages.find]
include = ["sproutcode*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 3: 安装 pytest 并验证**

Run: `.venv/bin/pip install "pytest>=8.0"`
Run: `.venv/bin/python -m pytest --version`
Expected: 输出版本号(如 `pytest 8.x.x`)

- [ ] **Step 4: Commit(主会话)**

```bash
git add docs/superpowers/plans/2026-10-20-v1-modular-streaming-pytest.md pyproject.toml
git commit -m "docs: v1 实现计划与 pytest 基建"
```

---

### Task 2: config + 流式 client【子代理写文件,主会话审查提交】

**Files:**
- Create: `sproutcode/__init__.py`
- Create: `sproutcode/config.py`
- Create: `sproutcode/client.py`
- Create: `tests/test_config.py`
- Create: `tests/test_client.py`
- Delete: `sproutcode.py`(根目录旧入口)

**Interfaces:**
- Produces: `load_api_key() -> str | None`;`ToolCall(id, name, arguments)`;`AssistantTurn(content, tool_calls).to_message() -> dict`;`collect_stream(chunks, on_text=None) -> AssistantTurn`;`LLMClient(api_key, *, base_url, model, sdk).chat(messages, tools, on_text=None) -> AssistantTurn`;`ChatClient` Protocol

- [ ] **Step 1: 先写测试 `tests/test_config.py`**

```python
from sproutcode.config import load_api_key


def test_load_api_key_prefers_environment(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-env")
    assert load_api_key() == "from-env"


def test_load_api_key_falls_back_to_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('DEEPSEEK_API_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.setattr("sproutcode.config.ENV_FILE", str(env_file))
    assert load_api_key() == "from-file"
```

- [ ] **Step 2: 先写测试 `tests/test_client.py`**

```python
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
```

- [ ] **Step 3: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_client.py -v`
Expected: FAIL,报 `ModuleNotFoundError: No module named 'sproutcode'`

- [ ] **Step 4: 删除根目录旧入口**

Run: `git rm sproutcode.py`
Expected: 文件从工作区删除(历史仍保留在 tag `v0`)

- [ ] **Step 5: 写入 `sproutcode/__init__.py`**

```python
"""SproutCode(码苗):终端 Coding Agent。"""

__version__ = "0.1.0"
```

- [ ] **Step 6: 写入 `sproutcode/config.py`**

```python
from __future__ import annotations

import os

# 所有"改一行就能换环境"的东西集中在这里:换模型/厂商只改 BASE_URL 和 MODEL,
# 这是坚持 OpenAI 兼容协议换来的可移植性。
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"
MAX_ROUNDS = 25           # 单任务工具调用轮数上限,防失控(保险丝,不是常态工况)
SHELL_TIMEOUT = 60        # 秒;v1 沿用 v0,正式的超时/截断策略是 v3 的主题
ENV_FILE = "/root/.env"   # API Key 兜底来源,环境变量优先

SYSTEM_PROMPT = (
    "你是 SproutCode(码苗),一个在终端里工作的 Coding Agent。\n"
    "你可以调用工具读写文件、执行 shell 命令,帮用户完成编程任务。\n"
    "- 动手优先:用工具实际完成,而不是只给建议;\n"
    "- 工具报错时读错误信息,调整参数或命令后重试;\n"
    "- 完成后用简短中文说明做了什么、结果如何。"
)


def load_api_key() -> str | None:
    """环境变量优先;否则解析 /root/.env 中 DEEPSEEK_API_KEY=... 一行。"""
    # 安全基线:Key 只从环境变量/本地 .env 读,永不写进代码、永不进 git。
    # 手写最小 .env 解析(只认 KEY=VALUE 行),换取"除 openai SDK 外零依赖"。
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return None
```

- [ ] **Step 7: 写入 `sproutcode/client.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from openai import OpenAI

from .config import BASE_URL, MODEL

TextCallback = Callable[[str], None]


@dataclass
class ToolCall:
    """聚合完成的一次工具调用。"""

    id: str
    name: str
    arguments: str


@dataclass
class AssistantTurn:
    """一轮模型响应的统一形态:真实客户端与 FakeLLM 都返回它。"""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_message(self) -> dict[str, Any]:
        """转成可原样写回 messages 的 assistant 消息。"""
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class ChatClient(Protocol):
    """Agent 只依赖这一层接口,测试用 FakeLLM 平替,不触网。"""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: TextCallback | None = None,
    ) -> AssistantTurn: ...


def collect_stream(
    chunks: Iterable[Any], on_text: TextCallback | None = None
) -> AssistantTurn:
    """把 SSE 增量聚合成一轮完整响应。

    文本 delta 直接回调给渲染层;tool_calls 按 index 分槽,id/name/arguments
    都可能被拆到多个 chunk,必须逐段拼接——这是流式相对非流式最容易写错的地方。
    """
    text_parts: list[str] = []
    slots: dict[int, dict[str, str]] = {}

    for chunk in chunks:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = choices[0].delta

        content = getattr(delta, "content", None)
        if content:
            text_parts.append(content)
            if on_text is not None:
                on_text(content)

        for call_delta in getattr(delta, "tool_calls", None) or []:
            index = getattr(call_delta, "index", 0) or 0
            slot = slots.setdefault(index, {"id": "", "name": "", "arguments": ""})

            call_id = getattr(call_delta, "id", None)
            if call_id and not slot["id"]:
                slot["id"] = call_id

            function = getattr(call_delta, "function", None)
            if function is not None:
                name = getattr(function, "name", None)
                arguments = getattr(function, "arguments", None)
                if name:
                    slot["name"] += name
                if arguments:
                    slot["arguments"] += arguments

    tool_calls = [
        ToolCall(
            id=slot["id"] or f"call_{index}",
            name=slot["name"],
            arguments=slot["arguments"] or "{}",
        )
        for index, slot in sorted(slots.items())
    ]
    return AssistantTurn(content="".join(text_parts) or None, tool_calls=tool_calls)


class LLMClient:
    """OpenAI 兼容流式客户端;换模型只改 config 里的两个常量。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        model: str = MODEL,
        sdk: Any | None = None,
    ) -> None:
        self.model = model
        self._sdk = sdk or OpenAI(api_key=api_key, base_url=base_url, max_retries=3)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_text: TextCallback | None = None,
    ) -> AssistantTurn:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            request["tools"] = tools
        stream = self._sdk.chat.completions.create(**request)
        return collect_stream(stream, on_text)
```

- [ ] **Step 8: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_client.py -v`
Expected: 5 passed

- [ ] **Step 9: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/__init__.py sproutcode/config.py sproutcode/client.py tests/test_config.py tests/test_client.py
git commit -m "feat: v1 配置与流式客户端"
```

---

### Task 3: 工具注册表 + @tool + 内置工具【子代理写文件,主会话审查提交】

**Files:**
- Create: `sproutcode/tools/__init__.py`
- Create: `sproutcode/tools/files.py`
- Create: `sproutcode/tools/shell.py`
- Create: `tests/test_tools.py`

**Interfaces:**
- Consumes: `SHELL_TIMEOUT` from `sproutcode.config`
- Produces: `ToolSpec`;`ToolRegistry()` with `.names`, `.tool`, `.schemas()`, `.execute(name, arguments) -> (str, bool)`;module-level `registry` / `tool`;builtins `read_file`, `write_file`, `list_files`, `run_shell`

- [ ] **Step 1: 先写测试 `tests/test_tools.py`**

```python
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

    (tmp_path / "sub").mkdir()
    listed, is_error = registry.execute(
        "list_files", json.dumps({"path": str(tmp_path)})
    )
    assert not is_error
    assert "hello.txt" in listed and "sub/" in listed


def test_run_shell_nonzero_exit_is_not_an_error():
    content, is_error = registry.execute("run_shell", '{"command": "exit 3"}')
    assert not is_error
    assert "[exit 3]" in content


def test_read_missing_file_is_error_text():
    content, is_error = registry.execute(
        "read_file", json.dumps({"path": "/definitely/not/here.txt"})
    )
    assert is_error and "ERROR" in content
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_tools.py -v`
Expected: FAIL,报 `ModuleNotFoundError: No module named 'sproutcode.tools'`

- [ ] **Step 3: 写入 `sproutcode/tools/__init__.py`**

```python
from __future__ import annotations

import inspect
import json
import types
from dataclasses import dataclass
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints

ToolFunc = Callable[..., Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    func: ToolFunc


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """取摘要作工具描述,取 Args 段作参数描述(只支持最小约定)。"""
    if not doc:
        return "", {}

    summary: list[str] = []
    params: dict[str, str] = {}
    in_args = False

    for raw in inspect.cleandoc(doc).splitlines():
        line = raw.strip()
        if line in {"Args:", "Arguments:", "参数:"}:
            in_args = True
            continue
        if line in {"Returns:", "Raises:", "Yields:"}:
            in_args = False
            continue
        if in_args:
            if ":" in line:
                name, desc = line.split(":", 1)
                if name.strip():
                    params[name.strip()] = desc.strip()
            continue
        if line:
            summary.append(line)

    return " ".join(summary), params


def _json_type(annotation: Any, param_name: str) -> dict[str, Any]:
    """把 Python 类型注解映射成 JSON Schema 片段。"""
    origin = get_origin(annotation)
    args = get_args(annotation)

    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if origin in (list, tuple, set):
        items = _json_type(args[0], param_name) if args else {"type": "string"}
        return {"type": "array", "items": items}
    if origin is dict:
        return {"type": "object"}
    if origin in (Union, types.UnionType):
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return _json_type(non_none[0], param_name)
        return {"anyOf": [_json_type(arg, param_name) for arg in non_none]}
    raise TypeError(
        f"工具参数 {param_name!r} 的类型 {annotation!r} 暂不支持自动生成 JSON Schema"
    )


def _build_parameters(func: ToolFunc, param_docs: dict[str, str]) -> dict[str, Any]:
    """从签名生成 parameters;有默认值的参数不进 required。"""
    signature = inspect.signature(func)
    hints = get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in signature.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"工具 {func.__name__} 不支持 *args/**kwargs 参数")
        annotation = hints.get(name, str)
        prop = _json_type(annotation, name)
        if name in param_docs:
            prop["description"] = param_docs[name]
        properties[name] = prop
        if param.default is param.empty:
            required.append(name)

    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    return parameters


class ToolRegistry:
    """工具名 → 实现 + Schema。隔离实例让测试不污染全局注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def tool(
        self,
        func: ToolFunc | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> ToolFunc | Callable[[ToolFunc], ToolFunc]:
        """装饰器:注册函数并从签名 + docstring 生成 Schema,返回原函数。"""

        def decorate(fn: ToolFunc) -> ToolFunc:
            tool_name = name or fn.__name__
            if tool_name in self._tools:
                raise ValueError(f"工具重复注册: {tool_name}")
            summary, param_docs = _parse_docstring(inspect.getdoc(fn))
            summary = description or summary
            if not summary:
                raise ValueError(f"工具 {tool_name} 缺少 docstring 描述")
            self._tools[tool_name] = ToolSpec(
                name=tool_name,
                description=summary,
                parameters=_build_parameters(fn, param_docs),
                func=fn,
            )
            return fn

        return decorate(func) if func is not None else decorate

    def schemas(self) -> list[dict[str, Any]]:
        """OpenAI function calling 的 tools 参数。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._tools.values()
        ]

    def execute(self, name: str, arguments: str) -> tuple[str, bool]:
        """统一执行入口:未知工具/坏 JSON/函数异常都转成错误文本。"""
        try:
            spec = self._tools.get(name)
            if spec is None:
                return f"[ERROR] 未知工具: {name}", True
            kwargs = json.loads(arguments) if arguments.strip() else {}
            if not isinstance(kwargs, dict):
                raise ValueError("工具参数必须是 JSON 对象")
            return str(spec.func(**kwargs)), False
        except Exception as exc:
            return f"[ERROR] {type(exc).__name__}: {exc}", True


registry = ToolRegistry()
tool = registry.tool

# 装饰器与全局注册表就绪后再导入内置工具,导入即注册。
from . import files, shell  # noqa: E402,F401
from .files import list_files, read_file, write_file  # noqa: E402,F401
from .shell import run_shell  # noqa: E402,F401

__all__ = [
    "ToolRegistry",
    "ToolSpec",
    "registry",
    "tool",
    "read_file",
    "write_file",
    "list_files",
    "run_shell",
]
```

- [ ] **Step 4: 写入 `sproutcode/tools/files.py`**

```python
from __future__ import annotations

import os

from . import tool


@tool
def read_file(path: str) -> str:
    """读取指定路径文本文件的内容,返回全文。

    Args:
        path: 文件路径。
    """
    with open(path, encoding="utf-8") as f:
        return f.read()


@tool
def write_file(path: str, content: str) -> str:
    """把文本内容整体写入指定文件(覆盖),父目录不存在时自动创建。

    Args:
        path: 目标文件路径。
        content: 完整文件内容。
    """
    # 自动建父目录把可预见的失败在工具内消化,省掉模型
    # "目录不存在→报错→mkdir→重写"一整轮往返。
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}({len(content)} 字符)"


@tool
def list_files(path: str = ".") -> str:
    """列出目录下的一级条目,目录名以 / 结尾。

    Args:
        path: 目录路径,默认为当前目录。
    """
    with os.scandir(path) as entries:
        names = sorted(
            entry.name + ("/" if entry.is_dir() else "") for entry in entries
        )
    return "\n".join(names) if names else "(空目录)"
```

- [ ] **Step 5: 写入 `sproutcode/tools/shell.py`**

```python
from __future__ import annotations

import subprocess

from ..config import SHELL_TIMEOUT
from . import tool


@tool
def run_shell(command: str) -> str:
    """在 shell 中执行命令,返回退出码与输出。

    Args:
        command: 要执行的 shell 命令。
    """
    # 退出码以 [exit N] 的文本形式交给模型读;非零退出是合法结果,不是异常。
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=SHELL_TIMEOUT,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return f"[exit {proc.returncode}] {output.strip() or '(无输出)'}"
```

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_tools.py -v`
Expected: 8 passed

- [ ] **Step 7: 离线自检注册表(不花 API 费)**

```bash
.venv/bin/python - <<'EOF'
from sproutcode.tools import registry
names = {s["function"]["name"] for s in registry.schemas()}
assert names == {"read_file", "write_file", "list_files", "run_shell"}, names
c, e = registry.execute("write_file", '{"path": "/tmp/sprout-v1-check.txt", "content": "hi"}')
assert not e and "已写入" in c
assert registry.execute("read_file", '{"path": "/tmp/sprout-v1-check.txt"}') == ("hi", False)
c, e = registry.execute("run_shell", '{"command": "exit 3"}')
assert not e and "[exit 3]" in c
c, e = registry.execute("read_file", '{"path": "/nonexistent"}')
assert e and "ERROR" in c
print("工具层自检通过")
EOF
```

Expected: 打印「工具层自检通过」

- [ ] **Step 8: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/tools/ tests/test_tools.py
git commit -m "feat: v1 工具注册表与 @tool 装饰器"
```

---

### Task 4: Agent 类与循环【子代理写文件,主会话审查提交】

**Files:**
- Create: `sproutcode/agent.py`
- Create: `tests/test_agent.py`

**Interfaces:**
- Consumes: `ChatClient.chat(messages, tools, on_text) -> AssistantTurn`;`ToolRegistry.schemas()` / `.execute()`
- Produces: `Agent(client, *, registry=None, max_rounds=25, system_prompt=SYSTEM_PROMPT)`;`Agent.messages`;`Agent.run(user_input, *, on_text=None, on_tool_call=None, on_tool_result=None) -> str`

- [ ] **Step 1: 先写测试 `tests/test_agent.py`**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`
Expected: FAIL,报 `ModuleNotFoundError: No module named 'sproutcode.agent'`

- [ ] **Step 3: 写入 `sproutcode/agent.py`**

```python
from __future__ import annotations

from typing import Callable

from .client import AssistantTurn, ChatClient, TextCallback
from .config import MAX_ROUNDS, SYSTEM_PROMPT
from .tools import ToolRegistry, registry as default_registry

ToolCallCallback = Callable[[str, str], None]
ToolResultCallback = Callable[[str, bool], None]


class Agent:
    """持有消息历史并驱动 LLM↔工具循环;messages 就是 Agent 的全部状态。"""

    def __init__(
        self,
        client: ChatClient,
        *,
        registry: ToolRegistry | None = None,
        max_rounds: int = MAX_ROUNDS,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.client = client
        self.registry = registry or default_registry
        self.max_rounds = max_rounds
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def run(
        self,
        user_input: str,
        *,
        on_text: TextCallback | None = None,
        on_tool_call: ToolCallCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
    ) -> str:
        """处理一条用户消息,直到模型不再发起工具调用。"""
        # 记录本轮起点:失败/中断时把本轮 user 与半截 tool_calls 一起回退,
        # 否则残缺的 tool_call_id 会让下一次 API 请求直接 400。
        task_start = len(self.messages)
        self.messages.append({"role": "user", "content": user_input})
        try:
            for _ in range(self.max_rounds):
                turn = self.client.chat(
                    self.messages,
                    self.registry.schemas(),
                    on_text=on_text,
                )
                # 带 tool_calls 的 assistant 消息原样入历史,每个 call 都必须有 tool 回复。
                self.messages.append(turn.to_message())
                if not turn.tool_calls:
                    return turn.content or ""

                for call in turn.tool_calls:
                    if on_tool_call is not None:
                        on_tool_call(call.name, call.arguments)
                    content, is_error = self.registry.execute(
                        call.name, call.arguments
                    )
                    if on_tool_result is not None:
                        on_tool_result(content, is_error)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": content,
                        }
                    )

            fallback = "(已达单任务轮数上限,已停止。请把任务拆小后重试。)"
            if on_text is not None:
                on_text(fallback)
            return fallback
        except BaseException:
            del self.messages[task_start:]
            raise
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/agent.py tests/test_agent.py
git commit -m "feat: v1 Agent 类与工具循环"
```

---

### Task 5: CLI、包入口与渲染测试【子代理写文件,主会话审查提交】

**Files:**
- Create: `sproutcode/cli.py`
- Create: `sproutcode/__main__.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Agent.run(...)`;`LLMClient`;`load_api_key`;`MODEL`
- Produces: `main()`;`Renderer` with `.on_text/.on_tool_call/.on_tool_result/.finish()`;`preview_arguments(arguments, limit=60) -> str`

- [ ] **Step 1: 先写测试 `tests/test_cli.py`**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL,报 `ModuleNotFoundError: No module named 'sproutcode.cli'`

- [ ] **Step 3: 写入 `sproutcode/cli.py`**

```python
from __future__ import annotations

import sys

from .agent import Agent
from .client import LLMClient
from .config import MODEL, load_api_key

DIM = "\033[2m"
RESET = "\033[0m"
PROMPT = "码苗> "


def preview_arguments(arguments: str, limit: int = 60) -> str:
    """只截断展示层;完整参数仍会执行、仍会入历史。"""
    if len(arguments) <= limit:
        return arguments
    return arguments[: limit - 3] + "..."


class Renderer:
    """正文亮色、过程暗色;流式文本到达时才打印一次对话前缀。"""

    def __init__(self) -> None:
        self.started = False

    def on_text(self, chunk: str) -> None:
        if not self.started:
            print(f"\n{PROMPT}", end="", flush=True)
            self.started = True
        print(chunk, end="", flush=True)

    def on_tool_call(self, name: str, arguments: str) -> None:
        print(
            f"  {DIM}· {name}({preview_arguments(arguments)}){RESET}",
            flush=True,
        )

    def on_tool_result(self, content: str, is_error: bool) -> None:
        if is_error:
            print(f"  {DIM}  ↳ {content}{RESET}", flush=True)

    def finish(self) -> bool:
        """收尾并返回本轮是否输出过正文。"""
        had_text = self.started
        if had_text:
            print()
        self.started = False
        return had_text


def build_client(api_key: str | None = None) -> LLMClient:
    key = api_key or load_api_key()
    if not key:
        sys.exit("缺少 DEEPSEEK_API_KEY:请设置环境变量,或写入 /root/.env")
    return LLMClient(key)


def main() -> None:
    agent = Agent(build_client())
    print(f"SproutCode(码苗)v1 · {MODEL} · /exit 退出")
    while True:
        try:
            user = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in ("/exit", "/quit"):
            break

        renderer = Renderer()
        try:
            reply = agent.run(
                user,
                on_text=renderer.on_text,
                on_tool_call=renderer.on_tool_call,
                on_tool_result=renderer.on_tool_result,
            )
        except KeyboardInterrupt:
            if renderer.started:
                print()
            print("\n(任务已中断,本轮输入已丢弃;可直接重新描述任务)")
            continue
        except Exception as exc:
            if renderer.started:
                print()
            print(f"\n[API 错误] {exc}")
            continue

        had_text = renderer.finish()
        if not had_text:
            print(f"\n{PROMPT}{reply or '(无回复)'}")
```

- [ ] **Step 4: 写入 `sproutcode/__main__.py`**

```python
from .cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 运行全量测试**

Run: `.venv/bin/python -m pytest -v`
Expected: 全部通过(22 passed)

- [ ] **Step 6: 包入口离线自检**

Run: `printf '/exit\n' | .venv/bin/python -m sproutcode`
Expected: 打印 v1 banner 与「你>」后正常退出,无 ImportError

- [ ] **Step 7: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/cli.py sproutcode/__main__.py tests/test_cli.py
git commit -m "feat: v1 REPL 流式渲染与包入口"
```

---

### Task 6: 真机验收(主会话执行,门禁)

**Files:** 无新增;验收后清理 `sprout-demo/`

- [ ] **Step 1: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 22 passed

- [ ] **Step 2: 真机两轮任务(管道喂入,证明全程不退出 REPL)**

```bash
printf '请在 sprout-demo/ 目录下创建 hello.txt,内容为一行:SproutCode v1\n用 cat 确认文件内容,并运行 ls sprout-demo\n/exit\n' | .venv/bin/python -m sproutcode
```

- [ ] **Step 3: 验收判定(全部满足才算过)**

1. 进程退出码 0,两轮任务在同一 REPL 会话内完成;
2. `sprout-demo/hello.txt` 内容为 `SproutCode v1`;
3. 终端出现 `write_file(...)` 与 `run_shell(...)` 暗色工具轨迹;
4. 模型正文经流式回调输出(先出现正文,不是整段一次性拼接后打印),最终为中文总结。

- [ ] **Step 4: 清理验收产物**

Run: `rm -rf sprout-demo /tmp/sprout-v1-check.txt`
Expected: 工作区无临时文件

- [ ] **Step 5: 若失败则修到通过再继续,不得跳进门禁后的 tag 步骤**

---

### Task 7: 演进笔记、AGENTS.md 与 tag(主会话执行)

**Files:**
- Create: `docs/journey/v1-modular-streaming.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: 写演进笔记(固定三段)**

必须基于实际验收输出与调试记录,包含:

- **改了什么**:单文件 → 包结构;非流式 → SSE delta 聚合;手写 Schema → `@tool` 自动生成;新增 `ToolRegistry` 可实例化注册表;新增 `list_files`;新增 pytest + FakeLLM;入口改为 `python -m sproutcode`;根 `sproutcode.py` 删除。
- **为什么**:v0 195 行已到单文件可维护边界;流式解决"黑屏等待"的可观测性;工具数量增加后 Schema 样板需要消灭;重构前先有测试网,才能证明"行为等价"。
- **踩坑与收获**:至少写清流式 `tool_calls` 分片按 index 聚合、assistant 消息从 SDK 对象改成 dict 后 `content: None` 的形态、本轮回退逻辑从 `main` 搬进 `Agent.run`、FakeLLM 与真实客户端同接口、根目录同名文件与包冲突。若实际调试出现新问题,以实际为准补充。

- [ ] **Step 2: 更新 `AGENTS.md`**

- 进度行改为:v0、v1 均已完成并真机验收通过,tag `v0` / `v1`;
- 真机验收命令改为 `printf '...' | .venv/bin/python -m sproutcode`;
- 离线自检改为 `.venv/bin/python -m pytest`;
- `task_start` 说明改为 `Agent.run`(v0 在 `sproutcode.py`,v1 起在 `sproutcode/agent.py`)。

- [ ] **Step 3: 提交文档**

```bash
git add docs/journey/v1-modular-streaming.md AGENTS.md
git commit -m "docs: v1 演进笔记与工作指南更新"
```

- [ ] **Step 4: 打 tag 并推送**

```bash
git tag v1
git push origin main --tags
```

- [ ] **Step 5: 验证**

Run: `git tag --list`(应含 `v0`、`v1`)、`git status --short`(应为空)、`git log --oneline -5`

---

## 计划自检

- **Spec 覆盖**:§2 pyproject(Task 1);§3 v1 验收(Task 6);§4.1 包结构(Task 2-5);§4.2 流式与错误回传(Task 2、4);§4.3 工具协议(Task 3);§5 API/工具/中断/轮数上限(Task 2、3、4、5);§6 FakeLLM 与工具单测(Task 3、4);§8 演进笔记(Task 7)。
- **占位符**:无 TBD/TODO;所有代码与断言均为可执行内容。
- **类型一致性**:`AssistantTurn` / `ToolCall` 在 client 定义,agent 与测试同名使用;`ToolRegistry.execute` 始终返回 `(str, bool)`;`Agent.run` 的四个回调参数名在 cli 与测试中一致。
