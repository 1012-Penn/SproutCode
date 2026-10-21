from __future__ import annotations

import os

# 所有"改一行就能换环境"的东西集中在这里:换模型/厂商只改 BASE_URL 和 MODEL,
# 这是坚持 OpenAI 兼容协议换来的可移植性。
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"
MAX_ROUNDS = 25           # 单任务工具调用轮数上限,防失控(保险丝,不是常态工况)
SHELL_TIMEOUT = 60        # 秒;v1 沿用 v0,正式的超时/截断策略是 v3 的主题
ENV_FILE = "/root/.env"   # API Key 兜底来源,环境变量优先

SESSIONS_DIR = os.path.expanduser("~/.sproutcode/sessions")  # 会话 JSONL 全局目录,不污染用户项目
CONTEXT_BUDGET_TOKENS = 48_000  # 上下文预算(deepseek-chat 64K 窗口,近似估算偏低,再留余量)
COMPACT_THRESHOLD = 0.7         # 估算超过预算的 70% 即触发压缩(spec §4.4 默认)
KEEP_RECENT_TURNS = 4           # 压缩时保留最近 N 个 user 轮的原文
MAX_SUMMARY_MSG_CHARS = 1_500   # 摘要输入中单条消息的截断长度,防摘要调用自爆上下文


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
