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
