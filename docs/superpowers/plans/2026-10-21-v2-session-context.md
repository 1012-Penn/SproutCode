# SproutCode v2 实现计划:会话与上下文

> **执行方式(子代理分工):** 机械性工作交给通用子智能体(general-purpose)执行——照本计划逐字写入包代码与测试、跑 pytest 自检、回报全部输出;主会话负责对照计划逐行审查 diff、真机验收门禁、git 提交与 tag。子代理不执行 git 命令。

**Goal:** 交付 v2 里程碑:会话 JSONL 落盘 + `--resume` 恢复;斜杠命令(/help /clear /resume);token 近似计数 + 上下文压缩(超预算摘要旧消息);系统提示词外置。验收标准(spec §3):重启后恢复完整对话;构造超长会话触发压缩且 Agent 仍能引用早期关键信息。

**Architecture:** `sessions` 管 JSONL 落盘与重放(文件 = 完整事实);`context` 管 token 估算与压缩(内存视图 = 发给模型的现状)——**落盘与压缩彻底解耦**,resume 重放完整历史后由下次 run 起点的压缩钩子按需收敛。`prompts` 包把 system/compact 两份提示词外置为数据文件。`agent` 在 run 起点接压缩钩子、成功路径加 on_turn_commit 回调;`cli` 加 argparse `--resume` 与斜杠命令。

**Tech Stack:** 不变——Python 3.10.12、openai SDK(唯一运行时依赖)、pytest(仅开发依赖)、DeepSeek `deepseek-chat`。新增全部用标准库(json / pathlib / secrets / datetime / importlib.resources / argparse / math)。

**Spec:** `docs/superpowers/specs/2026-10-20-sproutcode-design.md` §3 v2 行、§4.1 包结构、§4.4 上下文管理、§5 错误处理(进程层中断落盘)、§6 测试策略(会话层回环、resume 等价性)、§8 演进笔记。

## 已确认的设计决策(与作者对齐)

1. **会话目录全局化**:`~/.sproutcode/sessions/`,不污染用户项目;会话 meta 记录发起时 cwd。
2. **文件保留完整历史**:压缩只改内存视图、不写 compact 事件;代价是恢复超长会话后首次 run 多一次摘要调用(记入演进笔记取舍)。
3. **压缩检查在 run 起点**(user 消息追加前)执行:天然覆盖 resume 重放后的超长历史;摘要调用失败时 user 消息尚未入历史,`task_start` 回退语义不受影响。

## Global Constraints

- 运行时依赖仅 `openai` SDK,CLI/新模块只用标准库;不引 tokenizer(token 估算按 spec §4.4 字符近似)
- 带 `tool_calls` 的 assistant 消息原样入历史、每个 `tool_call_id` 必有 `role=tool` 回复、任务失败/中断回退到本轮 user 之前——**v1 硬语义零变更**
- `client.py` 不动(摘要复用 `chat`,传 `tools=[]`)
- 中断/失败的本轮不落盘(与回退语义对齐,满足 spec §5"KeyboardInterrupt 不产生半截消息落盘")
- 不做 spec §5 的"API 超限强制压缩重试"(归后续错误处理主题),不做 v3 权限
- 每任务先写测试确认失败,再实现到全绿;子代理产出经主会话审查后才提交

## 文件结构(v2 终态)

```
sproutcode/
├── __init__.py          # 版本号 0.1.0 → 0.2.0
├── __main__.py          # 不动
├── cli.py               # argparse --resume、斜杠命令、CliState、会话接线
├── agent.py             # run 起点压缩钩子、on_turn_commit/on_compact、外置提示词默认
├── client.py            # 不动
├── config.py            # +5 常量;SYSTEM_PROMPT 常量外移进 prompts/
├── context.py           # 新:estimate_tokens + ContextManager.maybe_compact
├── sessions.py          # 新:SessionStore/Session/SessionMeta/SessionSummary
├── tools/               # 不动
└── prompts/             # 新:提示词外置
    ├── __init__.py      # load(name) -> str(importlib.resources)
    ├── system.txt       # 原 SYSTEM_PROMPT 全文迁入
    └── compact.txt      # 摘要指令
tests/
├── test_prompts.py      # 新
├── test_sessions.py     # 新
├── test_context.py      # 新
├── test_agent.py        # 扩:FakeLLM 记录 tools + 压缩/提交回调用例
└── test_cli.py          # 扩:handle_slash、format_session_list、parse_args
pyproject.toml           # + package-data
```

---

### Task 1: 保存本计划(主会话执行)

- [x] **Step 1:** 本文档全文写入 `docs/superpowers/plans/2026-10-21-v2-session-context.md`
- [x] **Step 2:** `git add docs/superpowers/plans/2026-10-21-v2-session-context.md && git commit -m "docs: v2 实现计划"`

---

### Task 2: prompts 外置 + config 常量【子代理写文件,主会话审查提交】

**Files:**
- Create: `sproutcode/prompts/__init__.py`
- Create: `sproutcode/prompts/system.txt`
- Create: `sproutcode/prompts/compact.txt`
- Create: `tests/test_prompts.py`
- Modify: `sproutcode/config.py`(删 SYSTEM_PROMPT,加 5 常量)
- Modify: `sproutcode/agent.py`(默认提示词改读外置文件,仅此一处)
- Modify: `pyproject.toml`(加 package-data)

**Interfaces:**
- Produces: `prompts.load(name: str) -> str`;config 新常量 `SESSIONS_DIR / CONTEXT_BUDGET_TOKENS / COMPACT_THRESHOLD / KEEP_RECENT_TURNS / MAX_SUMMARY_MSG_CHARS`

