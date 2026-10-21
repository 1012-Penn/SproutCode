"""内置 shell 工具:让模型能在本机执行任意命令。

这是能力最大、也最"危险"的一个工具——模型拿到的是字符串,直接交给
shell=True 的 subprocess 执行。v2 阶段尚无权限门禁(那是 v3 的主题),
当前的安全边界只有两个:SHELL_TIMEOUT 兜底防止命令挂死,以及把非零
退出当合法结果回传、由模型自行判断下一步。
"""

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
    # 若把非零退出当异常抛出,注册表会包成 [ERROR],模型反而分不清
    # "命令执行了但失败"(grep 没命中也是 exit 1)和"命令根本没跑成"。
    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=SHELL_TIMEOUT,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return f"[exit {proc.returncode}] {output.strip() or '(无输出)'}"
