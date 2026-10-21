"""SproutCode(码苗):一个从零实现的教学向终端 Coding Agent。

包结构一览(依赖方向自上而下,config 为最底层):
- config.py    常量与 API Key(不依赖包内任何模块)
- client.py    LLM 客户端,流式聚合为统一形态 AssistantTurn
- tools/       工具注册表 + 内置文件/shell 工具
- prompts/     外置提示词文件与装载
- agent.py     主循环:消息历史 + LLM↔工具循环
- context.py   上下文预算估算与压缩
- sessions.py  JSONL 会话持久化(resume / list)
- cli.py       REPL 与斜杠命令,入口 python -m sproutcode
"""

__version__ = "0.2.0"
