"""提示词装载:system.txt / compact.txt 等提示词外置为包内数据文件。

为什么要外置:提示词是调出来而非写出来的,迭代频率远高于代码。外置成
.txt 后,改提示词不产生代码 diff、不触发测试,还能像配置一样单独 review。
本模块只提供一个 load(name),按文件名读取;系统提示词在 agent.py,
压缩提示词在 context.py 各自调用。
"""

from __future__ import annotations

from importlib import resources


def load(name: str) -> str:
    """按名字读取打包在 sproutcode.prompts 里的提示词文本。

    提示词外置成数据文件:调提示词不用动代码,提示词本身也成了
    可 diff、可独立演进的工件;importlib.resources 让源码树与安装态都能读
    (直接 open 相对路径在 pip 安装后会失效,resources 走的是包定位机制)。
    """
    return (resources.files(__package__) / f"{name}.txt").read_text(encoding="utf-8")
