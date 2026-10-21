"""会话持久化:把每轮对话追加写进 JSONL 文件,支持恢复与列出。

什么是 JSONL:每行一个独立的 JSON 对象(首行是会话元信息,之后每行
一条消息)。为什么选它而不是一个大 JSON:逐行追加只需 open("a") 一行
代码,天然抗中断——写了一半崩了,最多丢最后一行,前面的都完好;而改写
大 JSON 必须整文件重序列化,既慢又危险。

核心设计是"文件是完整事实":只追加、只重放、从不改写历史。上下文压缩
(context.py)只发生在发给模型的内存视图里,所以任何时候 resume 都能
拿回未压缩的完整对话。与 agent.py 的协作点:on_turn_commit 回调在每轮
成功后调用 append_messages 落盘,中断轮不落盘。
"""

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
        """按轮粒度追加:一次 agent.run 成功后整轮写入,中断轮不落盘。

        轮粒度是刻意选择:如果逐条实时写,半轮崩掉会留下"有 tool_calls
        但缺 tool 回复"的残缺历史,resume 重放后第一条 API 请求就 400
        (与 agent.py 的 task_start 回退防的是同一个问题,这里是落盘侧)。
        所有消息共用同一个时间戳,方便事后按轮分组。
        """
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