- [ ] **Step 1: 先写测试 `tests/test_prompts.py`**

```python
import pytest

from sproutcode.agent import Agent
from sproutcode.prompts import load


def test_load_system_prompt_nonempty_and_key_sentence():
    text = load("system")
    assert "SproutCode" in text
    assert "工具" in text
    assert text.strip() == text


def test_load_compact_prompt_mentions_summary():
    assert "摘要" in load("compact")


def test_load_missing_prompt_raises():
    with pytest.raises(FileNotFoundError):
        load("nope")


def test_agent_default_prompt_comes_from_external_file():
    agent = Agent(client=None)
    assert agent.messages[0]["role"] == "system"
    assert agent.messages[0]["content"] == load("system")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_prompts.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'sproutcode.prompts'`

- [ ] **Step 3: 写入 `sproutcode/prompts/__init__.py`**

```python
from __future__ import annotations

from importlib import resources


def load(name: str) -> str:
    """按名字读取打包在 sproutcode.prompts 里的提示词文本。

    提示词外置成数据文件:调提示词不用动代码,提示词本身也成了
    可 diff、可独立演进的工件;importlib.resources 让源码树与安装态都能读。
    """
    return (resources.files(__package__) / f"{name}.txt").read_text(encoding="utf-8")
```

- [ ] **Step 4: 写入 `sproutcode/prompts/system.txt`**(原 config.SYSTEM_PROMPT 逐字迁入,文件不加首尾空行)

```
你是 SproutCode(码苗),一个在终端里工作的 Coding Agent。
你可以调用工具读写文件、执行 shell 命令,帮用户完成编程任务。
- 动手优先:用工具实际完成,而不是只给建议;
- 工具报错时读错误信息,调整参数或命令后重试;
- 完成后用简短中文说明做了什么、结果如何。
```

- [ ] **Step 5: 写入 `sproutcode/prompts/compact.txt`**

```
请把下面这段 Coding Agent 会话的早期对话压缩成一份摘要,供后续对话延续上下文。
要求:
- 保留用户提出的目标与关键约束(文件路径、命名、命令与输出等具体信息);
- 保留已完成的操作及其结论、未完成的事项;
- 直接输出摘要正文,不要任何开场白或客套。
```

- [ ] **Step 6: 修改 `sproutcode/config.py`**——删除 `SYSTEM_PROMPT = (...)` 整块,新增:

```python
SESSIONS_DIR = os.path.expanduser("~/.sproutcode/sessions")  # 会话 JSONL 全局目录,不污染用户项目
CONTEXT_BUDGET_TOKENS = 48_000  # 上下文预算(deepseek-chat 64K 窗口,近似估算偏低,再留余量)
COMPACT_THRESHOLD = 0.7         # 估算超过预算的 70% 即触发压缩(spec §4.4 默认)
KEEP_RECENT_TURNS = 4           # 压缩时保留最近 N 个 user 轮的原文
MAX_SUMMARY_MSG_CHARS = 1_500   # 摘要输入中单条消息的截断长度,防摘要调用自爆上下文
```

- [ ] **Step 7: 修改 `sproutcode/agent.py`**(仅默认提示词来源;其余不动)

```python
# 顶部 import 区:去掉 SYSTEM_PROMPT,加 prompts
from .config import MAX_ROUNDS
from .prompts import load as load_prompt

# __init__ 签名与赋值改为:
        system_prompt: str | None = None,
        ...
        # 提示词外置:默认读 prompts/system.txt,改提示词不用动代码。
        self.messages: list[dict] = [
            {
                "role": "system",
                "content": (
                    system_prompt
                    if system_prompt is not None
                    else load_prompt("system")
                ),
            }
        ]
```

- [ ] **Step 8: 修改 `pyproject.toml`**——追加:

```toml
[tool.setuptools.package-data]
sproutcode.prompts = ["*.txt"]
```

- [ ] **Step 9: 运行全量测试**

Run: `.venv/bin/python -m pytest -v`
Expected: 全部通过(v1 的 22 个 + 本任务 4 个)

- [ ] **Step 10: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/prompts/ tests/test_prompts.py sproutcode/config.py sproutcode/agent.py pyproject.toml
git commit -m "feat: v2 系统提示词外置与配置常量"
```

---

### Task 3: sessions.py 会话 JSONL 落盘与恢复【子代理写文件,主会话审查提交】

**Files:**
- Create: `sproutcode/sessions.py`
- Create: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `SESSIONS_DIR` from config
- Produces: `SessionMeta(id, created_at, model, cwd)`;`SessionSummary(id, created_at, message_count, preview)`;`Session(path, meta)` with `.append_messages(list[dict])` / `.load_messages() -> list[dict]`;`SessionStore(sessions_dir=None)` with `.new(*, model, cwd=None) -> Session` / `.open(id_prefix) -> Session` / `.list(limit=10) -> list[SessionSummary]`

- [ ] **Step 1: 先写测试 `tests/test_sessions.py`**

```python
import json
import os

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
```

注:`test_list_returns_summaries_newest_first` 依赖 mtime 排序,同一秒内两次创建可能乱序——若实测抖动,用 `os.utime(path, (t, t))` 显式设定两个文件的 mtime 后再断言(优先直接实现成稳定排序)。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_sessions.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'sproutcode.sessions'`

- [ ] **Step 3: 写入 `sproutcode/sessions.py`**

