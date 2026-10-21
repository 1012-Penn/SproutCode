"""命令行 REPL:把 Agent、会话存储和终端渲染粘合成用户可见的产品。

分层位置:最上层模块,依赖 agent/client/sessions/prompts 全部下层。
职责刻意保持"薄"——所有决策逻辑都在下层,这里只做三件事:

1. 读用户输入,按首字符分派:斜杠命令(/help /resume /clear /exit)
   本地处理,普通文本交给 agent.run;
2. 用 Renderer 把 Agent 的回调事件(流式文本/工具调用/压缩提示)
   渲染成终端输出,正文亮色、过程暗色;
3. 把每轮成功的结果通过 on_turn_commit 回调交给 Session 落盘。

入口链:python -m sproutcode → __main__.py → 这里的 main()。
"""

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
    """正文亮色、过程暗色;流式文本到达时才打印一次对话前缀。

    started 标志解决流式渲染的一个经典问题:收到第一个文本 chunk 之前,
    我们不知道这轮模型是直接回答(要打 "码苗> " 前缀)还是只发工具调用
    (不该有前缀)。所以前缀延迟到第一片文本到达时才打印。ANSI 转义码
    (\\033[2m 等)是终端的标准暗色/重置指令,不需要 curses 之类的库。
    """

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
    # 错误分级:Ctrl+C 视为正常操作(回退已由 Agent 内部完成,这里只需
    # 安抚用户);其他异常(网络/认证等)统一打印 [API 错误] 后继续 REPL——
    # 一次失败的请求不该让整个会话进程退出。
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
