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
