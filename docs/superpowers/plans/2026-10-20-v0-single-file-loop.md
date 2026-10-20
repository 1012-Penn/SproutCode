# SproutCode v0 实现计划:单文件最小闭环(通用子智能体分工)

> **执行方式(子代理分工):** 机械性工作交给通用子智能体(general-purpose)执行——照计划写入 `sproutcode.py`、跑离线自检、起草演进笔记;主会话负责编排、对照计划逐行审查、真机验收门禁、git 提交与 tag。每任务带检查点,总体流程仍为 superpowers:executing-plans 的内联执行。

**Goal:** 交付 `sproutcode.py`——DeepSeek 客户端 + Agent 循环 + 3 工具 + REPL 的非流式最小闭环,通过真机验收,打 `v0` tag,写演进笔记。

**Architecture:** 全部逻辑在一个文件里,自上而下四段:配置/Key 加载 → 工具实现 + 手写 Schema → Agent 循环(LLM↔工具交替,25 轮上限)→ REPL。模块化、流式、pytest 都是 v1 的主题,本阶段刻意不做。

**Tech Stack:** Python 3.10.12(已确认)、openai SDK(唯一运行时依赖)、DeepSeek `deepseek-chat`。

**Spec:** `docs/superpowers/specs/2026-10-20-sproutcode-design.md` §3 v0 行、§4.2 Agent 循环、§4.3 工具协议、§5 错误处理、§8 演进笔记约定。

## Global Constraints(逐条来自设计文档)

- 语言 Python 3.10+,运行时依赖仅 `openai` SDK,CLI 用标准库
- LLM:OpenAI 兼容协议,base_url `https://api.deepseek.com`,模型 `deepseek-chat`,**非流式**;Key 从环境变量或 `/root/.env` 的 `DEEPSEEK_API_KEY` 加载(已确认存在),永不入库
- 单任务工具调用轮数上限 25;工具结果统一 `(content, is_error)`,异常不向上抛、转错误文本回传模型
- 验收标准:对话中让 Agent 创建文件并执行 shell 命令,全程不退出 REPL
- 周期纪律:实现 → 自检 → 验收 → tag → 演进笔记,不做 v1 的事(不拆包、不流式、不写 pytest)

---

### Task 1: 环境准备、Flash 代理定义与计划归档

**Files:** Create: `.venv/`(不提交,已在 .gitignore)、`docs/superpowers/plans/2026-10-20-v0-single-file-loop.md`

- [ ] 保存本计划全文到 `docs/superpowers/plans/2026-10-20-v0-single-file-loop.md`
- [ ] `python3 -m venv .venv && .venv/bin/pip install openai`
- [ ] 验证:`.venv/bin/python -c "import openai; print(openai.__version__)"` 输出版本号
- [ ] Commit:`git add docs/superpowers/plans/ && git commit -m "docs: v0 实现计划"`

### Task 2: 实现 sproutcode.py【通用子智能体执行,主会话审查把关】

**Files:** Create: `sproutcode.py`(仓库根)

**分工:** 通用子智能体(general-purpose)按计划完整代码逐字写入文件,并跑 `py_compile` 与离线自检,原样回报全部输出;主会话对照计划审查文件与自检结果后才提交。

**Produces:** `load_api_key() -> str|None`、`read_file/write_file/run_shell(path[,content/command]) -> str`、`execute_tool(name, arguments) -> (content, is_error)`、`run_agent(client, messages) -> str`、`main()`

- [ ] 写入 `sproutcode.py`(完整代码见仓库本文件下方代码块,约 190 行)
- [ ] 语法检查:`.venv/bin/python -m py_compile sproutcode.py`
- [ ] 工具层离线自检(不花 API 费),全部断言通过并打印「工具层自检通过」:

```bash
.venv/bin/python - <<'EOF'
import sproutcode
assert sproutcode.load_api_key(), "api key 未加载"
c, e = sproutcode.execute_tool("write_file", '{"path": "/tmp/sc-test.txt", "content": "hi"}')
assert not e and "已写入" in c
assert sproutcode.execute_tool("read_file", '{"path": "/tmp/sc-test.txt"}') == ("hi", False)
c, e = sproutcode.execute_tool("run_shell", '{"command": "echo ok"}')
assert not e and "ok" in c and "exit 0" in c
c, e = sproutcode.execute_tool("run_shell", '{"command": "exit 3"}')
assert not e and "exit 3" in c          # 非零退出是合法结果,不是 is_error
c, e = sproutcode.execute_tool("read_file", '{"path": "/nonexistent"}')
assert e and "ERROR" in c
c, e = sproutcode.execute_tool("nope", "{}")
assert e
print("工具层自检通过")
EOF
```

- [ ] Commit(审查后):`git add sproutcode.py && git commit -m "feat: v0 单文件最小闭环"`

### Task 3: 真机验收(对照 spec 验收标准,主会话执行)

- [ ] 用管道模拟多轮会话(证明"全程不退出 REPL"),在仓库根执行:

```bash
printf '请在 sprout-demo/ 目录下创建 hello.txt,内容为一行:SproutCode v0\n用 cat 确认文件内容,并运行 ls sprout-demo\n/exit\n' | .venv/bin/python sproutcode.py
```

- [ ] 验收判定,全部满足才算过:
  1. 会话期间 REPL 未崩溃,两轮任务都在同一会话内完成;
  2. `cat sprout-demo/hello.txt` 输出 `SproutCode v0`;
  3. 终端回显中出现 `write_file(...)`、`run_shell(...)` 工具调用轨迹;
  4. Agent 的最终回复是中文总结。
- [ ] 若验收失败:定位、修复、重跑(修复并入 Task 2 的 feat commit,amend 或补提交)
- [ ] 清理验收产物:`rm -rf sprout-demo /tmp/sc-test.txt`

### Task 4: 演进笔记 + tag v0【通用子智能体起草,主会话定稿】

**Files:** Create: `docs/journey/v0-minimal-loop.md`(spec §8 固定三段:改了什么/为什么/踩坑与收获)

- [ ] Flash 子代理依据骨架 + Task 3 实际会话记录起草笔记
- [ ] 主会话审查定稿后 Commit:`git add docs/journey/ && git commit -m "docs: v0 演进笔记"`
- [ ] `git tag -a v0 -m "v0: 单文件最小闭环"`(打在笔记 commit 上,checkout v0 即得完整 v0 交付物)
- [ ] 收尾校验:`git status` 干净、`git log --oneline` 与 `git tag` 符合预期

## 自检结论

- spec v0 行逐项对照:单文件 ✓ / DeepSeek 客户端 ✓ / Agent 循环+25 轮上限 ✓ / 3 工具 ✓ / REPL 非流式 ✓ / 验收标准 ✓;(content,is_error)约定 ✓ / API 重试上限 3 次(SDK max_retries)✓ / Ctrl+C 安全退出 ✓
- 明确不做(留给后续版本):拆包、流式、pytest、审批、会话持久化、输出截断策略

---

## 附:sproutcode.py 完整实现代码(Task 2 执行依据)

```python
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
```