```python
from __future__ import annotations

import json
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import SESSIONS_DIR


def _new_session_id(now: datetime) -> str:
    """时间前缀 + 3 字节随机 hex:文件名可按时间排序,前缀短到够 --resume 补全。"""
    return now.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


@dataclass(frozen=True)
class SessionMeta:
    id: str
    created_at: str
    model: str
    cwd: str


@dataclass(frozen=True)
class SessionSummary:
    id: str
    created_at: str
    message_count: int
    preview: str


def _first_user_preview(messages: list[dict[str, Any]], limit: int = 40) -> str:
    for message in messages:
        if message.get("role") == "user" and message.get("content"):
            text = str(message["content"]).strip().replace("\n", " ")
            return text[:limit] + ("…" if len(text) > limit else "")
    return "(无用户消息)"


class Session:
    """一个会话 = 一个 JSONL 文件:首行 meta,之后每条消息一行。

    只追加、只重放,从不改写历史——文件是完整事实;上下文压缩只发生在
    发给模型的内存视图里,所以 resume 永远能拿回完整对话。
    """

    def __init__(self, path: Path, meta: SessionMeta) -> None:
        self.path = path
        self.meta = meta

    def append_messages(self, messages: list[dict[str, Any]]) -> None:
        """按轮粒度追加:一次 agent.run 成功后整轮写入,中断轮不落盘。"""
        ts = datetime.now().isoformat(timespec="seconds")
        with open(self.path, "a", encoding="utf-8") as f:
            for message in messages:
                f.write(
                    json.dumps({"ts": ts, "message": message}, ensure_ascii=False)
                    + "\n"
                )

    def load_messages(self) -> list[dict[str, Any]]:
        """重放消息行;坏行跳过并警告到 stderr,不因个别坏行丢掉整个会话。"""
        messages: list[dict[str, Any]] = []
        with open(self.path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line or line_no == 1:  # 首行恒为 meta
                    continue
                try:
                    messages.append(json.loads(line)["message"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    print(
                        f"[session] 跳过损坏行 {self.path.name}:{line_no}",
                        file=sys.stderr,
                    )
        return messages


class SessionStore:
    """管理会话目录:新建、按 id 前缀打开、按修改时间列最近。"""

    def __init__(self, sessions_dir: str | Path | None = None) -> None:
        self.sessions_dir = Path(sessions_dir or SESSIONS_DIR)

    def new(self, *, model: str, cwd: str | None = None) -> Session:
        now = datetime.now()
        meta = SessionMeta(
            id=_new_session_id(now),
            created_at=now.isoformat(timespec="seconds"),
            model=model,
            cwd=cwd or str(Path.cwd()),
        )
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self.sessions_dir / f"{meta.id}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"type": "meta", **asdict(meta)}, ensure_ascii=False
                )
                + "\n"
            )
        return Session(path, meta)

    def open(self, id_prefix: str) -> Session:
        """按 id 前缀打开会话;未命中或不唯一都报错,避免恢复错会话。"""
        if not id_prefix:
            raise ValueError("会话前缀为空")
        matches = sorted(self.sessions_dir.glob(f"{id_prefix}*.jsonl"))
        if not matches:
            raise ValueError(f"找不到会话: {id_prefix}")
        if len(matches) > 1:
            raise ValueError(f"会话前缀不唯一({len(matches)} 个命中): {id_prefix}")
        return Session(matches[0], self._read_meta(matches[0]))

    def list(self, limit: int = 10) -> list[SessionSummary]:
        """按修改时间倒序列最近会话,供 /resume 展示。"""
        paths = sorted(
            self.sessions_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        rows: list[SessionSummary] = []
        for path in paths[:limit]:
            session = Session(path, self._read_meta(path))
            messages = session.load_messages()
            rows.append(
                SessionSummary(
                    id=session.meta.id,
                    created_at=session.meta.created_at,
                    message_count=len(messages),
                    preview=_first_user_preview(messages),
                )
            )
        return rows

    def _read_meta(self, path: Path) -> SessionMeta:
        try:
            first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        except (OSError, IndexError, json.JSONDecodeError):
            first = {}
        return SessionMeta(
            id=first.get("id") or path.stem,
            created_at=first.get("created_at", ""),
            model=first.get("model", ""),
            cwd=first.get("cwd", ""),
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_sessions.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/sessions.py tests/test_sessions.py
git commit -m "feat: v2 会话 JSONL 落盘与恢复"
```

---

### Task 4: context.py token 近似计数与上下文压缩【子代理写文件,主会话审查提交】

**Files:**
- Create: `sproutcode/context.py`
- Create: `tests/test_context.py`

**Interfaces:**
- Consumes: `COMPACT_THRESHOLD / CONTEXT_BUDGET_TOKENS / KEEP_RECENT_TURNS / MAX_SUMMARY_MSG_CHARS` from config;`prompts.load("compact")`;`ChatClient.chat(messages, tools=[], on_text=None)`
- Produces: `estimate_text_tokens(text) -> int`;`estimate_tokens(messages) -> int`;`ContextManager(*, budget, threshold, keep_recent, max_summary_msg_chars).maybe_compact(messages, client) -> tuple[list[dict], str | None]`

- [ ] **Step 1: 先写测试 `tests/test_context.py`**

