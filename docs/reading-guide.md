# SproutCode 成果阅读指南(v2)

> 适用版本:tag `v2`。建议按「先故事、再调用链、最后测试」的顺序读,大约 110 分钟。
> 代码注释是块级教学注释,按代码块读,不要逐行抠。

## 1. 先建立坐标(5 分钟)

- `AGENTS.md`:项目规则、当前进度、已知的坑。
- `git log --oneline --decorate -12` 和 `git tag`:确认 v2 包含哪些提交。

先知道"现在在哪",后面读代码才不会把 v0 的遗留当成 v1 的设计。

## 2. 读演进叙事(15 分钟)

- **`docs/journey/v2-session-context.md`**:先读这篇。"文件=完整事实 / 压缩=内存视图"的解耦、压缩切口选 user 轮边界的原因、暗号实验怎么设计才能排除假阳性,都在里面。
- **`docs/journey/v1-modular-streaming.md`**:v1 的动机,尤其是流式、注册表、测试网和 `list_files` 的取舍。
- 如果对基线不熟,再翻 **`docs/journey/v0-minimal-loop.md`**,重点看 v0 的"四条横切约定"和"非流式的体感"。

这一步先理解"为什么要变成这样",不要急着看实现。

## 3. 读设计意图(10 分钟)

- `docs/superpowers/specs/2026-10-20-sproutcode-design.md`:里程碑路线(§3)确认 v2 的边界;上下文管理(§4.4)是本版的权威设计;错误处理与测试策略(§5-6)解释约束。
- 快速浏览 `docs/superpowers/plans/2026-10-21-v2-session-context.md` 的任务拆分,不用逐行读代码块。

这一步把"设计约束"和"实现选择"分开。

## 4. 按一次请求的生命周期读代码(35 分钟)

顺着"用户输入 → 流式响应 → 工具执行 → 消息回写 → 落盘/压缩"走一遍:

1. **`sproutcode/__main__.py`**:包入口,只有 `main()`。
2. **`sproutcode/cli.py`**:REPL 主循环、斜杠命令(`handle_slash`/`restore`)与 `CliState`;再看 `Renderer`,理解正文和工具轨迹为什么用两套回调。
3. **`sproutcode/agent.py`**:项目心脏。重点看 run 起点的压缩钩子、`task_start`、LLM↔工具循环、`on_turn_commit` 为什么只在成功路径触发,以及 `except BaseException` 的回退。
4. **`sproutcode/sessions.py`**:JSONL 落盘与重放。注意"只追加不改写"与轮粒度提交如何天然满足"中断不落半截消息"。
5. **`sproutcode/context.py`**:token 近似计数与 `maybe_compact`。注意切口为什么选在倒数第 N 个 user 消息、摘要为什么以 user 消息注入。
6. **`sproutcode/prompts/`**:外置的 system/compact 提示词与 `load()`。
7. **`sproutcode/client.py` 的 `collect_stream()`**:重点看文本 delta 如何回调、`tool_calls` 如何按 `index` 分槽拼接。
8. **`sproutcode/client.py` 的 `LLMClient.chat()`**:确认它只是 SDK 的薄包装,Agent 不依赖 SDK 细节;压缩摘要复用它(`tools=[]`)。
9. **`sproutcode/tools/__init__.py` 的 `ToolRegistry` 与 Schema 生成**:注册、Schema 输出、统一执行入口。
10. **`sproutcode/tools/files.py` 和 `shell.py`**:四个内置工具;注意它们的 docstring 就是 Schema 描述的来源。
11. **`sproutcode/config.py`**:常量(v2 新增会话目录与压缩预算)、Key 加载。

读完这条链,应该能回答:一条用户消息在哪些对象之间流动、失败时历史如何回退、一轮对话什么时候落盘、超预算时上下文怎么被压缩而会话文件为什么不变。

## 5. 读测试,把行为钉死(20 分钟)

测试是另一份可执行的规格,按同样顺序读:

- **`tests/test_client.py`**:流式文本、按 index 分片的 `tool_calls`、无 choices 的 chunk。
- **`tests/test_agent.py`**:纯文本、工具调用、工具错误回传、25 轮上限、异常回退;v2 加的轮切片提交与 run 起点压缩用例最能体现新约束。
- **`tests/test_context.py`**:计数近似、阈值内不动、切口不拆 tool 对、摘要注入结构。
- **`tests/test_sessions.py`**:写读回环、前缀匹配、坏行跳过、列表摘要。
- **`tests/test_tools.py`**:Schema 生成、坏 JSON、未知工具、异常转文本、非零退出码不算错误。
- **`tests/test_cli.py` / `tests/test_config.py` / `tests/test_prompts.py`**:斜杠命令、渲染前缀、参数截断、Key 加载、外置提示词,快速过。

然后自己跑一遍:

```bash
cd /root/workplace/SproutCode
.venv/bin/python -m pytest -v
```

## 6. 跑起来看行为(10 分钟)

```bash
.venv/bin/python -m sproutcode
```

输入一个会触发工具的任务,观察正文是否分块出现、工具轨迹是否暗色打印、`/exit` 是否正常退出。再看 v2 的两个新行为:`/resume` 列出最近会话;退出后用 `--resume <banner 里的会话 id>` 重启,问它"上一轮让你做了什么",验证记忆恢复。复现验收的管道命令在 v2 计划文档 Task 7。

## 7. 可选:读工程轨迹(15 分钟)

```bash
git log --oneline v1..v2
git diff v1 v2 --stat
```

配合 v2 计划文档看"为什么按模块拆提交",这部分是面试叙述的骨架。

---

如果只有 15 分钟,读这三处就够:**`docs/journey/v2-session-context.md` → `sproutcode/context.py` → `tests/test_sessions.py`**。它们分别覆盖"为什么做、上下文怎么管、落盘恢复怎么证明"。核心循环不熟的话,把 `sproutcode/agent.py` 换进第二格。
