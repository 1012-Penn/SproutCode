#!/usr/bin/env python3
"""SproutCode(码苗)v0 —— 单文件最小闭环 Coding Agent。

DeepSeek 客户端 + Agent 循环 + 3 个工具(read_file / write_file / run_shell)
+ 简单 REPL,非流式。设计文档:docs/superpowers/specs/2026-10-20-sproutcode-design.md
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from openai import OpenAI

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"
MAX_ROUNDS = 25           # 单任务工具调用轮数上限,防失控
SHELL_TIMEOUT = 60        # 秒;v0 只防卡死,正式超时/截断策略是 v3 的主题
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


# ---------- 工具实现(Schema 本版手写,@tool 自动生成是 v1 的主题)----------

def read_file(path: str) -> str:
    """读取文本文件全文。"""
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    """把 content 整体写入文件(覆盖),父目录不存在时自动创建。"""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}({len(content)} 字符)"


def run_shell(command: str) -> str:
    """执行 shell 命令,返回退出码与输出。"""
    proc = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=SHELL_TIMEOUT
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return f"[exit {proc.returncode}] {output.strip() or '(无输出)'}"


TOOL_IMPL = {"read_file": read_file, "write_file": write_file, "run_shell": run_shell}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径文本文件的内容,返回全文。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把文本内容整体写入指定文件(覆盖),父目录不存在时自动创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "在 shell 中执行命令,返回退出码与输出。用于跑脚本、查目录、装依赖等。",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的命令"}},
                "required": ["command"],
            },
        },
    },
]


def execute_tool(name: str, arguments: str) -> tuple[str, bool]:
    """执行一次工具调用,结果统一为 (content, is_error)。

    异常不向上抛,转错误文本回传模型,促其自我纠正(设计文档 §4.2)。
    """
    try:
        if name not in TOOL_IMPL:
            return f"[ERROR] 未知工具: {name}", True
        result = TOOL_IMPL[name](**json.loads(arguments))
        return str(result), False
    except Exception as exc:
        return f"[ERROR] {type(exc).__name__}: {exc}", True


# ---------- Agent 循环(项目的心脏)----------

def run_agent(client: OpenAI, messages: list) -> str:
    """处理一条用户消息:LLM 与工具交替,直到模型不再发起工具调用。"""
    for _ in range(MAX_ROUNDS):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOL_SCHEMAS
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content})
            return msg.content
        messages.append(msg)  # 带 tool_calls 的 assistant 消息必须原样入历史
        for call in msg.tool_calls:
            args = call.function.arguments
            preview = args if len(args) <= 60 else args[:57] + "..."
            print(f"  \033[2m· {call.function.name}({preview})\033[0m")
            content, is_error = execute_tool(call.function.name, args)
            if is_error:
                print(f"  \033[2m  ↳ {content}\033[0m")
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": content}
            )
    return "(已达单任务轮数上限,已停止。请把任务拆小后重试。)"


# ---------- REPL ----------

def main() -> None:
    api_key = load_api_key()
    if not api_key:
        sys.exit("缺少 DEEPSEEK_API_KEY:请设置环境变量,或写入 /root/.env")
    client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=3)
    messages: list = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("SproutCode(码苗)v0 · deepseek-chat · /exit 退出")
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
        task_start = len(messages)
        messages.append({"role": "user", "content": user})
        try:
            reply = run_agent(client, messages)
        except KeyboardInterrupt:
            # 回退本轮:半截 tool_calls 留在历史里会让下一轮请求 400
            del messages[task_start:]
            print("\n(任务已中断,本轮输入已丢弃;可直接重新描述任务)")
            continue
        except Exception as exc:
            del messages[task_start:]
            print(f"\n[API 错误] {exc}")
            continue
        print(f"\n码苗> {reply}")


if __name__ == "__main__":
    main()