```python
from sproutcode.client import AssistantTurn
from sproutcode.context import (
    ContextManager,
    estimate_text_tokens,
    estimate_tokens,
)


class SummaryFakeLLM:
    """记录请求、返回固定摘要文本的替身,只服务压缩测试。"""

    def __init__(self, summary: str = "早期:用户创建了 a.txt,内容是码苗 v2。") -> None:
        self.summary = summary
        self.requests = []

    def chat(self, messages, tools, on_text=None):
        self.requests.append(
            {"messages": [dict(m) for m in messages], "tools": list(tools)}
        )
        return AssistantTurn(content=self.summary)


def build_messages(turns: int = 6, filler: str = "数据" * 200) -> list[dict]:
    """够长的多轮对话:每轮 user/assistant 各一条,超预算但不至于跑不动。"""
    messages = [{"role": "system", "content": "sys"}]
    for i in range(turns):
        messages.append({"role": "user", "content": f"第{i}轮 {filler}"})
        messages.append({"role": "assistant", "content": f"完成{i} {filler}"})
    return messages


def test_estimate_text_tokens_cjk_vs_ascii():
    # 中文 ≈ 1 字符/token,英文 ≈ 4 字符/token:量级关系正确即可(spec §4.4)
    assert estimate_text_tokens("汉" * 100) == 100
    assert estimate_text_tokens("a" * 100) == 25
    assert estimate_text_tokens("a" * 99) == 25


def test_estimate_tokens_covers_tool_calls_payload():
    plain = [{"role": "user", "content": "hi"}]
    with_tools = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "a.txt"}',
                    },
                }
            ],
        }
    ]
    assert estimate_tokens(plain) > 0
    assert estimate_tokens(with_tools) > estimate_tokens(plain)


def test_maybe_compact_noop_under_threshold():
    client = SummaryFakeLLM()
    ctx = ContextManager(budget=10_000)
    messages = build_messages()

    result, note = ctx.maybe_compact(messages, client)

    assert result is messages
    assert note is None
    assert client.requests == []


def test_maybe_compact_replaces_old_block_and_keeps_recent_turns():
    client = SummaryFakeLLM()
    messages = build_messages(turns=6)
    ctx = ContextManager(budget=300, threshold=1.0, keep_recent=2)

    result, note = ctx.maybe_compact(messages, client)

    assert note and "已压缩" in note
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"
    assert "摘要" in result[1]["content"]
    assert "a.txt" in result[1]["content"]  # 摘要来自 LLM 返回
    assert [m["role"] for m in result[2:]] == ["user", "assistant", "user", "assistant"]
    assert result[2]["content"].startswith("第4轮")  # 保留的是最近 2 个 user 轮
    # 摘要调用:单条输入、不带任何工具
    request = client.requests[0]
    assert request["tools"] == []
    assert len(request["messages"]) == 1
    assert "待压缩的早期对话" in request["messages"][0]["content"]


def test_maybe_compact_never_splits_tool_call_pairs():
    client = SummaryFakeLLM()
    messages = build_messages(turns=4)
    messages.append({"role": "user", "content": "读一下"})
    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": "call_9", "content": "xx"})
    ctx = ContextManager(budget=300, threshold=1.0, keep_recent=1)

    result, _ = ctx.maybe_compact(messages, client)

    assert result[2]["role"] == "user"  # 保留区从 user 消息开始
    # 保留区内不存在悬空 tool 消息:每个 tool_call_id 都有配对的 assistant
    ids = {
        call["id"]
        for m in result
        if m.get("tool_calls")
        for call in m["tool_calls"]
    }
    assert "call_9" in ids
    assert all(
        m["tool_call_id"] in ids for m in result if m.get("role") == "tool"
    )


def test_maybe_compact_truncates_each_message_in_summary_input():
    client = SummaryFakeLLM()
    messages = build_messages(turns=6, filler="长" * 3000)
    ctx = ContextManager(
        budget=100, threshold=1.0, keep_recent=2, max_summary_msg_chars=100
    )

    _, note = ctx.maybe_compact(messages, client)

    assert note
    body = client.requests[0]["messages"][0]["content"]
    rendered = [
        line
        for line in body.splitlines()
        if line.startswith("[user]") or line.startswith("[assistant]")
    ]
    assert rendered
    assert all(len(line) <= len("[assistant] ") + 100 + 1 for line in rendered)


def test_maybe_compact_empty_summary_is_noop():
    client = SummaryFakeLLM(summary="   ")
    messages = build_messages(turns=6)
    ctx = ContextManager(budget=300, threshold=1.0, keep_recent=2)

    result, note = ctx.maybe_compact(messages, client)

    assert result is messages
    assert note is None


def test_maybe_compact_noop_when_no_old_block():
    client = SummaryFakeLLM()
    messages = [{"role": "system", "content": "s"}]
    for _ in range(3):
        messages += [
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "a"},
        ]
    ctx = ContextManager(budget=1, threshold=1.0, keep_recent=4)

    result, note = ctx.maybe_compact(messages, client)

    assert result is messages  # user 轮数 ≤ keep_recent,没有旧块可摘
    assert note is None
    assert client.requests == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_context.py -v`
Expected: FAIL,`ModuleNotFoundError: No module named 'sproutcode.context'`

- [ ] **Step 3: 写入 `sproutcode/context.py`**

