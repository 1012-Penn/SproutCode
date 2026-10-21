from __future__ import annotations

from importlib import resources


def load(name: str) -> str:
    """按名字读取打包在 sproutcode.prompts 里的提示词文本。

    提示词外置成数据文件:调提示词不用动代码,提示词本身也成了
    可 diff、可独立演进的工件;importlib.resources 让源码树与安装态都能读。
    """
    return (resources.files(__package__) / f"{name}.txt").read_text(encoding="utf-8")
