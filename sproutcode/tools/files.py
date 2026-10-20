from __future__ import annotations

import os

from . import tool


@tool
def read_file(path: str) -> str:
    """读取指定路径文本文件的内容,返回全文。

    Args:
        path: 文件路径。
    """
    with open(path, encoding="utf-8") as f:
        return f.read()


@tool
def write_file(path: str, content: str) -> str:
    """把文本内容整体写入指定文件(覆盖),父目录不存在时自动创建。

    Args:
        path: 目标文件路径。
        content: 完整文件内容。
    """
    # 自动建父目录把可预见的失败在工具内消化,省掉模型
    # "目录不存在→报错→mkdir→重写"一整轮往返。
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path}({len(content)} 字符)"


@tool
def list_files(path: str = ".") -> str:
    """列出目录下的一级条目,目录名以 / 结尾。

    Args:
        path: 目录路径,默认为当前目录。
    """
    # 只列一层,不做递归:没有输出截断之前(v3 才做),递归遍历大仓库
    # 可能一次把上下文撑爆;要看深层结构,模型可以自己再列下一层。
    with os.scandir(path) as entries:
        names = sorted(
            entry.name + ("/" if entry.is_dir() else "") for entry in entries
        )
    return "\n".join(names) if names else "(空目录)"