```python
from __future__ import annotations

import json
import math
from typing import Any

from .client import ChatClient
from .config import (
    COMPACT_THRESHOLD,
    CONTEXT_BUDGET_TOKENS,
    KEEP_RECENT_TURNS,
    MAX_SUMMARY_MSG_CHARS,
)
from .prompts import load as load_prompt

# 近似规则(spec §4.4):CJK 字符 ≈ 1 token,其余 ≈ 4 字符/token。
# 不引 tokenizer 换零依赖,误差靠预算常量留余量来吸收。
_CJK_RANGES = ((0x4E00, 0x9FFF), (0x3040, 0x30FF), (0x3000, 0x303F), (0xFF00, 0xFFEF))


def _is_cjk(ch: str) -> bool:
    return any(start <= ord(ch) <= end for start, end in _CJK_RANGES)


def estimate_text_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if _is_cjk(ch))
    return cjk + math.ceil((len(text) - cjk) / 4)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """整条消息 json.dumps 后估算:role、键名、tool_calls 开销一并近似进去。"""
    return sum(
        estimate_text_tokens(json.dumps(message, ensure_ascii=False))
        for message in messages
    )


def _render_for_summary(
    messages: list[dict[str, Any]], max_chars: int
) -> str:
    """旧消息块 → 摘要输入文本;单条截断,防摘要调用自己把上下文撑爆。"""
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "?")
        parts = [str(message.get("content") or "")]
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            parts.append(
                f'{function.get("name", "?")}({function.get("arguments", "")})'
            )
        body = " ".join(part for part in parts if part).strip() or "(空)"
        if len(body) > max_chars:
            body = body[:max_chars] + "…"
        lines.append(f"[{role}] {body}")
    return "\n".join(lines)


class ContextManager:
    """超过预算阈值时,把最旧消息块替换成一次 LLM 摘要调用。

    只改内存视图、不动会话文件:文件是完整事实,视图是发给模型的现状。
    切口选在倒数第 keep_recent 个 user 消息——user 轮边界天然不会
    拆散 assistant(tool_calls)/tool 配对。
    """

    def __init__(
        self,
        *,
        budget: int = CONTEXT_BUDGET_TOKENS,
        threshold: float = COMPACT_THRESHOLD,
        keep_recent: int = KEEP_RECENT_TURNS,
        max_summary_msg_chars: int = MAX_SUMMARY_MSG_CHARS,
    ) -> None:
        self.budget = budget
        self.threshold = threshold
        self.keep_recent = keep_recent
        self.max_summary_msg_chars = max_summary_msg_chars

    def maybe_compact(
        self, messages: list[dict[str, Any]], client: ChatClient
    ) -> tuple[list[dict[str, Any]], str | None]:
        """返回 (新 messages, 提示文本);不需要或无法压缩时原样返回。"""
        if estimate_tokens(messages) <= self.budget * self.threshold:
            return messages, None

        user_indexes = [
            i for i, m in enumerate(messages) if m.get("role") == "user"
        ]
        if len(user_indexes) <= self.keep_recent:
            return messages, None
        cut = user_indexes[-self.keep_recent]
        old_block = messages[1:cut]  # messages[0] 恒为 system,永远保留
        if not old_block:
            return messages, None

        prompt = (
            load_prompt("compact")
            + "\n\n=== 待压缩的早期对话 ===\n"
            + _render_for_summary(old_block, self.max_summary_msg_chars)
        )
        summary_turn = client.chat(
            [{"role": "user", "content": prompt}], [], on_text=None
        )
        summary = (summary_turn.content or "").strip()
        if not summary:
            return messages, None

        compacted = (
            [messages[0]]
            + [
                {
                    "role": "user",
                    "content": "(以下是本会话更早对话的摘要,供你延续上下文)\n"
                    + summary,
                }
            ]
            + messages[cut:]
        )
        note = (
            f"已压缩上下文:估算 {estimate_tokens(messages)}→"
            f"{estimate_tokens(compacted)} tokens"
        )
        return compacted, note
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_context.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/context.py tests/test_context.py
git commit -m "feat: v2 token 近似计数与上下文压缩"
```

---

### Task 5: agent.py 接入压缩与会话提交回调【子代理写文件,主会话审查提交】

**Files:**
- Modify: `sproutcode/agent.py`(全文替换)
- Modify: `tests/test_agent.py`(FakeLLM 记录 tools;新增 4 个用例)

**Interfaces:**
- Consumes: `ContextManager.maybe_compact`;`prompts.load`
- Produces: `Agent(client, *, registry=None, max_rounds=MAX_ROUNDS, system_prompt=None, context=None)`;`run(..., on_turn_commit=None, on_compact=None)`;`TurnCommitCallback = Callable[[list[dict]], None]`;`CompactCallback = Callable[[str], None]`

- [ ] **Step 1: 修改 `tests/test_agent.py` 的 FakeLLM**——把 `self.requests.append(copy.deepcopy(messages))` 改为同时记录 tools,并更新既有两处断言:

```python
    def chat(self, messages, tools, on_text=None):
        self.requests.append(
            {"messages": copy.deepcopy(messages), "tools": list(tools)}
        )
```

原 `client.requests[0][-1] == {"role": "user", "content": "hi"}` 改为 `client.requests[0]["messages"][-1] == {"role": "user", "content": "hi"}`;
原 `client.requests[1][-1]["role"] == "tool"` 改为 `client.requests[1]["messages"][-1]["role"] == "tool"`。

- [ ] **Step 2: 在 `tests/test_agent.py` 追加用例**(文件顶部补 `from sproutcode.context import ContextManager`)

