# SproutCode 成果阅读指南(v1)

> 适用版本:tag `v1`。建议按「先故事、再调用链、最后测试」的顺序读,大约 90 分钟。
> 代码注释是块级教学注释,按代码块读,不要逐行抠。

## 1. 先建立坐标(5 分钟)

- `AGENTS.md`:项目规则、当前进度、已知的坑。
- `git log --oneline --decorate -8` 和 `git tag`:确认 v1 包含哪些提交。

先知道"现在在哪",后面读代码才不会把 v0 的遗留当成 v1 的设计。

## 2. 读演进叙事(15 分钟)

- **`docs/journey/v1-modular-streaming.md`**:先读这篇。它按"改了什么 / 为什么 / 踩坑与收获"讲清了 v1 的动机,尤其是流式、注册表、测试网和 `list_files` 的取舍。
- 如果对基线不熟,再翻 **`docs/journey/v0-minimal-loop.md`**,重点看 v0 的"四条横切约定"和"非流式的体感"。

这一步先理解"为什么要变成这样",不要急着看实现。

## 3. 读设计意图(10 分钟)

- `docs/superpowers/specs/2026-10-20-sproutcode-design.md`:里程碑路线(§3)确认 v1 的边界;包结构、Agent 循环、工具协议(§4.1-4.3)是权威设计;错误处理与测试策略(§5-6)解释约束。
- 快速浏览 `docs/superpowers/plans/2026-10-20-v1-modular-streaming-pytest.md` 的任务拆分,不用逐行读代码块。

这一步把"设计约束"和"实现选择"分开。

## 4. 按一次请求的生命周期读代码(35 分钟)

顺着"用户输入 → 流式响应 → 工具执行 → 消息回写"走一遍:

1. **`sproutcode/__main__.py`**:包入口,只有 `main()`。
2. **`sproutcode/cli.py`**:REPL 主循环;再看 `Renderer`,理解正文和工具轨迹为什么用两套回调。
3. **`sproutcode/agent.py`**:项目心脏。重点看 `task_start`、LLM↔工具循环、每个 `tool_call_id` 的对应关系,以及 `except BaseException` 的回退。
4. **`sproutcode/client.py` 的 `collect_stream()`**:重点看文本 delta 如何回调、`tool_calls` 如何按 `index` 分槽拼接。
5. **`sproutcode/client.py` 的 `LLMClient.chat()`**:确认它只是 SDK 的薄包装,Agent 不依赖 SDK 细节。
6. **`sproutcode/tools/__init__.py` 的 `ToolRegistry`**:注册、Schema 输出、统一执行入口。
7. **`sproutcode/tools/__init__.py` 的 Schema 生成部分**:docstring 解析与类型到 JSON Schema 的映射。
8. **`sproutcode/tools/files.py` 和 `shell.py`**:四个内置工具;注意它们的 docstring 就是 Schema 描述的来源。
9. **`sproutcode/config.py`**:常量、系统提示词、Key 加载。

读完这条链,应该能回答:一条用户消息在哪些对象之间流动、失败时历史如何回退、工具描述从哪里来。

## 5. 读测试,把行为钉死(20 分钟)

测试是另一份可执行的规格,按同样顺序读:

- **`tests/test_client.py`**:流式文本、按 index 分片的 `tool_calls`、无 choices 的 chunk。
- **`tests/test_agent.py`**:纯文本、工具调用、工具错误回传、25 轮上限、异常回退。最后两个测试最能体现设计约束。
- **`tests/test_tools.py`**:Schema 生成、坏 JSON、未知工具、异常转文本、非零退出码不算错误。
- **`tests/test_cli.py` / `tests/test_config.py`**:渲染前缀、参数截断、Key 加载,快速过。

然后自己跑一遍:

```bash
cd /root/workplace/SproutCode
.venv/bin/python -m pytest -v
```

## 6. 跑起来看行为(10 分钟)

```bash
.venv/bin/python -m sproutcode
```

输入一个会触发工具的任务,观察正文是否分块出现、工具轨迹是否暗色打印、`/exit` 是否正常退出。想复现验收,用 v1 计划文档 Task 6 里的管道命令。

## 7. 可选:读工程轨迹(15 分钟)

```bash
git log --oneline v0..v1
git diff v0 v1 --stat
```

配合 v1 计划文档看"为什么按模块拆提交",这部分是面试叙述的骨架。

---

如果只有 15 分钟,读这三处就够:**`docs/journey/v1-modular-streaming.md` → `sproutcode/agent.py` → `tests/test_agent.py`**。它们分别覆盖"为什么做、核心循环、如何证明循环没坏"。
