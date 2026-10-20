# SproutCode(码苗)设计文档

日期:2026-10-20
状态:已与作者确认设计,待实现

> 名字寓意:一颗单文件种子,一步步长成完整的 Coding Agent。

## 1. 项目定位

SproutCode(码苗)是一个用 Python 从零实现的终端 Coding Agent,以**渐进演进**的方式构建:从单文件最小闭环开始,逐步重构生长为具备子 Agent、MCP 协议、测试驱动自修复能力的完整 Agent。

双重目的:

1. **学习项目**——深入理解 tool calling 循环、上下文管理、多 Agent 调度等 LLM 应用的核心机制,而非调用框架;
2. **校招作品**——git 历史 + tag 即真实工程演进轨迹,每阶段配演进笔记,可在面试中讲清"每一步为什么这么做"。

## 2. 技术选型

| 决策 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.10+ | AI 生态成熟,校招后端/算法方向认可 |
| LLM 接入 | OpenAI 兼容协议,开发期接 DeepSeek | 换模型只改 base_url/模型名;Key 从 `/root/.env` 的 `DEEPSEEK_API_KEY` 加载 |
| 交互形态 | 终端 CLI REPL(类 Claude Code) | 精力聚焦 Agent 核心能力 |
| 运行时依赖 | 仅 `openai` SDK | CLI 用标准库 argparse,颜色用 ANSI 转义;"除 SDK 外零依赖"是可讲的工程决策 |
| 测试 | pytest + FakeLLM 注入 | 不花 API 费用跑全量测试 |
| 包管理 | pyproject.toml + venv + pip | 标准化、可发布 |

## 3. 里程碑路线图

每个版本独立可运行、打 git tag、配一篇 `docs/journey/vN-*.md` 演进笔记(改了什么、为什么、踩坑与收获)。演进方式为**单主线持续重构**(方案 A + 笔记),不做阶段快照目录。

| 阶段 | 主题 | 核心交付 | 验收标准 |
|------|------|----------|----------|
| v0 | 单文件最小闭环 | `sproutcode.py`(~200 行):DeepSeek 客户端 + Agent 循环 + 3 工具(read_file / write_file / run_shell)+ 简单 REPL,非流式 | 对话中让 Agent 创建文件并执行 shell 命令,全程不退出 REPL |
| v1 | 模块化重构 | 拆 `sproutcode/` 包(client / agent / tools / cli / config 分离);流式输出;`@tool` 装饰器 + JSON Schema 自动生成;pytest 基建(FakeLLM) | 功能与 v0 等价;工具注册表单测通过;流式文本与工具调用过程可视化 |
| v2 | 会话与上下文 | 会话 JSONL 落盘、`--resume` 恢复;斜杠命令(/help /clear /resume);token 近似计数 + 上下文压缩(超预算摘要旧消息);系统提示词外置 | 重启后恢复完整对话;构造超长会话触发压缩且 Agent 仍能引用早期关键信息 |
| v3 | 权限与安全 | 危险命令 y/n 审批;项目根路径限制(拒绝越界读写);shell 超时与输出截断 | 越界路径被拒;`rm -rf` 类命令必须人工确认;长输出被截断不撑爆上下文 |
| v4 | 子 Agent | Task 工具派生子 Agent:独立 messages、受限工具集、非交互模式、仅回传最终文本;支持并行 fan-out | 给一个多文件任务,子 Agent 各自完成搜索/修改,主 Agent 汇总;总 token 消耗低于单 Agent 对照 |
| v5 | MCP 协议 | MCP stdio 客户端;外部 server 工具动态发现并并入注册表;`.sproutcode.json` 配置 | 接入一个现成 MCP server(如 filesystem),其工具可被 Agent 调用 |
| v6 | 测试驱动自修复 | 工作流:写码 → 跑测试 → 读失败输出 → 自动修复,循环上限 N 轮 | 预置一个带失败测试的小项目,Agent 在 N 轮内修复到全绿 |
| v7 | 打磨发布 | README + 架构图、安装体验、demo、小型评估任务集(跑通率统计) | 评估集跑通率报告生成;新人按 README 可 10 分钟内跑起来 |

顺序依据:先懂循环本质(v0)再谈架构(v1);会话/上下文(v2)是 Agent 区别于聊天机器人的分水岭;安全(v3)在能力变强前就位;子 Agent(v4)依赖前四层的稳定抽象;MCP(v5)复用 v1 的统一工具抽象;自修复(v6)是全能力的综合应用;打磨收尾(v7)。

实现节奏:每个里程碑独立走一轮"实现计划 → 开发 → 测试 → 验收 → tag → 演进笔记"循环,不做跨里程碑的大批量实现。首个实现周期为 v0。

## 4. 核心架构

### 4.1 包结构(v1 形态,v2+ 在此之上生长)