```python
def test_agent_commits_exact_turn_slice_on_success():
    client = FakeLLM(AssistantTurn(content="一"))
    agent = Agent(client, registry=make_registry())
    committed = []

    agent.run("hi", on_turn_commit=committed.append)

    assert committed == [
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "一"},
        ]
    ]


def test_agent_does_not_commit_on_exception():
    client = FakeLLM(RuntimeError("boom"))
    agent = Agent(client, registry=make_registry())
    committed = []

    with pytest.raises(RuntimeError, match="boom"):
        agent.run("hi", on_turn_commit=committed.append)

    assert committed == []


def test_agent_compacts_at_run_start_with_toolless_summary_call():
    # 两次 run 各消耗「一次摘要 + 一次正常对话」,共 4 份脚本:
    # budget=10 下第一次 run 后历史仍超预算,第二次 run 起点会再压一次。
    client = FakeLLM(
        AssistantTurn(content="早期摘要"),
        AssistantTurn(content="好的"),
        AssistantTurn(content="再摘要"),
        AssistantTurn(content="好"),
    )
    agent = Agent(
        client,
        registry=make_registry(),
        context=ContextManager(budget=10, threshold=1.0, keep_recent=1),
    )
    # 预置一段超预算历史,让 run 起点必触发压缩
    agent.messages += [
        {"role": "user", "content": "第一轮" + "长" * 200},
        {"role": "assistant", "content": "完成" + "长" * 200},
        {"role": "user", "content": "第二轮" + "长" * 200},
        {"role": "assistant", "content": "完成" + "长" * 200},
    ]
    notes = []

    reply = agent.run("继续", on_compact=notes.append)

    assert reply == "好的"
    assert notes and "已压缩" in notes[0]
    assert client.requests[0]["tools"] == []        # 第一次调用是摘要,不带工具
    assert client.requests[1]["tools"]              # 第二次是正常对话,带工具
    assert agent.messages[1]["role"] == "user"      # system 之后第一条是摘要注入
    assert "早期摘要" in agent.messages[1]["content"]
    # 本轮提交的切片不含摘要(摘要只存在于视图,永不落盘);
    # 第二次 run 起点仍超预算会再压一次,但 task_start 在压缩之后,
    # 提交切片仍精确始于本轮 user 消息。
    committed = []
    agent.run("再继续", on_turn_commit=committed.append)
    assert committed[0][0] == {"role": "user", "content": "再继续"}


def test_agent_max_rounds_turn_still_commits():
    call = ToolCall(id="call_1", name="echo", arguments='{"text": "x"}')
    client = FakeLLM(*([AssistantTurn(content=None, tool_calls=[call])] * 3))
    agent = Agent(client, registry=make_registry(), max_rounds=3)
    committed = []

    agent.run("loop", on_turn_commit=committed.append)

    assert len(committed) == 1
    assert committed[0][0]["role"] == "user"
    assert sum(1 for m in committed[0] if m["role"] == "tool") == 3
```

- [ ] **Step 3: 运行新用例确认失败**

Run: `.venv/bin/python -m pytest tests/test_agent.py -v`
Expected: 新用例 FAIL(`Agent.__init__() got an unexpected keyword argument 'context'` / `on_turn_commit`),既有用例仍绿

- [ ] **Step 4: 写入 `sproutcode/agent.py`**(全文)

```python
from __future__ import annotations

from typing import Callable

from .client import ChatClient, TextCallback
from .config import MAX_ROUNDS
from .context import ContextManager
from .prompts import load as load_prompt
from .tools import ToolRegistry, registry as default_registry

ToolCallCallback = Callable[[str, str], None]
ToolResultCallback = Callable[[str, bool], None]
TurnCommitCallback = Callable[[list[dict]], None]
CompactCallback = Callable[[str], None]


class Agent:
    """持有消息历史并驱动 LLM↔工具循环;messages 就是 Agent 的全部状态。"""

    def __init__(
        self,
        client: ChatClient,
        *,
        registry: ToolRegistry | None = None,
        max_rounds: int = MAX_ROUNDS,
        system_prompt: str | None = None,
        context: ContextManager | None = None,
    ) -> None:
        self.client = client
        self.registry = registry or default_registry
        self.max_rounds = max_rounds
        # 提示词外置:默认读 prompts/system.txt,调提示词不用动代码。
        self.messages: list[dict] = [
            {
                "role": "system",
                "content": (
                    system_prompt
                    if system_prompt is not None
                    else load_prompt("system")
                ),
            }
        ]
        self.context = context or ContextManager()

    def run(
        self,
        user_input: str,
        *,
        on_text: TextCallback | None = None,
        on_tool_call: ToolCallCallback | None = None,
        on_tool_result: ToolResultCallback | None = None,
        on_turn_commit: TurnCommitCallback | None = None,
        on_compact: CompactCallback | None = None,
    ) -> str:
        """处理一条用户消息,直到模型不再发起工具调用。"""
        # 先压缩再进本轮:resume 重放出的超长历史也在这里被收敛。
        # 压缩若失败,user 消息尚未入历史,不影响下方 task_start 的回退语义。
        self.messages, note = self.context.maybe_compact(
            self.messages, self.client
        )
        if note and on_compact is not None:
            on_compact(note)

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
                    if on_turn_commit is not None:
                        on_turn_commit(self.messages[task_start:])
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
            if on_turn_commit is not None:
                on_turn_commit(self.messages[task_start:])
            return fallback
        except BaseException:
            del self.messages[task_start:]
            raise
```

- [ ] **Step 5: 运行全量测试**

Run: `.venv/bin/python -m pytest -v`
Expected: 全部通过

- [ ] **Step 6: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/agent.py tests/test_agent.py
git commit -m "feat: v2 Agent 接入压缩与会话提交"
```

---

### Task 6: cli.py 斜杠命令与 --resume 入口【子代理写文件,主会话审查提交】

**Files:**
- Modify: `sproutcode/cli.py`(全文替换)
- Modify: `sproutcode/__init__.py`(版本号 0.2.0)
- Modify: `tests/test_cli.py`(追加用例)

**Interfaces:**
- Consumes: `Agent.run(..., on_turn_commit, on_compact)`;`SessionStore/Session`;`prompts.load`
- Produces: `parse_args(argv=None)`;`CliState(agent, session, store)`;`handle_slash(user, state) -> bool`;`restore(state, id_prefix) -> None`;`format_session_list(rows) -> str`;`main(argv=None)`

- [ ] **Step 1: 在 `tests/test_cli.py` 追加用例**

```python
from sproutcode.agent import Agent
from sproutcode.cli import (
    CliState,
    format_session_list,
    handle_slash,
    parse_args,
)
from sproutcode.prompts import load as load_prompt
from sproutcode.sessions import SessionStore


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
```

- [ ] **Step 2: 运行新用例确认失败**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: 新用例 FAIL,`ImportError: cannot import name 'CliState'`

- [ ] **Step 3: 写入 `sproutcode/cli.py`**(全文;Renderer/preview_arguments/build_client 逐字保留)

```python
from __future__ import annotations

