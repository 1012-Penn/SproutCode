#!/usr/bin/env python3
"""SproutCode(码苗)v0 —— 单文件最小闭环 Coding Agent。

DeepSeek 客户端 + Agent 循环 + 3 个工具(read_file / write_file / run_shell)
+ 简单 REPL,非流式。设计文档:docs/superpowers/specs/2026-10-20-sproutcode-design.md

教学导读:整个文件自上而下就是四层,职责边界从 v0 起定下,v1 拆包只是把它们搬进各自的模块——
  ① 配置层:常量 + API Key 加载(改一行就能换模型/环境)
  ② 工具层:普通函数 + JSON Schema 合同 + 统一执行入口
  ③ Agent 循环:LLM 与工具交替的心脏,消息列表就是 Agent 的全部状态
  ④ REPL:与用户交互的外壳,任何单轮失败都不掀翻整个会话
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from openai import OpenAI

# ---------- ① 配置层 ----------
# 所有"改一行就能换环境"的东西集中在这里:想换模型/厂商,
# 只需改 BASE_URL 和 MODEL 两个值——这正是坚持 OpenAI 兼容协议的回报。
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"
MAX_ROUNDS = 25           # 单任务工具调用轮数上限,防失控(保险丝,不是常态工况)
SHELL_TIMEOUT = 60        # 秒;v0 只防卡死,正式超时/截断策略是 v3 的主题
ENV_FILE = "/root/.env"   # API Key 兜底来源,环境变量优先

# 系统提示词:给模型定角色和行为"方向"(动手优先、报错重试)。
# 注意它不教具体流程——"先勘察再动手"这类执行策略由模型自己涌现,
# 提示词逐条教既教不完,也约束不住。
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
    # 这里手写了一个最小 .env 解析(只认 KEY=VALUE 行),
    # 换取"除 openai SDK 外零依赖",不为此引入 python-dotenv。
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


# ---------- ② 工具层 ----------
# 约定:每个工具就是一个普通 Python 函数——入参用简单类型(方便从 JSON 反序列化),
# 返回值是"给模型读的字符串"。模型对工具的全部理解来自两处:
# 函数的实际行为 + 下方 TOOL_SCHEMAS 里的描述文本,两者要一起维护。

def read_file(path: str) -> str:
    """读取文本文件全文。"""
    with open(path, encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> str:
    """把 content 整体写入文件(覆盖),父目录不存在时自动创建。"""
    # 自动建父目录 = 把可预见的失败在工具内提前消化,
    # 省掉模型"目录不存在→报错→mkdir→重写"一整轮往返(花的都是 token 和延迟)。
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}({len(content)} 字符)"


def run_shell(command: str) -> str:
    """执行 shell 命令,返回退出码与输出。"""
    # 退出码以 [exit N] 的形式"带内"放进输出文本,让模型自己读出成败;
    # 非零退出是合法结果(不是异常),模型会据此决定是否重试。
    proc = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=SHELL_TIMEOUT
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return f"[exit {proc.returncode}] {output.strip() or '(无输出)'}"


# 注册表:工具名 → 函数。execute_tool 按名字分发;v4 子 Agent 的"受限工具集"、
# v5 MCP 远端工具,复用的正是这个统一注册表抽象。
TOOL_IMPL = {"read_file": read_file, "write_file": write_file, "run_shell": run_shell}

# JSON Schema 是 function calling 的"合同":告诉模型每个工具叫什么、何时用、
# 参数长什么样。模型看不到函数体,description 写得越准,调用就越准。
# v0 手写三份;v1 的 @tool 装饰器会从签名 + docstring 自动生成,消灭这层样板。
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


# 统一执行入口:把"模型的调用请求"(工具名 + JSON 字符串)翻译成一次 Python 调用,
# 并把一切异常兜成错误文本。所有工具调用都从这扇门进出——
# v3 的危险命令审批,届时也是在这条路径上挂钩子。
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


# ---------- ③ Agent 循环:项目的心脏 ----------
# 解剖开只有一句话:"把工具结果喂回模型"。每一轮循环 =
#   调 LLM → 模型发起 tool_calls?→ 逐个执行 → 结果以 role=tool 追加进 messages
#         → 回到循环开头再调 LLM;模型不再要工具时,返回纯文本。
# messages 列表就是 Agent 的全部状态:它的结构一旦残缺(如 tool_call_id 对不上),
# Agent 就坏了——所以下面对"什么能进 messages、怎么回退"格外小心。
def run_agent(client: OpenAI, messages: list) -> str:
    """处理一条用户消息:LLM 与工具交替,直到模型不再发起工具调用。"""
    for _ in range(MAX_ROUNDS):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOL_SCHEMAS
        )
        msg = resp.choices[0].message
        # 模型不再要工具 = 任务收口:把它的话入史并返回给 REPL。
        if not msg.tool_calls:
            messages.append({"role": "assistant", "content": msg.content})
            return msg.content
        # 带 tool_calls 的 assistant 消息必须"原样"入历史:每个 tool_call_id
        # 都要有对应的 role=tool 回复,缺一个,下一次 API 请求就被 400 拒掉。
        messages.append(msg)
        # 逐个执行本轮的工具调用;工具间可能相互依赖(先写文件再 cat),故顺序执行。
        for call in msg.tool_calls:
            # 终端回显:暗色打印一行"模型在做什么",参数超长截断——
            # 截断只发生在打印层,完整参数照常执行、照常入历史。
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


# ---------- ④ REPL:与用户交互的外壳 ----------
# 职责只有三件:收输入、交给 run_agent、展示结果。
# 健壮性目标:任何单轮失败(网络错、用户 Ctrl+C 中断)只损失那一轮,不崩进程。
def main() -> None:
    api_key = load_api_key()
    if not api_key:
        sys.exit("缺少 DEEPSEEK_API_KEY:请设置环境变量,或写入 /root/.env")
    client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=3)
    # 会话级状态:system prompt 打头,之后所有 user/assistant/tool 消息都追加在这里。
    # v2 会把这份列表以 JSONL 落盘,实现 --resume。
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
        # 记录本轮起点:若任务中途失败或被 Ctrl+C 打断,把 messages 回退到这里——
        # 半截 tool_calls 留在历史里,下一轮 API 请求会直接 400(见 ③ 的说明)。
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
