# Coding101 —— AuraAgent 架构入门

这篇文档和 [`ARCHITECTURE.md`](ARCHITECTURE.md) 的分工不同：`ARCHITECTURE.md` 是按 Epic 时间顺序写的变更日志，每条记录"做了什么、为什么这么改、过程中踩了什么坑"，适合查一个具体历史决策的来龙去脉，但假设读者已经熟悉整个项目。**这篇文档反过来，是写给第一次接触这个代码库的人的**——不关心"是怎么演化来的"，只关心"现在长什么样、为什么这么设计、从哪个文件开始读"。

读法建议：第一部分建立全局框架（设计原则 + 目录地图），第二部分是具体文件的深潜——每个文件按"解决什么问题 → 核心抽象是什么 → 为什么这么设计 → 和谁协作"的顺序讲，不逐行复述代码。想知道某个具体功能的历史背景和踩坑细节，去 `ARCHITECTURE.md` 搜对应章节；想知道怎么把项目跑起来，去 [`README.md`](../README.md)。

## 目录

**第一部分：架构设计原则与设计思路**
1. [这个项目是什么](#1-这个项目是什么)
2. [反复出现的设计模式](#2-反复出现的设计模式)
3. [代码目录地图：分层与职责边界](#3-代码目录地图分层与职责边界)
4. [非代码目录的作用](#4-非代码目录的作用)

**第二部分：关键文件详解**
5. [核心引擎与双前端桥梁](#5-核心引擎与双前端桥梁)（`core/`、`cli/`、`providers/`）
6. [多代理编排与工具/安全边界](#6-多代理编排与工具安全边界)（`agents/`、`tools/`、`confirmation/`）
7. [GUI 后端、MCP 集成、Skills、配置](#7-gui-后端mcp-集成skills配置)（`gui/`、`mcp_integration/`、`skills/`、`config/`）

[延伸阅读](#延伸阅读)

---

## 第一部分：架构设计原则与设计思路

### 1. 这个项目是什么

一句话定位：**一个从零手写的个人 AI Agent**——核心是一个纯 `asyncio` 实现的白盒 ReAct 循环（Reason → Act → Observe），直接调官方 SDK（`anthropic`/`openai`），**没有用 LangChain 这类黑箱框架**。"白盒"不是一句口号，是真的体现在代码里：每一轮的 Thought / Tool Call / Observation 都会被打印到终端、写进 `logs/session-*.jsonl`，出了问题能直接在日志里看到模型在想什么、调了什么工具、工具回了什么——不需要靠框架自己的 tracing 工具才能看懂发生了什么。

在这个核心之上，现在长成了三层能力：

- **单 Agent → 多 Agent**：一个 Leader 可以把任务分给多个 Worker（`agents/`，Leader-Worker 编排），但 `core/react_engine.py`（ReAct 循环本体）对"多 Agent"这件事完全零感知——每个 Agent 不过是一个独立的 `AsyncReActEngine` 实例，Multi-Agent 的所有逻辑都堆叠在 core 之上，不侵入 core 本身。这是理解整个代码库分层方式的第一个关键例子。
- **单前端 → 三前端**：最早只有 CLI（`main.py`），后来加了 GUI（`gui/server.py` + React 前端）和 Telegram 机器人（`telegram_bot.py`），三者都只是"壳"——真正的装配逻辑（把所有工具、Agent、Skill 组装起来）只有一处，`core/bootstrap.py::build_app_context()`，三个前端都调用这同一个函数。这保证了"CLI 能做的事 GUI/Telegram 也一定能做"不是靠记住去维护一致，而是架构上不可能不一致。
- **静态工具集 → 会自我扩展的工具集**：模型在对话过程中可以自己提议"装一个新工具/招一个新 Worker/接一个新的 MCP 服务"（`tools/self_extend/`），经人类审批后立即生效并持久化。这是这个项目里风险最高、也最有意思的一块，专门用"风险分级"机制来控制（见下面第 2 节）。

### 2. 反复出现的设计模式

这些模式不是巧合地相似，是**同一个问题反复出现时刻意复用同一个解法**——读代码时认出它们，比每次都重新理解一遍要快得多。

#### 2.1 可热切换指针（Swappable-indirection）

**问题**：某个运行时值（当前用哪个 LLM 厂商？当前日历/任务连的是本地 JSON 还是真实 Google 账号？工作目录当前指向哪？）可能被好几个消费者各自持有引用，如果值本身能被用户一条命令换掉（比如 `/config use`、`/calendar connect`），怎么让所有消费者立刻看到新值，而不需要一个个去通知？

**解法**：引入一个很薄的包装对象，只有一个可变字段 `._current`，所有消费者拿到的都是**同一个包装对象实例**，每次要用的时候读 `.current`（或者直接调包装对象自己的方法，由它转发给当前的具体实现），而不是在构造时就把具体实现缓存进自己的闭包里。换值时只需要调包装对象的 `set_current(new_value, name)`，一次调用，所有消费者下一次读取就自动拿到新值。

这个模式在项目里出现了至少五次，每次面对的是同一类问题：`providers/swappable_provider.py`（LLM 厂商）、`tools/workspace_root.py::SwappableWorkspaceRoot`（工作目录/笔记目录）、`tools/calendar/swappable_calendar_provider.py`（日历后端）、`tools/tasks/swappable_task_provider.py`（任务后端）、`confirmation/swappable_channel.py`（确认通道）。如果以后需要让某个新的运行时值也支持热切换，直接照抄这个形状，不要发明新的。

#### 2.2 窄接口 JSON 存储

**问题**：很多功能（日程、项目、记忆、设备、会话）都需要一份简单的持久化数据，但不想到处散落着"打开文件、读 JSON、改字段、写回去"的裸代码，也不想为这么轻量的数据引入真正的数据库。

**解法**：每个 `tools/*/[...]_store.py` 都是同一个形状——`__init__(file_path)`，内部一个 `asyncio.Lock` 保证并发安全，私有的 `_load()`/`_save()` 做实际的文件 I/O，对外只暴露几个语义明确的公开方法（比如 `add_schedule`/`list_schedules`/`delete_schedule`），外部代码**永远不直接摸底层的 dict 或文件**。好处是如果以后真要换成数据库，改动范围严格限定在这一个文件内，调用方一行都不用动。

#### 2.3 Sandbox 边界：一个函数，到处收口

**问题**：好几类工具（笔记、通用文件管理、下载）都要接受模型/用户给的路径，怎么保证这些路径不会跳出预期目录（路径穿越攻击，比如 `../../../../etc/passwd`）？

**解法**：**只有一个**函数负责这件事——`tools/sandbox_path.py::resolve_within_sandbox(root, relative_path)`——任何接受用户/模型给出的相对路径的工具（笔记、通用文件管理、文档读写、截图、下载）都必须先过这一关，不允许自己拼路径。注意这个"根目录"本身是可以配置、甚至运行时切换的（见上面的可热切换指针模式：`/workspace set`、`/notes set` 换的是 `root`，不是校验逻辑本身），但校验函数永远是同一个，边界永远存在，只是边界画在哪可以变。笔记和工作区各有自己独立的根目录，互不影响，但都走同一套边界检查代码（日历、任务不在这个清单里——它们的数据落在一个固定的 JSON 文件路径上，本身不接受任意相对路径参数，所以不存在需要收口的攻击面）。

#### 2.4 CLI/GUI 复用：逻辑只写一次

**问题**：同一个能力（比如"切换 LLM 厂商"）既要能从 CLI 的 `/config use` 触发，也要能从 GUI 点一个按钮触发。如果两边各自实现一遍，迟早会在某个边界情况上悄悄跑出不一样的行为。

**解法**：`cli/service.py` 放的是纯粹的查询/动作函数——不调 `input()`/`print()`/`getpass()`，只接受参数、返回结果或抛异常。`cli/commands.py`（终端的 `/` 命令解析）和 `gui/routes.py`（REST 端点）都只是薄薄的 I/O 适配层，两边**调用的是同一个 `cli/service.py` 函数**。这样"CLI 和 GUI 行为一致"不是一条需要记住去遵守的约定，而是架构上不可能不一致——以后任何新功能要同时给两个前端用，逻辑写进 `cli/service.py` 这一层，就自动两边都能用。

#### 2.5 人类审批（HITL）：一个接口，多种物理通道

**问题**：模型有时需要人类当场点头才能继续（删除一个文件、装一个新的外部 MCP 服务），而"人类"可能正坐在终端前，也可能在浏览器里开着 GUI，也可能在手机 Telegram 里聊天——确认这件事该怎么跟"具体是哪个前端"解耦？

**解法**：`confirmation/base.py` 定义一个很小的接口——`confirm()`（是/否）和 `ask_open_question()`（开放式文本问答，比如让人类直接输入一个密钥，不经过模型）——三个前端各自实现一个具体通道（`TerminalConfirmationChannel`、`WebSocketConfirmationChannel`、`TelegramConfirmationChannel`），工具代码只认接口，不关心背后到底是哪种通道在等人类回应。有一条边界规则值得记住：**GUI 上人类自己点按钮触发的动作，永远不需要再走这个确认接口**——这个接口专门用来挡"模型自主发起的高风险调用"，人类自己的操作本身就是批准，再加一层确认是多余的摩擦。

#### 2.6 risk_level 分级：审查力度跟风险大小挂钩，不是一刀切

**问题**：模型自己提议"装一个新工具/扩展权限/接一个新的外部服务"，这几件事的实际风险天差地别，如果审批流程的仔细程度都一样，轻的事情会显得啰嗦，重的事情审查强度又不够。

**解法**：`tools/self_extend/` 里的四类自我扩展能力明确分了四档风险，从轻到重：`capability_grant`（只是把已经装好、已经审查过的代码授权给某个 Agent 用，审查文案最短）→ `code_execution`（模型自己写了一段新代码，必须把**完整代码**原样展示给人看，而不是摘要）→ `scope_expansion`（新增一个长期存在的团队角色，但只能调已有工具，不涉及新代码）→ `arbitrary_execution`（接入一个全新的外部 MCP 服务，没有代码可审查，本质是信任一整个命令/包，审查文案最重）。工具层面也沿用同一套思路，比如 `delete_file`/`kill_process` 标为 `destructive`，`take_screenshot` 标为 `privacy_exposure`——风险的"种类"不同就用不同标签，而不是都塞进一个笼统的"危险"桶里。

#### 2.7 延迟求值的读取：零参闭包而不是提前捕获的值

**问题**：有些工具在注册时（`register_xxx_tools(...)` 被调用的那一刻），它需要读取的那个值还没有确定下来（比如笔记子目录这种可能在注册之后才被赋值的配置项）。如果直接把当前值当参数传进去，以后这个值变了，工具里缓存的还是注册那一刻的旧值。

**解法**：传一个零参数的 lambda，在工具**真正被调用的那一刻**才执行这个 lambda 去读取当前值——Python 闭包里的自由变量本来就是在调用时才解析，不是定义时，所以哪怕这个变量是在同一个函数里后面才被赋值的，这样写也是安全的。`tools/notes/notes_tool.py::register_quick_note_tool` 的 `get_quick_notes_subdir` 参数是这个模式的例子。

### 3. 代码目录地图：分层与职责边界

```
core/        ReAct 循环本体 + 组合根 + 日志抽象 —— 对 Multi-Agent/CLI/GUI 这些上层概念零感知
cli/         service.py（纯逻辑）+ commands.py（终端 I/O 适配）+ context.py（CLIContext）
gui/         FastAPI 后端（server.py/routes.py）+ frontend/（React+Vite，独立前端工程）
agents/      建在 core 之上的 Multi-Agent 编排层
tools/       每个子包是一类领域能力，registry.py 是唯一共享的工具登记表
providers/   把不同 LLM 厂商的 API 差异封在一层接口后面
confirmation/把"人类审批"抽象成一个接口，供任意前端实现
mcp_integration/ 接外部 MCP 服务器（别人已经写好的工具集）
skills/      让模型在对话中自己写一个新工具（代码级扩展）
config/      版本控制内的策略/编制数据（设置、团队编制、MCP 服务器列表）
```

逐项说明"为什么这样分层"，而不只是"这里放什么"：

- **`core/` 是整个系统的发动机，但发动机不知道车身长什么样**。`react_engine.py` 实现的 Reason→Act→Observe 循环只认"系统提示词 + 工具注册表 + LLM Provider"，它不知道自己是被 CLI 的 REPL 调用、被 GUI 的 WebSocket handler 调用、还是被 Telegram 的消息处理器调用，也不知道自己可能只是 Leader-Worker 编排里的一个 Worker 引擎。这种"无知"是故意设计的——它是 Multi-Agent 能够不侵入 core 就叠加上去的前提；如果 core 本身感知"我是 Leader 还是 Worker"，`agents/` 整层就没法干净地叠在上面。
- **`cli/` 把"逻辑"和"终端交互"显式拆成两个文件**：`service.py` 是真正的业务逻辑（纯函数，不摸 `input()`/`print()`），`commands.py` 只负责把终端输入解析成函数调用、把返回值打印出来。这个拆分不是代码整洁癖，是 2.4 节"CLI/GUI 复用"模式的直接后果——`gui/routes.py` 要复用同一套逻辑，前提是逻辑本身不能跟终端 I/O 耦合在一起。
- **`gui/` 是另一套 I/O 适配，经由 `core/bootstrap.py` 共享同一个组合根**。`gui/frontend/` 是完全独立的 React+Vite 工程（有自己的 `package.json`/`npm run build`），构建产物落到 `gui/static/`（gitignored），由 `gui/server.py` 当静态文件挂载在根路径——这意味着前端的构建工具链和 Python 后端是两个完全不同的技术栈，中间只靠 REST/WebSocket 协议连接，没有共享任何代码。
- **`agents/` 建在 `core/` 之上，但不修改 `core/` 一行代码**。一个 Worker 本质上就是另一个独立的 `AsyncReActEngine` 实例，Leader 通过把"调用某个 Worker"包装成一个普通工具（`delegate_to_<name>`）来委派任务——从 `core/react_engine.py` 的视角看，调用一个 Worker 跟调用 `create_note` 这样的普通工具没有任何区别。按 `capabilities` 过滤每个 Agent 能看到哪些工具（`ScopedToolRegistryView`），这个过滤发生在 `agents/` 这一层，`tools/registry.py` 本身的共享注册表里始终是全集。
- **`tools/` 每个子目录是一个独立的领域能力包**，彼此之间原则上不互相 import（`tools/calendar/` 不需要知道 `tools/tasks/` 的存在），但共享两个东西：`tools/registry.py`（所有工具最终注册进同一个 `ToolRegistry`，模型看到的是一个统一的工具列表）和 `tools/sandbox_path.py`（任何涉及文件路径的工具都过同一个边界检查）。
- **`providers/` 的存在是为了让"换一个 LLM 厂商"这件事只影响这一层**。`AsyncReActEngine` 只认 `LLMProvider` 这个接口的几个方法，不关心背后具体在跟 Anthropic 还是 OpenAI-compatible 的端点说话——`AnthropicProvider`/`OpenAIProvider`（后者同时覆盖 DeepSeek 等任何 OpenAI 兼容端点）只是这个接口的两个具体实现，想加第三个厂商，只需要再写一个实现类，不用动 core 或任何工具代码。
- **`confirmation/` 把"谁来批准"这件事从"批准什么"中剥离出来**。任何工具代码只知道自己手里有一个 `ConfirmationChannel`，调它的 `confirm()`/`ask_open_question()`，完全不知道、也不需要知道背后到底是终端在等用户按 y/n，还是 WebSocket 在等浏览器那边点按钮，还是 Telegram 在等用户回一条消息。
- **`mcp_integration/` 和 `skills/` 是两条并行但不同的"给模型新增能力"的路径**，容易混淆但职责分得很清楚：`mcp_integration/` 接的是**别人已经写好、独立运行的外部程序**（通过标准的 MCP 协议，比如官方的文件系统服务器），AuraAgent 自己不写这部分代码，只负责连接和适配；`skills/` 则是**让模型在对话过程中自己生成一段新代码**（一个 `run.py`），审批通过后立即可用——前者是"接入已有的轮子"，后者是"当场造一个新轮子"，风险模型也完全不同（见 2.6 节），所以故意没有合并成一套机制。
- **`config/` 存的是"运行策略"，不是"运行数据"**：当前团队有哪些 Agent、各自能调哪些工具（`agents.json`）、接了哪些 MCP 服务器（`mcp_servers.json`）、各种可调参数（`settings.py` 读取的 `.env`）——这些都在版本控制之内（除了 `.env` 本身），因为它们描述的是"这个系统应该怎么运作"，不是运作过程中产生的内容。

### 4. 非代码目录的作用

- **`docs/`**：面向人类读者的说明文档，不是运行时会读取的东西。`ARCHITECTURE.md`/`ARCHITECTURE-cn.md` 是按时间顺序的变更历史；这篇 `Coding101.md` 是结构化的入门讲解；根目录的 `README.md`/`README-cn.md` 是给"还没决定要不要用这个项目"的人看的、最轻量的介绍和启动步骤。三者面向不同的读者意图，不是同一份内容的三种重复。
- **`sandbox/`**（gitignored）：运行时产生/积累的实际数据——本地 JSON 日历/任务文件、笔记、记忆、下载的文件、Google OAuth 的 token。这是运行时状态，不该进版本控制，也正因为"策略在 `config/`、数据在 `sandbox/`"分得很干净，像 Project 功能才能做到"项目的真实内容目录"和"AuraAgent 自己关于这个项目的摘要元数据"分开存放而不互相污染。
- **`skills_store/`**：动态生成或安装的 Skill 实际代码存放地（每个子目录一个 `SKILL.md` + `run.py`）。概念上它也是一种运行时产生的内容，但因为涉及"模型生成的代码会被执行"，把它从 `sandbox/` 里单独拎出来命名，是为了让人一眼看出这个目录的内容需要被认真审查过，不是普通数据。
- **`tests/`**：一个测试文件对应一个被测模块（`tests/test_xxx.py` ↔ 某个 `xxx.py`），用 `pytest`、asyncio 模式是 `auto`（不需要手写 `@pytest.mark.asyncio`）。测试覆盖度和是否要跑全量按改动的实际风险来定，不是逢改必跑全量——详见 `CLAUDE.md` 的 Testing 一节。
- **`logs/`**（gitignored）：`AuraLogger` 实际写出的 `session-*.jsonl` 文件，每一条记录一轮对话里的 Thought/Tool Call/Observation/Confirmation，是前面提到的"白盒"承诺的落地证据——出问题时直接去这里看模型当时在想什么，不需要靠框架自带的黑箱调试工具。

---

## 第二部分：关键文件详解

### 5. 核心引擎与双前端桥梁

这一组是整个项目的"骨架"——后面讲的多代理编排、GUI、MCP、Skills 全都搭在这几个文件之上，先把这几个理解透，后面的章节才有地方挂。

#### 5.1 `core/react_engine.py` —— ReAct 循环本体

**解决什么问题**：把"反复问模型下一步做什么、执行它要求的工具、把结果喂回去，直到给出最终答案"这个循环，写成一个不依赖任何具体前端、LLM 厂商、或"Multi-Agent 存在"这个概念的最小实现。

**核心抽象**：
```python
class AsyncReActEngine:
    def __init__(self, provider, registry, logger, system_prompt, max_turns=15, agent_name="root"): ...
    async def run(self, user_input: str, history: list[ConversationTurn] | None = None) -> str: ...
```

**几个值得记住的设计点**：
- 模块自己的 docstring 就声明了"故意不 import `providers/`、`tools/` 的具体工具模块、`confirmation/`、`agents/`"——只认抽象的 `LLMProvider` 和 `ToolRegistry` 对外暴露的 `get_tool_specs()`/`dispatch()` 这两个方法（`agents/scoped_tool_registry.py::ScopedToolRegistryView` 结构上满足同样的形状，所以也能直接塞进来用，引擎完全不知道两者的区别）。
- `agent_name` 只是一个用于日志的显示标签——引擎对"Multi-Agent 这件事存在"完全零感知，Leader 和每个 Worker 都只是这同一个类的不同实例，区别只在于构造时传入的 `system_prompt`/工具视图/名字不同。
- `history` 参数是整个"记忆"机制的关键：传 `None`（默认）得到一次性、无状态的调用，调用结束后历史被丢弃——`agents/delegate_tool.py` 的 Worker 调用故意一直这样用，因为多个 `delegate_to_<worker>` 可能在同一轮里并发执行，共享同一个预构建的 Worker 引擎实例，必须保证各自互不干扰；传入同一个 list 并在下一次调用时传回去，就能让对话记忆持续累积（CLI 的 REPL、GUI 每个 WebSocket 连接都是这么给 Leader 用的）。
- 一轮里模型请求的多个工具调用是**并发**执行的（`asyncio.gather(..., return_exceptions=True)`），不是顺序 `for` 循环——`return_exceptions=True` 不是可选的细节：没有它，任何一个工具调用抛出未捕获异常都会取消其它正在并发执行的工具调用，破坏"一个工具失败不连累其它"这条既有保证。
- 只捕获 `ToolExecutionError`（转成一条 `is_error` 的 Observation 喂回模型，循环继续），其余任何异常都直接扬出去让这次 `run()` 调用崩溃——这是贯穿整个项目的"白盒学习"哲学：宁可看见一次清晰的崩溃，也不要悄悄吞掉一个意料之外的 bug。

**协作者**：`core/message_types.py`（数据结构）、`core/logger.py`（事件记录）、`core/exceptions.py`、`providers/base.py`、`tools/registry.py`。

#### 5.2 `core/bootstrap.py` —— 唯一的组合根

**解决什么问题**：CLI、GUI、Telegram 三个前端都要组装同一套"工具 + Agent 编制 + Skill + 六个 self-extension 工具"的完整体系，怎么保证三者装出来的东西完全一样，而不是三份各自维护、注定会慢慢跑偏的拼装代码？

**解法**：一个 `async def build_app_context(settings, confirmation_channel, logger) -> AppContext`，三个前端都调用这同一个函数。调用方只需要自己先构造两样真正"因前端而异"的东西，再传进去：
- `logger`：CLI 用 `AuraLogger([TerminalSink(), JSONLSink(dir)])`；GUI/Telegram 换成各自的 `WebSocketSink`/`TelegramLogSink` 代替 `TerminalSink`。
- `confirmation_channel`：CLI 用 `TerminalConfirmationChannel`，GUI 用 `WebSocketConfirmationChannel`，Telegram 用 `TelegramConfirmationChannel`——接口（`confirmation/base.py`）本身从不改变，只是换一个实现。

除此之外，工具注册表、Agent 编制、六个 self-extension 工具——完全相同，因为字面上就是同一段代码在构造同一批对象。

**`AppContext` 和 `CLIContext` 的关系**：`AppContext`（`core/bootstrap.py` 定义）是 `build_app_context()` 返回给调用者的顶层对象（`provider`/`registry`/`agent_registry`/`orchestrator`/...），里面嵌了一个 `cli_context: CLIContext` 字段。`CLIContext`（`cli/context.py` 定义）专门给 `cli/commands.py` 的 "/" 命令层用——两者字段有一部分重叠（比如 `agents_config_lock`、`known_api_keys`），是因为 "/" 命令层需要的东西比三个前端共有的核心集合还要多一点（比如 `quick_notes_subdir` 这个纯 CLI 运行时字段，刻意只放在 `cli_context` 上，不进 `AppContext` 本体——`register_quick_note_tool` 通过一个零参 lambda `lambda: cli_context.quick_notes_subdir` 读它，呼应 2.7 节）。GUI/Telegram 两个前端都通过 `ctx.cli_context` 去复用同一套 "/" 命令背后对应的 `cli/service.py` 函数。

**`aclose()`**：对称于 CLI 原本 `main.py` 里的 `finally` 收尾块（取消 `scheduler_task`、关掉 `http_client`、关掉 `mcp_manager`），封装成一个方法让 GUI/Telegram 的关闭路径不需要自己记住这两步分别要做什么。

**协作者**：几乎每一个 `tools/*` 子包、`agents/`、`confirmation/`、`providers/`、`skills/`、`mcp_integration/`、`cli/context.py`——理解这个文件最好的方式不是通读全部 28KB，而是把它当成一张"这个系统到底由哪些零件拼起来"的清单去对照着看。

#### 5.3 `core/logger.py` —— `LogSink` 扇出抽象

**解决什么问题**："白盒"承诺（每一步 Thought/Tool Call/Observation 都要能被看到）同时需要终端打印、持久化 JSONL、以及 GUI/Telegram 的实时推送——这三种目的地的写法天差地别（同步 `print`、同步文件写、需要异步排队转发），怎么让 `AuraLogger` 自己完全不关心"现在是谁在消费这些事件"？

**核心抽象**：
```python
class LogSink(ABC):
    @abstractmethod
    def write(self, event: dict[str, Any]) -> None: ...
```
`AuraLogger._emit()` 把每一步构造成一个小 dict（`ts`/`turn`/`agent_name`/`event_type`/`payload`），广播给一个 `sink` 列表。`TerminalSink` 用 `rich` 渲染彩色面板；`JSONLSink` 直接同步写一行 JSON（由于整个写入过程里没有 `await`，在 asyncio 的协作式调度下这段代码天然是一个原子的临界区，不需要额外加锁）；GUI 的 `WebSocketSink`（`gui/ws_log_sink.py`）则用 `asyncio.Queue` 先缓冲再由自己的 task 异步 drain——因为规则写得很明确：**`write()` 永远不能是协程、不能 `await`**，这保证了 `core/react_engine.py` 可以放心地同步调用每一个 `log_*()` 方法，完全不用关心背后有没有一个 GUI 在异步转发。

**一个具体的例子**：`log_schedule_result()` ——一个定时任务跑完的结果不属于"当前这一个连接"，而是要广播给所有已登录设备；`WebSocketSink` 专门认出这一个 `event_type` 做"广播而不是单播"的特殊处理，`TerminalSink` 完全不认识这个事件类型，直接忽略——同一套 `LogSink` 接口下，不同的具体实现可以选择性地只关心自己在乎的事件类型，互相不需要知道对方的存在。

**协作者**：`core/react_engine.py`（唯一的事件产生方）、`gui/ws_log_sink.py`、`tg_bot/sink.py`。

#### 5.4 `core/message_types.py` / `core/exceptions.py` —— 厂商无关的数据与错误类型

`message_types.py` 里的 `ToolCallRequest`/`ToolResultInput`/`ConversationTurn`/`LLMResponse` 这几个 dataclass 存在的唯一理由，是让 `core/react_engine.py` 永远不需要知道 Anthropic 的 `tool_use`/`tool_result` content block 长什么样，或者 OpenAI 的 `function_call` 格式长什么样——每个具体 Provider 在自己的边界内做双向翻译。`ConversationTurn` 有个值得记住的细节：正常的 `role="assistant"` 回合应该带 `raw`（厂商原始消息，用于同厂商内部的历史回放），但一个"从 JSONL 恢复的历史会话"（比如 GUI 重新打开一个老对话）是没有 `raw` 的，只有重建出来的 `text`——两个 Provider 的 `_translate_history()` 都必须兼容 `raw is None` 这种情况，退化成最朴素的 `{"role": "assistant", "content": text}`。

`exceptions.py` 是一个很浅的异常体系，核心规则写在模块自己的 docstring 里：`core/react_engine.py` 只捕获 `ToolExecutionError`（转成一条 `is_error` 的 Observation 喂回模型，循环继续），其余异常类型全部放任扬出去让当前 `run()` 调用崩溃——悄悄吞掉一个意料之外的错误，在"白盒学习"这个项目定位下等于隐藏了一个真实的 bug。`SandboxPathError`/`MaxTurnsExceededError`/`ConfirmationDeniedError`/`LLMOutputTruncatedError` 分别对应各自的具体场景，而不是都笼统地抛一个 `Exception`。

#### 5.5 `cli/service.py` —— CLI/GUI 复用的核心函数层

**解决什么问题**：见 2.4 节——同一个功能既要给 "/" 命令用，也要给 REST 端点用，逻辑只能写一份。

**实际长什么样**：一长串纯函数/`async` 函数，每个都接收 `ctx: CLIContext`（有时还带具体参数），返回一个小的 `@dataclass` 状态对象（`ConfigStatus`/`WorkspaceStatus`/`CalendarStatus`/`TaskBackendStatus`/`ProjectStatus`/...）或者直接执行一个动作、抛一个异常——没有一处调用 `input()`/`print()`/`getpass()`。

**一个值得单独讲的例子**：`sync_active_directory(ctx, *, project_slug, directory_override=None)`——这是这一层里最重要的函数之一，在每一轮对话开始前都会被重新调用一次，按"Project 目录 > GUI 某个 Chat 自己的目录覆盖 > 持久化的默认 workspace"这个优先级重新**算出**这一轮该用哪个目录，而不是把目录当成一个到处被直接改写的全局变量。这是为了解决一个真实出现过的问题——`/workspace set` 和 `/project use`/`/project none` 曾经各自直接改写同一个共享的 `workspace_root`，互相毫无感知，导致"界面上显示 Project 还在生效，但文件工具实际已经写到别的地方去了"这种真实的用户可见的不一致（具体的问题现场和修复过程见 `ARCHITECTURE.md` 的 N13 条目）。

**协作者**：几乎所有 `tools/*` 子包的 store/provider、`cli/context.py` 的 `CLIContext`，被 `cli/commands.py` 和 `gui/routes.py` 两边同时调用。

#### 5.6 `cli/commands.py` —— `/` 命令分发层

**解决什么问题**：终端里打一行 `/project use ai_governance`，怎么从一个纯字符串变成对 `cli/service.py` 里具体函数的调用，并把结果或错误用人能读的格式打印出来？

**核心结构**：`is_command(line)` 判断一行是不是以 `/` 开头；`dispatch_command(line, ctx)` 解析出命令名分发给对应的 `_cmd_xxx`；每个 `_cmd_xxx` handler 只做"解析参数 → 调 `cli/service.py` 里的函数 → 打印结果或捕获异常打印错误消息"，本身不包含真正的业务逻辑。

**一个容易踩的细节**：`dispatch_command` 本身是 `async def`，但不是所有 `_cmd_xxx` 都需要跟着是 `async`——`_cmd_calendar` 是普通 `def`（因为它背后调用的 `cli/service.py` 函数全是同步的），而 `_cmd_tasks` 必须是 `async def`（因为 `finish_google_tasks_connect` 需要 `await` 一次真实的 HTTP 调用，去查找/创建名叫 "AuraAgent" 的那个 Google Tasks 列表）。给一个新命令判断要不要写成 `async def` 时，要看它背后调的 `cli/service.py` 函数本身是不是协程，不能直接照抄旁边某个命令的写法。

**"/" 命令和 self-extension 工具的关系**：两者是同一组底层能力（装 Skill、招 Worker……）的两条不同入口——一条由人类直接触发（跳过审批环节，因为人类自己敲命令这件事本身就是审批），一条由模型提议、经人类审批后触发——复用同一套构造/持久化辅助函数（`agents/agent_builder.py`、`agents/agent_config_writer.py`），保证两条路径不会悄悄长出不一致的行为。

#### 5.7 `providers/base.py` 与 `providers/swappable_provider.py` —— LLM 厂商隔离层

`providers/base.py` 定义的 `LLMProvider` 只有一个抽象方法 `async def send(system_prompt, history, tool_specs) -> LLMResponse`，外加一个 `model_name` 属性。接口要求 Provider 是**无状态**的——每次 `send()` 都要从传入的 `history` 重新构建自己的原生消息格式，这样同一个 Provider 实例才能安全地被多个并发的 `AsyncReActEngine.run()` 调用复用，不会因为内部状态互相污染。

`providers/swappable_provider.py` 是 2.1 节讲的"可热切换指针"模式**第一次**出现的地方——每一个引擎（Leader、每一个 Worker、以及未来被 `propose_new_agent`/`/agents add` 建出来的新 Worker）拿到的都是**同一个** `SwappableProvider` 实例，而不是某个具体的 `AnthropicProvider`/`OpenAIProvider`。`/config use` 切换厂商时只需要调这一个对象的 `set_current(new_provider, name)`，所有已经构造好、甚至还没构造出来的引擎，下一次调用就会自动用上新的 Provider——完全不需要去遍历"系统里现在到底有哪些引擎实例"这件本身就很难枚举全的事。`core/react_engine.py` 不需要对这个包装类做任何特殊处理：它只访问 `self.provider.model_name` 和 `await self.provider.send(...)`，`SwappableProvider` 把这两者原样转发给当前持有的具体 Provider，对调用方完全透明。

（`AnthropicProvider`/`OpenAIProvider` 这两个具体实现各自在自己的边界内做消息格式翻译——`OpenAIProvider` 还额外处理了一个真实踩过的坑：一次请求较大内容的工具调用在 JSON 参数还没生成完时就撞到 `max_tokens` 上限，截断的 JSON 直接喂给 `json.loads()` 会抛出一个对人类毫无意义的 stdlib 错误，`OpenAIProvider.send()` 捕获这种情况并重新包装成 `LLMOutputTruncatedError`，给出"是哪个工具、大概是什么原因、怎么解决"的清晰提示——这类"绝不让一个裸异常直接抛给用户看"的翻译，具体历史见 `ARCHITECTURE.md`。）

---

### 6. 多代理编排与工具/安全边界

#### 6.1 `agents/agent_registry.py` —— 团队编制的加载与运行时增减

**解决什么问题**：`config/agents.json` 声明的 Agent 名单需要在启动时就被校验（必须正好有一个 Leader，不能有重名），同时还要支持运行时增减 Worker（`propose_new_agent` 审批通过后，或 `/agents add`/`/agents remove`）而不需要重启进程。

**核心**：`AgentRegistry.load(path)` 从 JSON 构建整个名单，采用 fail-fast 风格——这跟 `ToolRegistry.register()`、`config/settings.py::load_settings()` 是同一套"宁可现在就崩溃，也不要晚点才暴露一个隐藏的配置错误"的惯例，刻意跟 MCP 服务器/Skill 加载时"某一项失败就跳过它，不影响其它"的 best-effort 风格区分开——因为一份损坏的 Agent 名单本身就是一个用户应该立刻修复的配置错误，而一个连不上的 MCP 服务器只是失去一个可选功能。`add_worker()`/`remove_worker()` 支持运行时修改内存中的名单，且 Leader 永远不能被移除——保证"正好一个 Leader"这个不变量不会在运行时被破坏。

**协作者**：`agents/agent_definition.py`（`AgentDefinition` 数据结构 + 自身字段校验）、`agents/agent_builder.py`（真正把一个 `AgentDefinition` 构造成可用引擎的工厂函数）、`agents/agent_config_writer.py`（把运行时的增减持久化回 `config/agents.json`）。

#### 6.2 `agents/scoped_tool_registry.py` —— 按 capabilities 过滤的"视图"

**解决什么问题**：所有工具共享同一个 `ToolRegistry`，但每个 Agent（Leader、每个 Worker）只应该**看到**、只应该**能调**自己 `capabilities` 允许的那个子集——"看到"和"能调"是两件不同的事：只做前者（过滤模型看到的工具列表）并不是一个真正的安全边界，因为一次模型幻觉、或者刻意构造的 `tool_use` 调用完全可以直接点名一个真实存在、但超出授权范围的工具名。

**核心**：`ScopedToolRegistryView` 同时做两件事——`get_tool_specs()` 只返回名字匹配 `allowed_patterns`（`fnmatch` 通配符）的工具规格给模型看；`dispatch()` 在真正转发给底层共享 `ToolRegistry` **之前，再检查一次**同一份白名单。少了第二次检查，`capabilities` 就只是"写在 prompt 里的建议"，不是真正的限制。

**`extra_tools`**：Leader 专属的 `delegate_to_<worker>` 工具就活在这里，从来不进共享的 `ToolRegistry`——构造 Worker 的视图时 `extra_tools` 永远是 `None`，所以"委派给另一个 Worker"这件事对 Worker 来说不是"没被授权调用"，而是结构上根本不存在，连名字都看不到。

**`_dynamic_patterns`**：一组独立于 `_allowed_patterns` 的、**可整体替换**（不像 `add_allowed_pattern()` 那样只能单向追加永久授权）的第二份白名单，专门给 Project 的"额外启用工具"用——进入一个 Project 时调用 `set_dynamic_patterns(project的enabled_tools)`，退出时清空成 `[]`，不需要为每一次临时授权单独写一条"撤销"逻辑，因为整个列表本身就是一次性整体换掉的。

**协作者**：`tools/registry.py`（被包装的共享注册表）、`agents/delegate_tool.py`、`tools/self_extend/`（`propose_new_skill` 等审批通过后会在运行时调用 `add_allowed_pattern()`/`add_extra_tool()` 动态扩权）。

#### 6.3 `agents/leader_worker_orchestrator.py` 与 `agents/delegate_tool.py` —— worker-as-tool

`leader_worker_orchestrator.py` 本身薄得几乎没有自己的逻辑——`OrchestrationMode` 这个接口目前只有它一个真正的实现（`SequentialPipelineOrchestrator`/`DebateOrchestrator` 是预留的占位，接口形状已经对齐但尚未真正实现编排逻辑），`run()` 就是直接把调用转发给预先构造好的 Leader 引擎。真正的机制（并发委派、按 `capabilities` 过滤）全部发生在别处，这个类存在的唯一理由是让"编排模式"这个概念本身可以被替换，而不是把 Leader-Worker 这一种编排方式硬编码进调用方。

`delegate_tool.py::build_delegate_tool()` 把一个**已经构造好**的 Worker `AsyncReActEngine` 包装成一个普通的 `(ToolSpec, handler)` 对，让 Leader 能像调 `calculate`/`fetch_url` 一样调用它——从 `core/react_engine.py` 的视角看，委派给一个 Worker 和调用任何别的工具没有任何区别。Worker 引擎是 `core/bootstrap.py` 启动时**构造一次**，之后每一次委派调用都复用同一个实例；`handler` 调 `worker_engine.run(args["task"])` 时**不传 `history`**，这保证了并发的多个 `delegate_to_<worker>` 调用（哪怕是对不同 Worker 的调用）能安全地共享同一批预构建引擎实例、互不干扰——这不是偶然安全，而是 5.1 节讲到的"`history=None` 时每次都会得到一个全新空列表"这条设计的直接应用。

**协作者**：`core/react_engine.py`（并发 dispatch 能力的来源）、`agents/scoped_tool_registry.py`（`delegate_to_*` 工具实际被塞进 Leader 视图的 `extra_tools` 里，而不是共享注册表）。

#### 6.4 `tools/registry.py`、`tools/base.py`、`tools/sandbox_path.py` —— 共享工具表与唯一的文件系统边界

**`registry.py`**：原生工具、MCP 适配后的工具、动态加载的 Skill——三条完全不同的来源，**最终都调用同一个 `register(spec, handler)` 方法**汇入这一个 `ToolRegistry`。`dispatch()` 里有一处值得记住的异常转译：工具 handler 抛出的 `ToolExecutionError` 原样向上传（这是预期内的、该让模型看到的失败），但任何其它未预期的异常都会被包一层转成 `ToolExecutionError` 再抛出——因为工具 handler 本身被当作"不可信代码"对待（没人能保证每一个工具作者都记得妥善处理每一种异常），这一层兜底保证一个写得不严谨的工具不会直接让整个 ReAct 循环崩溃，而是转成一条正常的失败 Observation，模型可以借此继续推理、换个办法再试。

**`base.py`**：`ToolSpec(name, description, input_schema)`——`input_schema` 是纯 JSON Schema，这不是巧合，而是因为 Anthropic 的 `tools=[...]` 参数和 OpenAI 的 `function.parameters` 刚好都认这个格式，同一份 `ToolSpec` 列表可以被任何 `LLMProvider` 直接拿去翻译成自己厂商的调用格式，不需要 `tools/` 这一层关心当前连的是哪个厂商。

**`sandbox_path.py::resolve_within_sandbox(root, relative_path)`**：整个项目里**唯一一处**做"路径是否逃出 sandbox"判断的函数。有个具体的坑值得记住：**检查绝对路径必须在拼接之前做，不能拼接之后再查**——因为 `pathlib` 的 `/` 运算符遇到右边是绝对路径的情况时，语义是整体替换掉左边（`Path(root) / "/etc/passwd"` 直接得到 `/etc/passwd`，`root` 被完全丢弃），如果检查顺序反了，这个边界形同虚设。这个函数本来住在 `tools/notes/path_guard.py`，后来 `tools/files/` 也需要一模一样的逻辑，才搬到 `tools/` 这一级给所有工具共享——这是"发现第二个消费者就该把逻辑提升到共享层"的一个具体例子。

**协作者**：几乎每一个涉及文件路径的 `tools/*` 子包。

#### 6.5 `confirmation/base.py` 与 `confirmation/swappable_channel.py` —— HITL 接口与它的热切换外壳

**`base.py`**：`ConfirmationChannel` 只有两个抽象方法——`confirm(request) -> bool`（是否批准）和 `ask_open_question(prompt) -> str`（开放式问答）。两者被放进同一个接口，而不是两个并行接口，是因为它们本质上是"同一个物理通道，两种不同的响应形状"——不管背后是终端在等 y/n、WebSocket 在等浏览器点击、还是 Telegram 在等一条文字回复，对工具代码而言都只是在问人类一个问题，区别只在于问题的形式。`core/react_engine.py` 从不 `import` 这个模块——HITL 完全被封装在需要它的工具 handler 内部，引擎的职责永远只是"循环 / 调模型 / 派发工具 / 记日志"，不掺一点 HITL 逻辑进去。

**`swappable_channel.py`**：又是 2.1 节"可热切换指针"模式的一个实例（第四个），但这次热切换的目的不是"让用户手动选一个新实现"，而是给 `tools/scheduler/scheduler_loop.py` 用——一次无人值守的定时任务运行期间，需要把真正会打断人类的确认通道**临时**换成一个"自动全部拒绝"的 `AutoDeclineConfirmationChannel`，跑完之后再换回真实的通道，而不需要把每一个已经注册好、挂着真实 `ConfirmationChannel` 引用的工具重新注册一遍。这种"临时替身"的安全性建立在一个前提上：这次切换永远发生在 `AppContext.run_lock` 持有期间——跟任何一次实时的 CLI/GUI 对话轮次用的是同一把锁，所以不存在"一次真实的人类确认"和"一次自动拒绝的定时任务"同时在飞的情况。

**协作者**：几乎所有带 `risk_level` 的工具（`delete_file`/`kill_process`/`propose_*`/日历任务工具的部分操作）、三个前端各自的具体实现（`TerminalConfirmationChannel`/`WebSocketConfirmationChannel`/`TelegramConfirmationChannel`）、`tools/scheduler/scheduler_loop.py`。

---

### 7. GUI 后端、MCP 集成、Skills、配置

#### 7.1 `gui/server.py` —— FastAPI 后端 + WebSocket 传输

和 `core/bootstrap.py` 的关系：调用同一个 `build_app_context()`，只换掉 `logger`/`confirmation_channel` 的具体实现（`WebSocketSink`+`WebSocketConfirmationChannel` 代替 CLI 的 `TerminalSink`+`TerminalConfirmationChannel`）。

**单进程单 `AppContext` 模型**：跟 `main.py` 一样全程只有一个共享的 `AppContext`，但支持多个 WebSocket 连接同时存在（桌面 GUI + 一台连接的 Auralis 手机，或两个浏览器标签页）——每个连接有自己的 `WebSocketSink` 队列（靠 `connection_id` 区分）、自己的 `active_session`，但同一时刻真正在跑一轮对话的只能有一个：`ctx.run_lock` 把所有真正触碰共享 `workspace_root`/`active_project`/Leader 状态的操作（一轮对话本身、连接时的同步、切换 chat/session）全部序列化，GUI、CLI、后台 scheduler 用的是同一把锁，谁都不特殊。

**会话持久化**：每个连接有一个"active session"，`SessionSink` 把每个事件实时写进该 session 自己的文件，所以一段对话能跨进程重启存活，不只是当前连接生命周期内的内存记忆——真正喂给 Leader 引擎的 `history` 是从 session 持久化的事件**重建**出来的（`cli/service.py::history_from_events()`，同一个函数也被 `telegram_bot.py` 复用，不让两个前端各写一份重建逻辑）。

**鉴权**：`AURA_REQUIRE_AUTH` 打开时，每个 REST 端点都要求一个有效的设备 Bearer token，`/ws` 连接在 `accept()` **之前**就校验 `?token=` 查询参数，一个未授权的 socket 连都不会被接受——这是让这个进程能安全放在 Cloudflare Tunnel 后面、给一台真实手机远程访问的前提。

#### 7.2 `gui/ws_log_sink.py` 与 `gui/ws_channel.py` —— 把白盒事件流和 HITL 请求揍进同一根 WebSocket

**`WebSocketSink`**：每个连接自己一条 `asyncio.Queue`（不是共享一条）——同时存在多个连接时，一条共享队列会导致"谁先调用 `.get()` 就抢到本该发给别的连接的事件"这种错乱；`current` 属性标记"此刻该往哪个连接的队列写"，`gui/server.py` 在任何会产生事件的动作之前（一条新消息、切换 session）都在 `run_lock` 保护下先设置好 `current`，所以两个连接的动作永远不会被错发到对方的队列里。`write()` 规则跟 `core/logger.py` 一样——绝不能 `await`，真正的网络发送发生在 `drain()` 里，由 `gui/server.py` 给每个连接开一个背景 task 持续跑。

**`broadcast()`**：唯一的例外——一小部分事件类型（目前只有 `schedule_result`，定时任务跑完的结果）不属于"当前这一个连接的实时对话"，而是每台在线设备都该看到的后台事件，`write()` 特殊处理这几个类型，发给所有已注册的队列而不是只发给 `current` 那一个。

**`WebSocketConfirmationChannel`**：跟 CLI 的 `TerminalConfirmationChannel` 是同一个接口的另一个实现，区别只在于"怎么问人"。`confirmation_request`/`open_question_request` 不是另开一条通道，而是直接写进 `WebSocketSink` 那同一条队列——对 GUI 客户端而言，白盒事件和"需要你输入"的请求是同一根有序事件流上的不同事件类型，不是两条分开接线的通道。人类的回答通过 WebSocket 自己的接收循环（`gui/server.py`）路由回这里的 `resolve(request_id, value)`，是经典的"用 `request_id` 做键的 pending Futures 字典"模式，把一个基于单条双工连接的异步请求/响应协议，变成 `confirm()`/`ask_open_question()` 里一句简单的 `await`。

一个安全细节：`ask_open_question()` 故意**不记日志**——这是 `propose_mcp_server` 用来收集环境变量密钥值的同一条通道，这些值绝不能进 `logs/session-*.jsonl` 或 GUI 自己的事件流，跟 `confirmation/terminal_channel.py` 同名方法的考虑完全一致。

#### 7.3 `gui/routes.py` —— REST 端点

**跟 `cli/service.py` 的关系**：每一个 handler 都是 `cli/service.py` 之上薄薄的 HTTP 适配——跟 `cli/commands.py` 的 "/" 命令 handler 调的是**同一批函数**，一个面板动作和它对应的 "/" 命令必然做同一件事，这是架构上的保证，不是约定。

**读端点（GET）**：永远 live 现算，不缓存——因为背后的真实状态（`config/agents.json`、共享 `ToolRegistry`）可能在任意时刻被另一个前端、某个 self-extension 工具、或一次刚刚热加载的 Skill 改掉。

**写端点**：跳过 `propose_new_agent`/`propose_new_skill` 那种"先展示审查内容，再单独走一次批准"的两步舞——人类自己填表单点提交这件事本身就是批准，跟 "/" 命令自己最后那个 `[y/N]` 提示被定义为"防手误，不是防模型越权"是同一套道理。唯一的例外是装一个 Skill（`/api/skills/stage` 再 `/api/skills/commit` 两次独立调用），对称于 `/skills load|install` 自己的两阶段"先展示完整代码再问"的形状——因为跟新增一个 Agent/授予一个 capability 不同，一份来自外部的 Skill 代码，人类理应先真的读一遍再批准，不能只填个名字就点提交。

#### 7.4 `mcp_integration/mcp_client_manager.py` —— 外部 MCP 服务器的连接与隔离

**核心**：读 `config/mcp_servers.json`，对每个配置的服务器用官方 `mcp` SDK 通过 stdio 连接，把它暴露的工具适配进共享 `ToolRegistry`。一个连不上/初始化失败的服务器只记一条警告然后跳过——MCP 服务器是可选的、best-effort 的集成，一条写错的配置不该让 AuraAgent 本身起不来。

**`follow_active_directory`**：配置里写 `"follow_active_directory": true` 的服务器，它的 `args` 里某处会带一个占位符 `{directory}`，由 `cli/service.py::sync_active_directory()` 在目录真正发生变化时用实际路径替换掉、并重启这个服务器的子进程。这里特意没用 MCP 协议本身的"Roots"机制（让客户端动态告诉服务器它现在能访问哪些目录，不需要重启）——因为这个特性在协议规范里刚被标记 deprecated（SEP-2577）不久，在一个刚被废弃的特性上建新功能是个糟糕的赌注，所以选了"换一个 `args` 值、重启子进程"这个更朴素、但不依赖协议未来走向的办法。代价是目录真正变化时要重新起一次子进程——没变化时直接跳过重连。

**每个服务器独占一个专属、持久的 `asyncio.Task`**：这是对一个真实复现过的 bug 的修复——之前所有服务器的 connect/close/reconnect 共用一个"owner task"，而 anyio 的 cancel scope 是按**每个 Task** 一个栈来追踪的，`stdio_client()`/`ClientSession()` 各自会开自己的内部 task group（也就是自己的 cancel scope）——多个服务器的 `AsyncExitStack` 都压在同一个 task 的同一个 cancel-scope 栈上，一旦关闭顺序不是严格的"后进先出"（简单的 per-server 重连在一般情况下根本无法保证这一点），就会抛出一个关于 cancel scope 的 `RuntimeError`。用两台真实的 MCP 服务器子进程写了一个最小复现脚本确认了这个问题跟 `npx`/Windows 子进程时序无关，纯粹是"per-server 隔离要做到 per-Task，不是 per-stack"。给每个服务器一个专属 Task 彻底绕开了整个问题类别：这个服务器的 `async with AsyncExitStack()` 永远在同一个 Task 里进入和退出，一次重连不过是这个 Task 自己循环回去重新打开一次——其它服务器活在自己的 Task、自己的栈上，完全不会被牵连。

#### 7.5 `skills/skill_loader.py` —— Skill 热加载机制

**核心**：扫描 `skills_store/` 下每个含 `SKILL.md` 清单的子目录，注册成一个可调用的工具——和原生工具、MCP 工具一样，最终都汇入同一个 `ToolRegistry.register()`，模型侧完全看不出区别。

**调用约定**：每个 Skill 的 `run.py` 统一通过一个 `--args-json` 参数接收全部工具参数（打包成一个 JSON blob），而不是把每个参数映射成各自的 CLI flag——这样子进程调用代码永远是同一份，跟某个具体 Skill 的 `input_schema` 声明了什么参数完全无关。子进程用 `sys.executable`（不是裸的 `"python"`）启动，保证永远用跟父进程一样的 Python 环境，跟 `mcp_client_manager.py` 是同一处修复的同一个原因。Skill 自己的目录被设为子进程的工作目录，所以 Skill 可以用相对路径引用自己的本地文件。

**一个真实踩过的坑**：早期一个生成文档的 Skill，把用户给的 `output_path` 当作相对于**它自己的目录**（也就是上面说的 `cwd`）去解析，而不是相对于其它文件工具（`tools/files/file_tool.py`）实际用的 `workspace_root`——结果生成的文件要么其它工具根本找不到，要么路径带子目录时直接崩溃。修复方式是给每个 Skill 子进程注入一个 `AURA_WORKSPACE_ROOT` 环境变量，让 Skill 自己负责按这个环境变量解析/收口自己的输出路径——这整类问题后来也是文档生成功能从 Skill 迁移到原生 `tools/documents/` 的直接原因（原生工具同进程运行，直接共享 `workspace_root`，不需要再靠环境变量传递、靠 Skill 作者自己记得处理）。

一个"失败就跳过而不是 abort"的例子：一个解析失败或加载出错的 Skill 只记日志跳过，跟 MCP 服务器同一种"可选、best-effort"的姿态，不影响其它 Skill 或启动本身。

#### 7.6 `config/settings.py` —— pydantic-settings 与 alias 的坑

**核心**：一个 `Settings(BaseSettings)` 对象，从 `.env` 文件和环境变量读所有配置，项目里其它代码不该直接读 `os.environ`——整个应用的配置入口只有这一处。

**命名规则**：AuraAgent 自己的内部策略开关一律带 `AURA_` 前缀（比如 `AURA_LLM_PROVIDER`、`AURA_MAX_TURNS`）；外部定义的凭证名则不加前缀，原样用它们本来的名字（`ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`TELEGRAM_BOT_TOKEN`）——因为这些是调用方（Anthropic SDK 自己的惯例、BotFather 给的变量名约定）定义的外部名字，不是 AuraAgent 自造的概念。

**一个真实的坑**（项目的 `CLAUDE.md` 专门记录过）：`model_config` 里没有设 `populate_by_name=True`，所以任何带 `alias=...` 的字段，在代码里直接构造 `Settings(...)`（测试、脚本）时**必须用 alias 字符串**，不能用 Python 字段名——比如必须写 `Settings(AURA_REQUIRE_AUTH=True)`，写成 `Settings(require_auth=True)` 不会报错，只会被 `extra="ignore"` 悄悄吃掉，什么都没发生。这类"该报错却没报错"的坑最难发现——`tests/test_gui_auth.py` 的历史上真的因为这个产生过一次 silent 的测试失败。

**一个具体的历史例子**（`llm_max_output_tokens`）：`4096`（两个 SDK 自己的默认值）被证明对一次携带大量生成内容的工具调用（比如很多页的 `create_pptx`）太小——模型在这个上限处被截断，留下一段解析不出来的 JSON 参数字符串（后续的清晰报错处理见 5.4/5.7 节、`providers/openai_provider.py`），加这个设置就是为了把上限提高、并且可配置，而不是硬编码。

#### 7.7 `config/agents.json` —— 团队编制的实际样子

不是代码，是数据——但理解它的结构是理解 `agents/agent_registry.py` 怎么工作的最快方式。每一项是一个 Agent 声明：`name`/`role`（`"leader"` 恰好一个、`"worker"` 若干个）/`system_prompt`/`capabilities`（一组 `fnmatch` 通配符，比如 `"*task*"` 会匹配所有名字里带 `"task"` 的工具——这正是为什么给 Tasks 模块新增 `update_task`/`set_task_done` 两个工具时完全不需要改这个文件，已有的 `"*task*"` 通配符自动就覆盖了新工具名，这是本项目真实发生过的一次判断，不是假设）。

**和 self-extension / "/" 命令的关系**：这份文件不是手写一次就不再变的静态配置——`propose_new_agent` 审批通过、`/agents add`/`remove`、`propose_new_skill` 等运行时扩权，最终都会通过 `agents/agent_config_writer.py` 把变化写回这个文件，保证重启后依然生效。读这个文件能看到的，就是当前这一刻"谁能调什么"的真实状态，不需要去猜测运行时可能叠加了哪些临时授权（唯一的例外是 Project 的 `_dynamic_patterns`——那是故意设计成不持久化、随进出 Project 消失的，见 6.2 节）。

## 延伸阅读

- 想知道某个具体功能的历史背景、为什么这么改、过程中踩了什么坑 —— 看 [`ARCHITECTURE.md`](ARCHITECTURE.md)（中文版：[`ARCHITECTURE-cn.md`](ARCHITECTURE-cn.md)）。
- 想知道怎么把项目跑起来、有哪些可选功能 —— 看 [`README.md`](../README.md)（中文版：[`README-cn.md`](../README-cn.md)）。