import argparse
import sys

from . import __version__
from .agent import Agent
from .client import LLMClient
from .config import MODEL, load_api_key
from .prompts import load as load_prompt
from .sessions import Session, SessionStore

DIM = "\033[2m"
RESET = "\033[0m"
PROMPT = "码苗> "

HELP_TEXT = (
    "/help              显示本帮助\n"
    "/resume [id前缀]   列出最近会话;带参数则恢复指定会话\n"
    "/clear             清空当前对话并开启新会话\n"
    "/exit              退出(等价 /quit)"
)


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sproutcode", description="SproutCode(码苗)终端 Coding Agent"
    )
    parser.add_argument(
        "--resume", metavar="ID前缀", help="恢复指定会话后继续对话"
    )
    return parser.parse_args(argv)


def format_session_list(rows) -> str:
    """把 /resume 列表渲染成对齐的多行文本。"""
    if not rows:
        return "(没有可恢复的会话)"
    return "\n".join(
        f"{row.id}  {row.created_at}  {row.message_count}条  {row.preview}"
        for row in rows
    )


class CliState:
    """REPL 的可变状态:斜杠命令与主循环共享的只有这三样。"""

    def __init__(self, agent: Agent, session: Session, store: SessionStore) -> None:
        self.agent = agent
        self.session = session
        self.store = store


def restore(state: CliState, id_prefix: str) -> None:
    """重放指定会话进 agent 并切换当前会话文件;找不到时 ValueError 往上抛。"""
    state.session = state.store.open(id_prefix)
    # system 用当前外置提示词,不用会话里存的那份老提示词
    state.agent.messages = (
        [{"role": "system", "content": load_prompt("system")}]
        + state.session.load_messages()
    )


