from __future__ import annotations

import os

# 所有"改一行就能换环境"的东西集中在这里:换模型/厂商只改 BASE_URL 和 MODEL,
# 这是坚持 OpenAI 兼容协议换来的可移植性。
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"
MAX_ROUNDS = 25           # 单任务工具调用轮数上限,防失控(保险丝,不是常态工况)
SHELL_TIMEOUT = 60        # 秒;v1 沿用 v0,正式的超时/截断策略是 v3 的主题
ENV_FILE = "/root/.env"   # API Key 兜底来源,环境变量优先

SYSTEM_PROMPT = (
    "你是 SproutCode(码苗),一个在终端里工作的 Coding Agent。\n"
    "你可以调用工具读写文件、执行 shell 命令,帮用户完成编程任务。\n"
    "- 动手优先:用工具实际完成,而不是只给建议;\n"
    "- 工具报错时读错误信息,调整参数或命令后重试;\n"
    "- 完成后用简短中文说明做了什么、结果如何。"
)


def load_api_key() -> str | None:
    """环境变量优先;否则解析 /root/.env 中 DEEPSEEK_API_KEY=... 一行。"""
    # 安全基线:Key 只从环境变量/本地 .env 读,永不写进代码、永不进 git。
    # 手写最小 .env 解析(只认 KEY=VALUE 行),换取"除 openai SDK 外零依赖"。
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return None
