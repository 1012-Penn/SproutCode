# v1 演进笔记:模块化、流式与测试网

- 日期:2026-10-20
- 对应交付:`sproutcode/` 包(9 个源文件,约 600 行)+ 5 个测试文件(22 项 pytest)+ `pyproject.toml`
- 对照:`v0`(单文件 195 行,非流式,手写 Schema,无测试)

> v1 要回答的问题:单文件已经跑通之后,怎样在不改变行为的前提下把它长成可持续演进的架构,并让"流式"和"可测试"成为后续版本的默认能力。

## 改了什么

**结构:一个文件变成七个职责单元。** `config` 只放常量、系统提示词与 Key 加载;`client` 负责 OpenAI 兼容协议与 SSE 聚合;`agent` 持有 messages 并驱动工具循环;`tools` 提供可实例化的 `ToolRegistry` 与四个内置工具;`cli` 只做 REPL 和 ANSI 渲染;`__main__` 提供 `python -m sproutcode` 入口。根目录的 `sproutcode.py` 被删除——同名文件和包目录会冲突,入口统一改为模块方式。

**流式:从"等完整回复"变成"边到边显示"。** `collect_stream()` 把文本 delta 直接回调给渲染层,把 `tool_calls` 按 `index` 分槽聚合。终端里正文亮色、工具轨迹暗色;正文第一次到达时才打印一次 `码苗> ` 前缀,而不是等整轮结束再补。

**工具:手写 Schema 换成 `@tool` 装饰器。** 装饰器从函数签名 + docstring 生成 OpenAI function calling 的 JSON Schema:支持 `str/int/float/bool`、`list[T]`、`dict`、`Optional[T]` 与默认参数;有默认值的参数不进 `required`;`Args:` 段自动变成参数描述。工具注册表是可实例化的 `ToolRegistry`,测试可以建隔离实例,不污染全局注册表;这层抽象也为 v4 的受限工具集和 v5 的 MCP 工具并入留好了位置。工具从 3 个增加到 4 个,补了 `list_files`(只列一级)。

**循环:从函数变成 `Agent` 类。** `Agent.messages` 就是会话状态;`run()` 负责追加 user 消息、LLM↔工具交替、25 轮上限,以及最关键的本轮回退。v0 放在 `main()` 里的 `task_start` 逻辑被搬进 `Agent.run()`:任何异常或 Ctrl+C 都把 messages 回退到本轮 user 之前,不让半截 `tool_calls` 留在历史里。

**测试:重构前先铺网。** 新增 pytest + FakeLLM。FakeLLM 与 `LLMClient.chat()` 同接口,按脚本返回 `AssistantTurn`;测试覆盖纯文本、工具调用、工具错误回传、轮数上限、异常回退。流式聚合用 `SimpleNamespace` 伪造 chunk,单独测文本 delta、按 index 分片拼接、无 choices 的 chunk。工具层覆盖 Schema 生成、未知工具、坏 JSON、异常转文本、写入/读取/列目录、非零退出码不算错误。全量 22 项,不触网、不花 API 费。

**工程:运行时依赖仍然只有 `openai`。** `pyproject.toml` 只把 pytest 放进 `dev` extras;`pythonpath = ["."]` 让测试无需安装包也能导入。v1 没有做 v2 的会话落盘、`--resume`、token 预算与上下文压缩——messages 仍然只在内存里。

真机验收(2026-10-20,管道喂两轮输入):第一轮让 Agent 在 `sprout-demo/` 创建 `hello.txt`,第二轮 `cat` 确认并 `ls`;进程退出码 0,两轮都在同一 REPL 内完成,文件内容为 `SproutCode v1`,终端能看到 `write_file(...)` / `run_shell(...)` 轨迹。流式另用带时间戳的回调验证:78 个 delta,首个在 0.697s 到达,整轮 1.309s 结束——不是等完整响应再一次性打印。

## 为什么

**为什么要在功能没变的时候先做一次重构。** v0 的 195 行已经能完成任务,但所有职责挤在一起:改流式要动循环,改工具要同时改函数和 Schema,想写测试只能整文件导入。v1 的价值不是新增用户可见功能,而是把"以后每次改动的成本"降下来。模块边界按变化原因划:换模型改 `config/client`,加工具改 `tools`,改交互改 `cli`,改循环策略改 `agent`。

**为什么流式值得单独做一版。** v0 的非流式在真机上体感很差:任务执行中终端长时间没有输出,用户不知道模型在思考、在写文件,还是卡住了。流式不只是"看起来快",它是 Agent 的可观测性——把"现在在干什么"变成用户能看见的信号。流式也顺手暴露了 tool calling 的细节:非流式里 `tool_calls` 是聚合好的对象,流式里必须自己按 index 拼 id、name、arguments,这正是理解 function calling 协议的好切口。