def handle_slash(user: str, state: CliState) -> bool:
    """处理斜杠命令;返回 True 表示该输入已被消费(/exit 在主循环单独处理)。"""
    command, _, arg = user.partition(" ")
    arg = arg.strip()

    if command == "/help":
        print(HELP_TEXT)
    elif command == "/clear":
        state.agent.messages = [{"role": "system", "content": load_prompt("system")}]
        state.session = state.store.new(model=MODEL)
        print(f"(已清空对话,新会话 {state.session.meta.id})")
    elif command == "/resume":
        if not arg:
            print(format_session_list(state.store.list(limit=10)))
        else:
            try:
                restore(state, arg)
            except ValueError as exc:
                print(f"[resume 失败] {exc}")
            else:
                print(
                    f"(已恢复会话 {state.session.meta.id},"
                    f"{len(state.agent.messages) - 1} 条消息)"
                )
    else:
        print(f"未知命令 {command},可用:/help /resume /clear /exit")
    return True


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    client = build_client()
    agent = Agent(client)
    store = SessionStore()

    if args.resume:
        try:
            session = store.open(args.resume)
        except ValueError as exc:
            sys.exit(f"resume 失败:{exc}")
        state = CliState(agent, session, store)
        restore(state, args.resume)
        print(f"(已恢复会话 {session.meta.id},{len(agent.messages) - 1} 条消息)")
    else:
        session = store.new(model=MODEL)
        state = CliState(agent, session, store)

    print(
        f"SproutCode(码苗)v{__version__} · {MODEL} · "
        f"会话 {state.session.meta.id} · /help 帮助"
    )
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
        if user.startswith("/"):
            handle_slash(user, state)
            continue

        renderer = Renderer()
        try:
            reply = agent.run(
                user,
                on_text=renderer.on_text,
                on_tool_call=renderer.on_tool_call,
                on_tool_result=renderer.on_tool_result,
                on_turn_commit=state.session.append_messages,
                on_compact=lambda note: print(
                    f"  {DIM}({note}){RESET}", flush=True
                ),
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

- [ ] **Step 4: 修改 `sproutcode/__init__.py`**:`__version__ = "0.2.0"`

- [ ] **Step 5: 运行全量测试**

Run: `.venv/bin/python -m pytest -v`
Expected: 全部通过

- [ ] **Step 6: 包入口离线自检**

Run: `printf '/exit\n' | .venv/bin/python -m sproutcode`
Expected: 打印 v2 banner(含会话 id)后正常退出,无 ImportError;`~/.sproutcode/sessions/` 出现一个含 meta 行的 jsonl

- [ ] **Step 7: Commit(主会话,审查 diff 后)**

```bash
git add sproutcode/cli.py sproutcode/__init__.py tests/test_cli.py
git commit -m "feat: v2 斜杠命令与 --resume 入口"
```

---

### Task 7: 真机验收(主会话执行,门禁)

**Files:** 无新增;验收后清理临时产物

- [ ] **Step 1: 全量测试**

Run: `.venv/bin/python -m pytest -q`
Expected: 全部通过(22 旧 + ~26 新)

- [ ] **Step 2: 真机 resume 闭环**

```bash
printf '请创建 /tmp/sprout-v2-demo/note.txt,内容为一行:码苗 v2 会话测试\n/exit\n' | .venv/bin/python -m sproutcode | tee /tmp/v2-run1.log
```
从输出抓出 banner 里的会话 id(形如 `20261021-xxxxxx-xxxxxx`),然后:

```bash
printf '不要用任何工具,凭对话记忆回答:我上一条消息让你创建了什么文件?内容是什么?\n/exit\n' | .venv/bin/python -m sproutcode --resume <id> | tee /tmp/v2-run2.log
```
判定:run2 回答引用 note.txt 及其内容(证明恢复的是记忆,不是靠工具现场发现);run2 结束后该会话 jsonl 追加了新一轮消息(继续同一文件)。

- [ ] **Step 3: JSONL 落盘格式检查**

```bash
.venv/bin/python - <<'EOF'
import json, os, glob
path = sorted(glob.glob(os.path.expanduser("~/.sproutcode/sessions/*.jsonl")))[-1]
lines = open(path, encoding="utf-8").read().splitlines()
meta = json.loads(lines[0])
assert meta["type"] == "meta" and meta["model"] == "deepseek-chat"
for line in lines:
    json.loads(line)
roles = [json.loads(l)["message"]["role"] for l in lines[1:]]
assert "user" in roles and "assistant" in roles
print(f"JSONL 检查通过:{path} 共 {len(lines)} 行")
EOF
```

- [ ] **Step 4: 真机压缩召回(暗号实验)**

```bash
.venv/bin/python - <<'EOF' | tee /tmp/v2-compact.log
from sproutcode.agent import Agent
from sproutcode.cli import build_client
from sproutcode.context import ContextManager

# 预算压到 600 估算 token:一两轮后必触发压缩,keep_recent=1 只保最后一轮
agent = Agent(build_client(), context=ContextManager(budget=600, threshold=1.0, keep_recent=1))
print(agent.run("请记住:项目暗号是「绿芽7749」。先复述一遍暗号。"))
for i in range(6):
    agent.run(f"第{i}轮背景资料:" + "豆苗生长需要光照水分与养分," * 80)
reply = agent.run("不要用工具,只回答:项目暗号是什么?")
print("最终回答:", reply)
assert "7749" in reply, "暗号丢了:摘要没有保住早期关键信息"
assert agent.messages[1]["role"] == "user" and "摘要" in agent.messages[1]["content"]
print("压缩召回验收通过")
EOF
```
判定:输出含"压缩召回验收通过";过程中能看到 on_compact 的「已压缩上下文」提示属正常(此脚本未接回调,不看这条)。

- [ ] **Step 5: 清理验收产物**

Run: `rm -rf /tmp/sprout-v2-demo /tmp/v2-run1.log /tmp/v2-run2.log /tmp/v2-compact.log`
(会话 jsonl 保留——它们是功能产物,不是垃圾。)

- [ ] **Step 6: 任一失败修到通过再继续,不得跳进门禁后的 tag 步骤**

---

### Task 8: 演进笔记、AGENTS.md 与 tag(主会话执行)

**Files:**
- Create: `docs/journey/v2-session-context.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: 写演进笔记(固定三段,基于真实验收输出)**

- **改了什么**:新增 sessions(JSONL 落盘/前缀恢复/列表)、context(字符近似 token 计数 + 阈值压缩 + 摘要注入)、prompts(提示词外置);Agent 接 on_turn_commit/on_compact 与 run 起点压缩钩子;CLI 加 --resume 与 /help /clear /resume;版本 0.2.0。
- **为什么**:会话/上下文是 Agent 区别于聊天机器人的分水岭(spec §3);REPL 一关历史全丢是 v1 最大缺口;提示词外置让它成为可 diff 的工件。
- **踩坑与收获**:至少写清——文件=完整事实/压缩=内存视图的取舍(恢复长会话多一次摘要调用);压缩切口必须在 user 轮边界(不拆 tool_calls/tool 对);run 起点压缩与 task_start 回退的先后关系;字符近似计数的误差与预算余量;FakeLLM 记录 tools 的测试基建演进。以实际调试记录为准补充。

- [ ] **Step 2: 更新 `AGENTS.md`**

- 进度行:v0、v1、v2 均已完成并真机验收通过,tag `v0` / `v1` / `v2`;
- 技术约定补充:会话目录 `~/.sproutcode/sessions/`;压缩检查在 `Agent.run` 起点、只改内存视图不改会话文件;提示词在 `sproutcode/prompts/*.txt`;
- 已踩过的坑:如有新坑(以实际为准)追加。

- [ ] **Step 3: 提交文档**

```bash
git add docs/journey/v2-session-context.md AGENTS.md
git commit -m "docs: v2 演进笔记与工作指南更新"
```

- [ ] **Step 4: 打 tag 并推送**

```bash
git tag v2
git push origin main --tags
```

- [ ] **Step 5: 验证**

Run: `git tag --list`(应含 v0/v1/v2)、`git status --short`(应为空)、`git log --oneline -10`

---

## 计划自检

- **Spec 覆盖**:§3 v2 行四项交付(Task 2 提示词外置、Task 3 落盘+resume、Task 4 计数+压缩、Task 6 斜杠命令);§4.4 近似规则/70% 阈值/保 system+近 N 轮(Task 4);§5 中断不落半截消息(Task 3 轮粒度 + Task 5 回退路径不 commit);§6 会话层回环与 resume 等价性(Task 3/5)、FakeLLM(Task 4/5);§8 演进笔记(Task 8)。
- **占位符**:无 TBD/TODO;所有代码与断言均为可执行内容。
- **类型一致性**:`maybe_compact` 返回 `(list, str|None)` 在 context/agent/测试三处一致;`on_turn_commit` 收到的是 messages 切片;`SessionStore.list()` 返回 `SessionSummary`,`format_session_list` 按其字段渲染;FakeLLM.requests 结构变更后既有断言已同步更新。
- **纪律**:不做 v3 权限与超限强制重试;`client.py`、`tools/`、`__main__.py` 零改动。
