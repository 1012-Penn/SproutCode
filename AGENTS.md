# AGENTS.md — SproutCode(码苗)工作指南

> 新会话先读这份,避免重复踩坑。项目权威 spec 见 `docs/superpowers/specs/2026-10-20-sproutcode-design.md`。

## 项目与进度

- 从零实现的终端 Coding Agent(Python 3.10+),**渐进演进**:每版本独立可运行、打 tag、配演进笔记。
- 里程碑路线:v0 单文件闭环 → v1 模块化+流式+pytest → v2 会话/上下文 → v3 权限安全 → v4 子 Agent → v5 MCP → v6 自修复 → v7 发布。
- 进度**以 `git tag` 与 `docs/journey/` 为准**(本文件不逐次更新):v0、v1、v2 均已完成并真机验收通过,tag `v0` / `v1` / `v2`。

## 必须遵守的工作流(spec §3)

1. 每个里程碑独立走一轮:**实现计划 → 开发 → 测试 → 验收 → tag → 演进笔记**;不做跨里程碑的大批量实现。
2. 计划存 `docs/superpowers/plans/YYYY-MM-DD-vN-*.md`;演进笔记存 `docs/journey/vN-<主题>.md`,固定三段:改了什么 / 为什么 / 踩坑与收获(写给人读,不写营销话术)。
3. 单 main 线持续重构,直接在 main 提交;周期结束 `git push origin main --tags`(origin = https://github.com/1012-Penn/SproutCode.git)。
4. 提交信息:`feat:` / `docs:` / `chore:` + 中文短语。

## 技术约定

- 运行时依赖**仅 openai SDK**,CLI 用标准库——这是可讲的工程决策,新增依赖要有强理由。
- 虚拟环境 `.venv/`(Python 3.10.12,openai 与 pytest 已装):一律用 `.venv/bin/python` 执行。
- LLM:OpenAI 兼容协议接 DeepSeek(仓库内常量 `BASE_URL`/`MODEL`);API Key 从环境变量或 `/root/.env` 的 `DEEPSEEK_API_KEY` 读取,**永不入库**。
- 工具结果统一 `(content, is_error)`,异常转错误文本回传模型;单任务轮数上限 25。
- 会话 JSONL 落盘在 `~/.sproutcode/sessions/`(v2 起):文件是**完整事实**、只追加不改写;上下文压缩只改 `Agent.run` 起点的内存视图(超预算 70% 摘要旧块,切口必在 user 轮边界),摘要永不落盘;中断/失败轮经 `on_turn_commit` 之外路径自然不落盘。
- 系统与摘要提示词外置在 `sproutcode/prompts/*.txt`(v2 起),改提示词不动代码。
- 入口统一为 `python -m sproutcode`(v1 起,根目录不再有 `sproutcode.py`);`--resume <id前缀>` 恢复会话。
- 真机验收用管道喂 REPL(可复现、可留档):`printf '...' | .venv/bin/python -m sproutcode`。
- 离线自检不花 API 费:`.venv/bin/python -m pytest`(工具层/Agent 循环/流式聚合全覆盖)。

## 已踩过的坑(别再走)

- **`.zcode/agents/*.md` 自定义代理定义在当前会话不生效**:代理注册表在会话启动时固定,新建定义要到下个会话才可见。实测过,别再试。
- 分工偏好(用户指定):**机械性工作派通用子智能体(general-purpose)**——照既定规范写文件、跑自检、起草文档;主会话负责对照审查、验收门禁与 git 操作。子代理产出必须经主会话 diff/审查后才提交。不指定 Flash 模型、不动用 workflow,除非用户明确要求。
- 带 tool_calls 的 assistant 消息必须原样入历史,每个 `tool_call_id` 都要有对应 `role=tool` 回复,否则下一轮请求直接 400;任务中断/失败要把 messages 回退到本轮 user 消息之前(`Agent.run` 的 `task_start` 机制,v0 在 `sproutcode.py`,v1 起在 `sproutcode/agent.py`,后续重构必须保留该语义)。
- 根目录不要同时存在 `sproutcode.py` 与 `sproutcode/` 包:同名文件会遮蔽包,报 `'sproutcode' is not a package`;入口统一用 `python -m sproutcode`。
- 上下文压缩的旧块切口必须落在 user 轮边界,否则会拆散 assistant(tool_calls)/tool 配对,悬空 tool 消息让下一轮请求 400(v2)。
- 测"每轮检查"型钩子(如压缩)时,FakeLLM 脚本要按次数推演:超预算状态下**每次** run 起点都会再触发一次摘要调用,脚本份数 = Σ(每轮的摘要 + 对话)(v2 实测踩过)。
- 真机验收"记忆恢复"类问题必须限制"不要用工具,凭记忆回答",否则模型现场用工具发现答案会造成恢复失败的假阳性(v2)。
- 代码注释风格(用户指定):**教学向、以代码块为单位**,讲"为什么这么设计",不逐句注释。
- 不入库目录已由 .gitignore 覆盖:`.venv/`、`.zcode/`、`__pycache__/`、`.env`;验收用的临时产物(sprout-demo/ 等)验收后即清理。

## 文档地图

- `docs/superpowers/specs/` — 设计文档(权威)
- `docs/superpowers/plans/` — 各里程碑实现计划
- `docs/journey/` — 演进笔记(面试叙述底稿)
- `docs/reading-guide.md` — 成果阅读顺序(按版本更新)