**为什么先有测试网再拆包。** 拆包最大的风险是"行为悄悄变了":模块导入、消息形态、异常路径都可能出问题。22 项测试里,Agent 循环的测试用 FakeLLM 把"模型下一轮会说什么"变成脚本,能精确断言 messages 的形状和顺序;这比真机跑一遍更能证明"与 v0 等价"。测试网也是后续 v2/v3 改动的安全垫。

**为什么工具注册表做成实例而不是全局字典。** v0 的 `TOOL_IMPL` 是模块级字典,够用但不可隔离:测试一注册工具就污染全局。`ToolRegistry` 让每个测试、每个子 Agent(v4)、每个 MCP server(v5)都能拿到自己的工具视图;`@tool` 只是往某个 registry 里注册的语法糖。这个决定现在只多几行,后面能省很多改造。

**为什么 `list_files` 只列一级。** 最初测试把文件写到 `tmp_path/nested/hello.txt`,却断言顶层列表里有 `hello.txt`,这实际上是在要求递归。v1 没有输出截断(v3 才做),递归遍历一个大仓库可能一次把上下文撑爆;`list_files` 保持 `ls` 的语义,要看深层结构,模型可以再列下一层,或用 `run_shell`。最后修的是测试:断言顶层恰好是 `nested/`、`sub/`、`top.txt`,把"只列一级"锁死。

## 踩坑与收获

**1. 根目录同名文件会遮蔽包。** 新建 `sproutcode/` 后,根目录还留着 `sproutcode.py`,导入时 Python 先找到了同名模块,测试直接报 `'sproutcode' is not a package`。删掉旧文件、入口改成 `python -m sproutcode` 后才正常。收获:包名和模块名不要共用;重构入口时,旧入口要么删除,要么改名成明确的兼容 shim,不能让它和包目录并存。

**2. 流式 `tool_calls` 必须按 index 分槽拼接。** 一个工具调用的 `id`、`name`、`arguments` 可能分散在多个 chunk 里,`arguments` 更是逐段 JSON 文本。实现用 `slots[index]` 聚合,`id` 只取第一次出现的值,`name`/`arguments` 逐段 `+=`;最后按 index 排序还原成调用列表。测试故意把 `read_file` 拆成 `"read_" + "file"`、把 JSON 拆成 `'{"pa' + 'th": "a.txt"}'`,确保拼接逻辑真的被覆盖。收获:流式不是"把非流式响应切成块",而是同一份协议的另一条编码路径,聚合逻辑必须单独测。

**3. assistant 消息从 SDK 对象换成了 dict,`content` 可能是 `None`。** v0 直接把 SDK 的 `ChatCompletionMessage` 塞进 messages;v1 统一用 `AssistantTurn.to_message()` 生成 dict,带 tool_calls 时 `content=None`、每个调用补 `"type": "function"`。这和 OpenAI 协议一致,但很容易在重构时漏字段。测试断言了完整 dict 形状,也断言下一轮请求能看到 `role=tool` 的回复。收获:消息历史的形状是 Agent 的硬约束,应该用测试锁住,而不是靠"我记得 API 要这样"。

**4. 回退逻辑搬进 `Agent.run()` 后,必须用异常测试证明它还在。** v0 在 `main()` 里记录 `task_start`,`del messages[task_start:]`;v1 把这段搬进 Agent,并捕获 `BaseException`(包括 `KeyboardInterrupt`)。测试先让一轮成功,再让下一轮抛 `RuntimeError`,断言 messages 与失败前完全一致。收获:重构时最容易丢的不是正常路径,而是异常路径;把"失败后状态不变"写成测试,比在代码里加注释可靠。

**5. FakeLLM 要复刻的是接口,不是 SDK。** FakeLLM 只实现 `chat(messages, tools, on_text=None) -> AssistantTurn`,并在返回前调用 `on_text` 模拟流式回调。Agent 完全不感知 OpenAI SDK,测试也就不需要 mock 网络层。收获:给外部依赖定义最窄的 Protocol,测试替身会变得非常轻;这也是后面接 MCP、子 Agent 时可以复用的思路。

**6. 真机验收要看"过程",不只看"结果"。** 管道喂输入能证明 REPL 不退出、文件写对,但看不出流式是否真的分块。补了一个带时间戳的直接客户端调用:78 个 delta,首个 0.697s、整轮 1.309s,才把"流式"从代码事实变成验收证据。收获:验收标准里的"可视化"要设计成可观测的证据,不能只看最终文本。

---

下一步是 v2:会话 JSONL 落盘与 `--resume`、斜杠命令、token 近似计数与上下文压缩。v1 留下的接口(Agent 持有 messages、client 返回统一 `AssistantTurn`)就是为这些能力准备的;`Agent.run()` 的循环本质仍然没变,变的还是循环外面的工程约束。
