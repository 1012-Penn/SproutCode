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