```
sproutcode/
├── __init__.py
├── __main__.py          # python -m sproutcode 入口
├── cli.py               # REPL 循环、斜杠命令、ANSI 渲染
├── agent.py             # Agent 循环:消息历史 + 工具调度
├── client.py            # LLM 客户端(OpenAI 兼容,DeepSeek/任意端点)
├── tools/
│   ├── __init__.py      # 注册表:@tool 装饰器 + JSON Schema 生成
│   ├── files.py         # read_file / write_file / list_files
│   └── shell.py         # run_shell(超时、截断、审批钩子)
└── config.py            # .env 加载、模型配置
```

后续新增:`sessions/`(v2 会话持久化)、`approve.py`(v3 审批)、`subagent.py`(v4)、`mcp/`(v5)、`workflow/`(v6)。文件过大时按职责继续拆分,单一文件保持"一个模块一件事"。

### 4.2 Agent 循环(项目的心脏)

```
用户输入 → messages 追加 → LLM 流式响应
  ├─ 纯文本 → 渲染输出,回到 REPL
  └─ tool_calls → 逐个执行工具 → 结果以 role=tool 消息追加 → 再次调用 LLM
                                  ↑ 循环,直到模型不再发起工具调用
                                  (单任务轮数上限默认 25,防失控)
```

- v0 用同步非流式跑通逻辑;v1 起流式(SSE delta 聚合出文本与 tool_calls 增量解析)。
- 工具结果统一为 `(content, is_error)` 结构,异常不向上抛,转成错误文本回传模型促其自我纠正。

### 4.3 工具协议

每个工具是带类型注解的 Python 函数;`@tool` 装饰器从函数签名 + docstring 自动生成 OpenAI 格式 JSON Schema 并注册到全局注册表。新增工具零样板。注册表是统一工具抽象(v5 接 MCP 时,远端工具以同样的 Schema 形态并入)。

### 4.4 上下文管理(v2)

- token 估算用字符数近似(中文 ~1 字符/token 量级,英文 ~4 字符/token),不引 tokenizer 依赖;
- 每轮对话后检查:超过预算阈值(默认 70%)时,把最旧的消息块替换为一次 LLM 摘要调用,保留 system prompt 与最近 N 轮原文;
- 会话以 JSONL 追加写盘(每条消息一行,含角色/时间戳/工具调用记录),`--resume <session>` 重放恢复。

### 4.5 子 Agent(v4)

Task 工具本质是"递归调用 Agent 循环":

- 子 Agent 拥有独立 messages、受限工具集(排除 Task 自身防无限递归);
- 非交互模式:审批钩子降级为直接拒绝危险操作;
- 完成后仅将最终文本作为工具结果回传父级——上下文隔离正是子 Agent 省 token 的原理(演进笔记重点阐述);
- fan-out:一次派生多个子 Agent 并行执行(`asyncio` 或线程池,届时依实测定)。

## 5. 错误处理

- **API 层**:网络错误/429/5xx 指数退避重试(上限 3 次);上下文超限错误触发强制压缩后重试;
- **工具层**:执行异常捕获为错误文本回传模型;shell 设超时(默认 30s)与输出截断(默认 10KB);
- **进程层**:Ctrl+C 在工具执行间隙安全退出并保存会话;KeyboardInterrupt 不产生半截消息落盘;
- **循环层**:单任务工具调用轮数上限(默认 25),超限终止并报告已完成的步骤。

## 6. 测试策略

- FakeLLM:实现与 client 相同接口,脚本化返回预定的 tool_calls/文本序列,测 Agent 循环状态机(调度、轮数上限、错误回传);
- 工具层:临时目录直接单测,路径限制/截断逻辑全覆盖;
- 会话层:JSONL 写读回环、resume 等价性;
- 不测网络传输层(由 openai SDK 保证);
- CI 就绪(后续接 GitHub Actions,本地先跑通)。

## 7. 安全基线

- 默认只允许读写项目根目录内的文件(可配置放行);
- 危险命令模式(删除、网络写操作、包管理等)需人工 y/n 确认,非交互场景直接拒绝;
- API Key 只从环境变量/.env 读取,永不入库(.gitignore 覆盖)。

## 8. 演进笔记约定

每阶段一篇 `docs/journey/vN-<主题>.md`,固定三段:

1. **改了什么**——本阶段的结构/行为差异(对照上一 tag);
2. **为什么**——驱动力是什么(哪个痛点/哪个新能力);
3. **踩坑与收获**——调试记录、对 LLM 应用机制的新理解。

笔记是面试叙述的底稿,写给人读,不写营销话术。

## 9. 明确不做(当前范围外)

- Web UI / TUI 框架;
- 多模态(图片输入);
- 自训模型/微调;
- Windows 一等公民支持(优先 Linux/macOS);
- v7 之前不考虑 PyPI 发布。
